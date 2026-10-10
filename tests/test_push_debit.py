"""
Débit des notifications push (10/10/2026) : envois par 10 en parallèle, une
connexion HTTPS réutilisée par fil, nouvel essai sur 429 / 5xx.

Firebase n'est JAMAIS appelé : la connexion vers FCM est simulée.
"""
import threading

import pytest
from django.core.management import call_command

from apps.notifications import push


@pytest.fixture(autouse=True)
def cle_simulee(monkeypatch):
    monkeypatch.setattr(push, '_cle_gardee', lambda chemin=None: {'project_id': 'essai'})
    monkeypatch.setattr(push, '_jeton_acces', lambda chemin=None: 'jeton-oauth')
    monkeypatch.setattr(push.time, 'sleep', lambda s: None)
    monkeypatch.setattr(push, '_post', lambda *a, **k: (_ for _ in ()).throw(AssertionError('réseau')))


def test_trop_de_requetes_puis_reussite(monkeypatch):
    reponses = iter([(429, '{}', None), (503, '{}', None), (200, '{"name": "m1"}', None)])
    monkeypatch.setattr(push, '_post_fcm', lambda *a: next(reponses))
    assert push.envoyer('J', 't', 'c') == {'name': 'm1'}


def test_trop_de_requetes_trois_fois_echec_remonte(monkeypatch):
    monkeypatch.setattr(push, '_post_fcm', lambda *a: (429, 'QUOTA', None))
    with pytest.raises(RuntimeError, match='429'):
        push.envoyer('J', 't', 'c')


def test_jeton_d_appareil_perime(monkeypatch):
    monkeypatch.setattr(push, '_post_fcm', lambda *a: (404, 'UNREGISTERED', None))
    with pytest.raises(push.JetonInvalide):
        push.envoyer('J', 't', 'c')


@pytest.mark.django_db
def test_mille_telephones_en_parallele_et_jetons_perimes_oublies(monkeypatch):
    from apps.authentication.models import CustomUser
    from apps.notifications.models import AppareilPush, Notification, PushEnvoye

    users = [CustomUser.objects.create_user(username=f'e{i}', email=f'e{i}@iss.mr', password='x',
                                            role='etudiant') for i in range(40)]
    for u in users:
        AppareilPush.objects.create(user_id=u.pk, jeton=f'J{u.pk}')
        Notification.objects.create(destinataire=u, titre='Emploi validé', message='m', lien='/emploi')
    perime = f'J{users[0].pk}'
    fils, envoyes = set(), []

    def fcm(jeton, titre, corps, donnees=None, chemin=None):
        fils.add(threading.get_ident())
        if jeton == perime:
            raise push.JetonInvalide('UNREGISTERED')
        envoyes.append(jeton)

    monkeypatch.setattr(push, 'configure', lambda chemin=None: True)
    monkeypatch.setattr(push, 'envoyer', fcm)
    call_command('envoyer_push')

    assert len(envoyes) == 39
    assert len(fils) > 1                                    # en parallèle
    assert not AppareilPush.objects.filter(jeton=perime).exists()
    assert PushEnvoye.objects.count() == 40                 # chaque notification notée une fois
    call_command('envoyer_push')
    assert len(envoyes) == 39                               # rien ne repart
