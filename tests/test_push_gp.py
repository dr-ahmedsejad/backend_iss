"""
App Groupe Polytechnique (une app pour tous les établissements, un projet
Firebase commun) :
- ses téléphones (projet 'gp') reçoivent avec la clé GP, le sigle en tête du
  titre et l'établissement + le profil dans les données ;
- les anciennes apps ISS ne voient aucun changement ;
- sans session, un jeton se désinscrit (« oublier ») ou se remplace.

Firebase n'est JAMAIS appelé : push.envoyer est simulé.
"""
import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.notifications import push
from apps.notifications.models import AppareilPush, Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def envois(monkeypatch, settings):
    settings.ETABLISSEMENT_CODE, settings.ETABLISSEMENT_SIGLE = 'iss', 'ISS'
    monkeypatch.setattr(push, 'cle_enseignant', lambda: '')
    monkeypatch.setattr(push, 'cle_gp', lambda: '/secrets/gp-iss.json')
    monkeypatch.setattr(push, 'configure', lambda chemin=None: True)
    faits = []

    def envoyer(jeton, titre, corps, donnees=None, chemin=None):
        faits.append({'jeton': jeton, 'titre': titre, 'donnees': donnees, 'cle': chemin})

    monkeypatch.setattr(push, 'envoyer', envoyer)
    return faits


def _compte(username, role='etudiant'):
    from apps.authentication.models import CustomUser
    return CustomUser.objects.create_user(username=username, email=f'{username}@iss.mr',
                                          password='x', role=role)


def test_le_telephone_gp_recoit_le_sigle_et_l_etablissement(envois):
    u = _compte('etu_gp')
    AppareilPush.objects.create(user_id=u.pk, jeton='GP1', projet='gp')
    AppareilPush.objects.create(user_id=u.pk, jeton='ANCIENNE', projet='')
    Notification.objects.create(destinataire=u, titre='Résultats', message='S1', lien='/notes')
    call_command('envoyer_push')
    par_jeton = {e['jeton']: e for e in envois}
    gp, ancienne = par_jeton['GP1'], par_jeton['ANCIENNE']
    assert gp['titre'] == 'ISS — Résultats' and gp['cle'] == '/secrets/gp-iss.json'
    assert gp['donnees']['etablissement'] == 'iss' and gp['donnees']['profil'] == 'etudiant'
    assert ancienne['titre'] == 'Résultats' and 'etablissement' not in ancienne['donnees']
    assert ancienne['cle'] is None                        # clé de l'ancienne app étudiante


def test_sans_cle_gp_ses_telephones_sont_ignores(envois, monkeypatch):
    monkeypatch.setattr(push, 'cle_gp', lambda: '')
    u = _compte('etu_sans_cle')
    AppareilPush.objects.create(user_id=u.pk, jeton='GP2', projet='gp')
    Notification.objects.create(destinataire=u, titre='X', message='y')
    call_command('envoyer_push')
    assert envois == []


def test_l_inscription_retient_le_projet():
    u = _compte('etu_inscrit')
    c = APIClient()
    c.force_authenticate(u)
    assert c.post('/api/v1/notifications/appareils/', {'jeton': 'J-GP', 'projet': 'gp'},
                  format='json').status_code in (200, 201)
    assert AppareilPush.objects.get(jeton='J-GP').projet == 'gp'
    c.post('/api/v1/notifications/appareils/', {'jeton': 'J-OLD'}, format='json')
    assert AppareilPush.objects.get(jeton='J-OLD').projet == ''


def test_oublier_sans_session():
    u = _compte('etu_oubli')
    AppareilPush.objects.create(user_id=u.pk, jeton='A-OUBLIER', projet='gp')
    r = APIClient().post('/api/v1/notifications/appareils/oublier/', {'jeton': 'A-OUBLIER'}, format='json')
    assert r.status_code == 204
    assert not AppareilPush.objects.filter(jeton='A-OUBLIER').exists()
    # Jeton inconnu : même réponse, rien à apprendre.
    assert APIClient().post('/api/v1/notifications/appareils/oublier/', {'jeton': 'INCONNU'},
                            format='json').status_code == 204


def test_remplacer_sans_session():
    u = _compte('etu_remplace')
    AppareilPush.objects.create(user_id=u.pk, jeton='VIEUX', projet='gp')
    r = APIClient().post('/api/v1/notifications/appareils/remplacer/',
                         {'ancien': 'VIEUX', 'nouveau': 'NEUF'}, format='json')
    assert r.status_code == 204
    a = AppareilPush.objects.get(user_id=u.pk)
    assert (a.jeton, a.projet) == ('NEUF', 'gp')
    assert APIClient().post('/api/v1/notifications/appareils/remplacer/', {'ancien': 'X'},
                            format='json').status_code == 400
