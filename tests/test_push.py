"""
Notifications push vers l'application étudiante (Firebase Cloud Messaging).

L'app ISS (`mr.iss.etudiant`) inscrit le téléphone à chaque connexion —
POST /notifications/appareils/. Jusqu'au 05/10/2026 l'adresse n'existait pas
côté ISS : 404, aucun téléphone connu, aucun push possible. Repris du SIGA-PRIVE.

Firebase n'est JAMAIS appelé ici : tout envoi réel ferait échouer le test.
"""
import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

URL = '/api/v1/notifications/appareils/'


@pytest.fixture(autouse=True)
def jamais_firebase(monkeypatch):
    from apps.notifications import push

    def interdit(*a, **k):
        raise AssertionError('appel réseau vers Firebase dans un test')

    monkeypatch.setattr(push, '_post', interdit)


@pytest.fixture
def comptes(db):
    from apps.authentication.models import CustomUser
    return [CustomUser.objects.create_user(username='etu%d' % i, email='etu%d@iss.mr' % i,
                                           password='x', role='etudiant') for i in (1, 2)]


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class TestInscription:

    def test_le_telephone_s_inscrit(self, comptes):
        from apps.notifications.models import AppareilPush
        r = client(comptes[0]).post(URL, {'jeton': 'J1', 'plateforme': 'android', 'langue': 'ar'},
                                    format='json')
        assert r.status_code == 201
        a = AppareilPush.objects.get(jeton='J1')
        assert (a.user_id, a.plateforme, a.langue) == (comptes[0].pk, 'android', 'ar')
        # À chaque connexion, l'app s'inscrit de nouveau : pas de doublon.
        assert client(comptes[0]).post(URL, {'jeton': 'J1'}, format='json').status_code == 200
        assert AppareilPush.objects.count() == 1

    def test_le_telephone_suit_le_compte_qui_s_y_connecte(self, comptes):
        from apps.notifications.models import AppareilPush
        client(comptes[0]).post(URL, {'jeton': 'J1'}, format='json')
        client(comptes[1]).post(URL, {'jeton': 'J1'}, format='json')
        assert AppareilPush.objects.get(jeton='J1').user_id == comptes[1].pk

    def test_la_deconnexion_desinscrit_son_seul_telephone(self, comptes):
        from apps.notifications.models import AppareilPush
        client(comptes[0]).post(URL, {'jeton': 'J1'}, format='json')
        assert client(comptes[1]).delete(URL, {'jeton': 'J1'}, format='json').status_code == 204
        assert AppareilPush.objects.filter(jeton='J1').exists()       # pas le sien
        client(comptes[0]).delete(URL, {'jeton': 'J1'}, format='json')
        assert not AppareilPush.objects.filter(jeton='J1').exists()

    def test_sans_jeton_ou_sans_compte(self, comptes):
        assert client(comptes[0]).post(URL, {}, format='json').status_code == 400
        assert client(comptes[0]).post(URL, {'jeton': 'x' * 513}, format='json').status_code == 400
        assert APIClient().post(URL, {'jeton': 'J1'}, format='json').status_code in (401, 403)


# ── L'envoi ──────────────────────────────────────────────────────────────────

@pytest.fixture
def firebase(monkeypatch):
    """Un Firebase simulé : configuré, et qui note ce qu'on lui envoie."""
    from apps.notifications import push
    envois = []
    monkeypatch.setattr(push, 'configure', lambda: True)
    monkeypatch.setattr(push, 'envoyer',
                        lambda jeton, titre, corps, donnees=None:
                        envois.append((jeton, titre, corps, donnees)))
    return envois


def notifier(user, titre='Emploi du temps de la semaine 1 validé', **kw):
    from apps.notifications.models import Notification
    return Notification.objects.create(destinataire=user, titre=titre, message='m',
                                       lien='/dashboard/portail/emploi', **kw)


def appareil(user, jeton='J1'):
    from apps.notifications.models import AppareilPush
    return AppareilPush.objects.create(user_id=user.pk, jeton=jeton)


class TestEnvoi:

    def test_sans_cle_firebase_rien_ne_part(self, comptes, capsys):
        from apps.notifications.models import PushEnvoye
        appareil(comptes[0])
        notifier(comptes[0])
        call_command('envoyer_push')
        assert 'Push désactivé' in capsys.readouterr().out
        assert not PushEnvoye.objects.exists()

    def test_une_notification_part_une_seule_fois(self, comptes, firebase):
        appareil(comptes[0])
        n = notifier(comptes[0])
        call_command('envoyer_push')
        call_command('envoyer_push')
        assert len(firebase) == 1
        jeton, titre, _, donnees = firebase[0]
        assert (jeton, titre) == ('J1', 'Emploi du temps de la semaine 1 validé')
        # L'app lit le lien au toucher : il ouvre l'onglet Emploi du temps.
        assert donnees['lien'] == '/dashboard/portail/emploi'
        assert donnees['notification_id'] == n.pk

    def test_chaque_telephone_du_destinataire_et_lui_seul(self, comptes, firebase):
        appareil(comptes[0], 'J1')
        appareil(comptes[0], 'J2')
        appareil(comptes[1], 'J3')
        notifier(comptes[0])
        call_command('envoyer_push')
        assert sorted(e[0] for e in firebase) == ['J1', 'J2']

    def test_une_notification_deja_lue_ne_part_pas(self, comptes, firebase):
        appareil(comptes[0])
        notifier(comptes[0], lue=True)
        call_command('envoyer_push')
        assert firebase == []

    def test_un_telephone_desinstalle_est_oublie(self, comptes, monkeypatch):
        from apps.notifications import push
        from apps.notifications.models import AppareilPush

        def perime(*a, **k):
            raise push.JetonInvalide('UNREGISTERED')

        monkeypatch.setattr(push, 'configure', lambda: True)
        monkeypatch.setattr(push, 'envoyer', perime)
        appareil(comptes[0])
        notifier(comptes[0])
        call_command('envoyer_push')
        assert not AppareilPush.objects.exists()

    def test_une_panne_reseau_sera_retentee(self, comptes, monkeypatch):
        from apps.notifications import push
        from apps.notifications.models import PushEnvoye
        essais = []

        def panne(*a, **k):
            essais.append(1)
            raise OSError('réseau')

        monkeypatch.setattr(push, 'configure', lambda: True)
        monkeypatch.setattr(push, 'envoyer', panne)
        appareil(comptes[0])
        notifier(comptes[0])
        call_command('envoyer_push')
        assert not PushEnvoye.objects.exists()      # rendue : on réessaiera
        call_command('envoyer_push')
        assert len(essais) == 2


class TestMiroir:

    def test_sur_le_miroir_le_telephone_peut_s_inscrire(self, comptes, settings):
        """Le miroir refuse toute écriture hors liste : sans l'adresse dans
        MIRROR_WRITE_ALLOWLIST, l'app recevrait un 403 en ligne."""
        settings.MIRROR_MODE = True
        r = client(comptes[0]).post(URL, {'jeton': 'J1'}, format='json')
        assert r.status_code == 201, r.content
