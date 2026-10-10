"""
GET /api/v1/portail/accueil/ — l'accueil de l'app mobile en UNE requête.

L'accueil demandait sept réponses à l'ouverture (notes, absences, fiche,
emploi du temps, non lues, dernière notification, périodes de réclamation) :
sept allers-retours sur le réseau mobile, sept passages par l'authentification
— multipliés par mille étudiants lors d'un pic. Ici, une seule requête.

Chaque partie est EXACTEMENT la réponse de sa vue habituelle (mêmes vues
appelées, même format, cache des notes et de l'emploi du temps compris) : l'app
la lit avec le même code. Une partie en échec vaut `null` sans empêcher les
autres (comme l'app le faisait déjà).

`?annee=` (année consultée) a le même sens que pour les notes et absences.
"""
import logging

from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEtudiant

logger = logging.getLogger('siga')


def _partie(nom, calcul):
    try:
        r = calcul()
        if getattr(r, 'status_code', 200) != 200:
            return None
        return r.data if hasattr(r, 'data') else r
    except Exception:
        logger.warning('portail/accueil : partie « %s » indisponible', nom, exc_info=True)
        return None


def _notifications(request):
    """Non lues et dernière notification, avec les règles de la cloche
    (sur le miroir, « lue » = lue sur le serveur de travail OU en ligne)."""
    from core.mirror import est_miroir
    from apps.notifications.serializers import NotificationSerializer
    from apps.notifications.views import NotificationViewSet

    vue = NotificationViewSet()
    vue.request = request
    qs = vue.get_queryset()
    miroir = est_miroir()
    non_lues = qs.filter(lue=False)
    if miroir:
        non_lues = non_lues.filter(lue_en_ligne=False)
    derniere = qs.order_by('-created_at').first()
    if derniere is None:
        return non_lues.count(), []
    donnees = dict(NotificationSerializer(derniere, context={'request': request}).data)
    donnees['lue'] = bool(derniere.lue or (miroir and getattr(derniere, 'lue_en_ligne', False)))
    return non_lues.count(), [donnees]


class AccueilView(APIView):
    permission_classes = [IsEtudiant]

    def get(self, request):
        from .serializers import ProfilEtudiantSerializer
        from .views import (MesAbsencesView, MesNotesView, MonEmploiView,
                            PeriodesReclamationActivesView, _get_etudiant)

        etudiant = _get_etudiant(request)
        notifs = _partie('notifications', lambda: _notifications(request)) or (None, None)
        return Response({
            'notes':         _partie('notes', lambda: MesNotesView().get(request)),
            'absences':      _partie('absences', lambda: MesAbsencesView().get(request)),
            'profil':        _partie('profil', lambda: ProfilEtudiantSerializer(
                                 etudiant, context={'request': request}).data),
            'emploi':        _partie('emploi', lambda: MonEmploiView().get(request)),
            'periodes':      _partie('periodes', lambda: PeriodesReclamationActivesView().get(request)),
            'non_lues':      notifs[0],
            'notifications': notifs[1],
        })
