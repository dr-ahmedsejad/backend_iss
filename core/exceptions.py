"""Custom exception handler DRF pour SIGA.

Objectifs sécurité :
1. JAMAIS retourner de stack trace, paths internes, noms de modèles ORM, ou détails BD
   à l'utilisateur final. Tout cela reste serveur-side (logs/Sentry).
2. Chaque erreur reçoit un `request_id` (UUID court) que l'utilisateur peut donner au
   support. Le support cherche ce request_id dans les logs serveur pour retrouver la
   stack trace complète.
3. Messages utilisateur traduits FR, courts, sans détails techniques.
4. Codes HTTP conservés (400/403/404/409/500…) pour compatibilité REST.

Cas spéciaux gérés :
- ProtectedError Django -> 409 "Impossible de supprimer : référencé par X"
- Throttled -> message FR avec délai
- ValidationError DRF (400) -> structure par champ préservée (sécurise car écrite par nous)
- Exceptions non gérées -> 500 avec message générique + request_id, stack trace logguée
"""
import math
import logging
import secrets

from django.db.models.deletion import ProtectedError
from rest_framework.views import exception_handler
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger('siga')


def _get_or_create_request_id() -> str:
    """Recupere le request_id pose par AuditMiddleware (X-Request-Id uuid4).
    Si non disponible (cas rare : exception avant le middleware), genere un
    fallback court (8 hex)."""
    try:
        from core.audit_context import get_request_context
        rid = get_request_context().get('request_id', '')
        if rid:
            # Coupe l'uuid4 a 8 char pour la version "user-friendly" affichee
            return rid.split('-')[0] if '-' in rid else rid[:8]
    except Exception:
        pass
    return secrets.token_hex(4)


def _safe_user_message_for_5xx() -> str:
    """Message generique 5xx — JAMAIS de details techniques."""
    return 'Une erreur interne est survenue. Veuillez réessayer ou contacter le support.'


# Combien de temps un même refus est tu avant d'être réécrit, par
# (compte, adresse, méthode). Un écran qui réessaie en boucle, ou un compte qui
# parcourt un menu interdit, écrirait sans cela des milliers de lignes
# identiques et noierait le journal — celui-là même qu'on vient lire après un
# incident.
AUDIT_403_FENETRE_SECONDES = 600


def _auditer_acces_refuse(request, view) -> None:
    """Journalise un 403, une fois par fenêtre et pour les comptes CONNUS.

    Deux bornes, et chacune a sa raison :

      * un visiteur ANONYME n'est pas journalisé. Un balayage automatisé — il y
        en a sur ce serveur — produirait des milliers de lignes sans jamais
        nommer personne. Son refus reste dans les journaux du serveur web ;
      * un même refus n'est écrit qu'UNE FOIS par fenêtre. Ce qu'on cherche
        dans un audit, c'est « ce compte a tenté d'accéder à ceci », pas le
        nombre de fois que son navigateur a réessayé.

    Ne lève jamais : un échec d'écriture du journal ne doit pas changer la
    réponse rendue à l'utilisateur.
    """
    try:
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated):
            return

        from django.core.cache import cache
        from core.audit_helpers import write_audit
        from core.models import ACTION_PERMISSION_DENIED

        chemin = (getattr(request, 'path', '') or '')[:200]
        methode = getattr(request, 'method', '') or ''
        cle = 'audit403:%s:%s:%s' % (user.pk, methode, chemin)
        # `add` ne réussit que si la clé n'existe pas : c'est le verrou.
        if not cache.add(cle, 1, AUDIT_403_FENETRE_SECONDES):
            return

        write_audit(
            action=ACTION_PERMISSION_DENIED,
            model_name=getattr(getattr(view, '__class__', None), '__name__', 'Vue'),
            object_id='0',
            changes={'endpoint': chemin, 'methode': methode},
            label='Accès refusé : %s %s' % (methode, chemin),
            # Nommé explicitement : le contexte de requête n'est renseigné que
            # si l'authentification par cookie JWT l'a synchronisé, et un refus
            # sans auteur ne sert à rien.
            user=user,
        )
    except Exception:                                   # noqa: BLE001
        logger.warning('Audit 403 non écrit', exc_info=True)


