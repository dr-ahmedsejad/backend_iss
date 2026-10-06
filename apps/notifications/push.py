"""Envoi des notifications push par Firebase Cloud Messaging (API HTTP v1).

Sans bibliothèque Google : le jeton d'accès OAuth2 s'obtient en signant un JWT
(RS256) avec la clé du compte de service — PyJWT et cryptography sont déjà là
(djangorestframework-simplejwt, pyHanko).

Configuration : `settings.FIREBASE_CREDENTIALS` = chemin du fichier JSON de la
clé (SECRET, hors git). Vide → `configure()` est faux et rien n'est envoyé.

L'app enseignant est un AUTRE projet Firebase : sa clé est dans
`settings.FIREBASE_CREDENTIALS_ENSEIGNANT`. Chaque fonction prend le chemin de
la clé à utiliser (`chemin`) ; sans lui, la clé de l'app étudiante.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from django.conf import settings

logger = logging.getLogger(__name__)

_SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'
_jetons_cache = {}  # chemin de la clé → {'valeur', 'expire'}


class JetonInvalide(Exception):
    """Le jeton de l'appareil n'est plus valable (app désinstallée…) : à supprimer."""


def cle_etudiant():
    return getattr(settings, 'FIREBASE_CREDENTIALS', '')


def cle_enseignant():
    return getattr(settings, 'FIREBASE_CREDENTIALS_ENSEIGNANT', '')


def _cle(chemin=None):
    with open(chemin or cle_etudiant(), encoding='utf-8') as f:
        return json.load(f)


def configure(chemin=None):
    chemin = chemin or cle_etudiant()
    if not chemin:
        return False
    try:
        cle = _cle(chemin)
        return bool(cle.get('project_id') and cle.get('private_key') and cle.get('client_email'))
    except (OSError, ValueError):
        logger.warning('FIREBASE_CREDENTIALS illisible : %s', chemin)
        return False


def _post(url, corps, entetes):
    req = urllib.request.Request(url, data=corps, headers=entetes, method='POST')
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8') or '{}')


def _jeton_acces(chemin=None):
    """Jeton OAuth2 (1 h), mis en cache jusqu'à 5 min avant son expiration."""
    chemin = chemin or cle_etudiant()
    _jeton_cache = _jetons_cache.setdefault(chemin, {'valeur': None, 'expire': 0.0})
    if _jeton_cache['valeur'] and time.time() < _jeton_cache['expire'] - 300:
        return _jeton_cache['valeur']
    cle = _cle(chemin)
    maintenant = int(time.time())
    assertion = jwt.encode({
        'iss': cle['client_email'],
        'scope': _SCOPE,
        'aud': cle.get('token_uri', 'https://oauth2.googleapis.com/token'),
        'iat': maintenant,
        'exp': maintenant + 3600,
    }, cle['private_key'], algorithm='RS256')
    corps = urllib.parse.urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': assertion,
    }).encode()
    rep = _post(cle.get('token_uri', 'https://oauth2.googleapis.com/token'), corps,
                {'Content-Type': 'application/x-www-form-urlencoded'})
    _jeton_cache['valeur'] = rep['access_token']
    _jeton_cache['expire'] = maintenant + int(rep.get('expires_in', 3600))
    return _jeton_cache['valeur']


def envoyer(jeton_appareil, titre, corps, donnees=None, chemin=None):
    """Envoie une notification à UN appareil. Lève JetonInvalide si FCM répond
    que le jeton n'existe plus (404 / UNREGISTERED) ; les autres erreurs sont
    remontées telles quelles (réseau, quota…)."""
    projet = _cle(chemin)['project_id']
    message = {
        'message': {
            'token': jeton_appareil,
            'notification': {'title': titre[:200], 'body': corps[:1000]},
            # Valeurs en texte uniquement (exigence FCM) ; lues par l'app au toucher.
            'data': {k: str(v) for k, v in (donnees or {}).items()},
            'android': {'priority': 'high', 'notification': {'channel_id': 'siga_general'}},
        }
    }
    try:
        return _post(
            f'https://fcm.googleapis.com/v1/projects/{projet}/messages:send',
            json.dumps(message).encode('utf-8'),
            {'Authorization': f'Bearer {_jeton_acces(chemin)}', 'Content-Type': 'application/json'},
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')
        if e.code == 404 or 'UNREGISTERED' in detail or ('INVALID_ARGUMENT' in detail and 'token' in detail):
            raise JetonInvalide(detail) from e
        raise