def custom_exception_handler(exc, context):
    """Handler central des exceptions DRF + Django.

    Renvoie systematiquement :
      {
        "status":     <code HTTP>,
        "error":      "<message FR>",
        "request_id": "<8 char hex>",      # toujours present pour traçabilité
        "errors":     { ... }              # uniquement pour 400 validation
      }
    """
    request_id = _get_or_create_request_id()
    view       = context.get('view')
    request    = context.get('request')
    user_id    = getattr(getattr(request, 'user', None), 'pk', None) if request else None

    # ── Cas special 1 : ProtectedError (suppression bloquee par FK) ───────────
    if isinstance(exc, ProtectedError):
        protected   = exc.protected_objects
        model_names = {obj.__class__.__name__ for obj in protected}
        # Pas de details internes - message generique
        logger.warning(
            'ProtectedError request_id=%s user=%s view=%s models=%s nb=%d',
            request_id, user_id, getattr(view, '__class__.__name__', '?'),
            ', '.join(model_names), len(protected),
        )
        return Response(
            {
                'status':     409,
                'error':      'Suppression impossible : cet enregistrement est référencé '
                              'par d\'autres données. Supprimez d\'abord les références.',
                'request_id': request_id,
            },
            status=status.HTTP_409_CONFLICT,
        )

    # ── Cas special 2 : Throttling (Axes / DRF rate limit) ───────────────────
    if isinstance(exc, Throttled):
        wait = exc.wait
        if wait is not None:
            minutes = math.ceil(wait / 60)
            if minutes >= 2:
                detail = f'Trop de tentatives. Réessayez dans {minutes} minutes.'
            else:
                seconds = math.ceil(wait)
                detail = f'Trop de tentatives. Réessayez dans {seconds} secondes.'
        else:
            detail = 'Trop de tentatives. Veuillez patienter avant de réessayer.'
        exc.detail = detail
        logger.info(
            'Throttled request_id=%s user=%s wait=%s',
            request_id, user_id, wait,
        )

    # ── Delegation au handler DRF standard ───────────────────────────────────
    response = exception_handler(exc, context)

    if response is not None:
        # Erreur "metier" connue de DRF (4xx structuree)
        code = response.status_code

        # Log de toute erreur 4xx (utile pour detecter abus, scan, brute force)
        logger.info(
            'API error %s request_id=%s user=%s view=%s',
            code, request_id, user_id,
            getattr(view, '__class__.__name__', '?') if view else '?',
        )

        if code == 400 and isinstance(response.data, dict):
            # Validation : preserver la structure par champ (les messages sont
            # ecrits par nous, donc surs - cf serializers.validate()).
            response.data = {
                'status':     400,
                'errors':     response.data,
                'request_id': request_id,
            }
        elif code >= 500:
            # 500 venant de DRF (rare, ex: NotImplementedError) - masquer
            logger.error(
                'API 5xx via DRF handler request_id=%s user=%s exc=%s',
                request_id, user_id, type(exc).__name__,
            )
            response.data = {
                'status':     code,
                'error':      _safe_user_message_for_5xx(),
                'request_id': request_id,
            }
        else:
            # 401, 403, 404, 405, 406, 409, 415, etc. - garder le message DRF
            # (ces messages sont generiques et OK pour l'utilisateur)
            if code == 403 and request is not None:
                # `PERMISSION_DENIED` etait declare dans le modele d'audit et
                # n'avait jamais produit une seule ligne : un refus ne laissait
                # de trace que dans les journaux du serveur, effaces a chaque
                # redemarrage.
                _auditer_acces_refuse(request, view)
            msg = _flatten(response.data)
            response.data = {
                'status':     code,
                'error':      msg,
                'request_id': request_id,
            }
        return response

    # ── Exception non geree par DRF (= bug applicatif) -> 500 masque ─────────
    logger.exception(
        'Unhandled exception request_id=%s user=%s view=%s exc_type=%s',
        request_id, user_id,
        getattr(view, '__class__.__name__', '?') if view else '?',
        type(exc).__name__,
        exc_info=exc,
    )
    return Response(
        {
            'status':     500,
            'error':      _safe_user_message_for_5xx(),
            'request_id': request_id,
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _flatten(data):
    """Aplatit recursivement une structure DRF (dict/list) en une chaine.
    Utilise pour 401/403/404 où on a juste un message simple a renvoyer."""
    if isinstance(data, list):
        return ' '.join(str(i) for i in data)
    if isinstance(data, dict):
        msgs = []
        for v in data.values():
            msgs.append(_flatten(v))
        return ' '.join(msgs)
    return str(data)
