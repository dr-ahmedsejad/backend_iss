"""
Ce que le journal d'audit doit retenir des événements de SÉCURITÉ.

Relevé sur `iss` le 02/10/2026 : sur 827 040 lignes, cinq actions pourtant
déclarées n'en avaient jamais produit une seule — `LOGOUT`,
`PASSWORD_CHANGED`, `PASSWORD_RESET`, `PERMISSION_DENIED`, `BULK_DELETE`. Et
`LOGIN_SUCCESS` n'en comptait que quatre, toutes venues de `/admin/`.

La cause est la même pour la connexion et la déconnexion : les récepteurs
écoutent les signaux de Django, que l'authentification par JETON ne déclenche
jamais. On voyait donc qui avait ESSAYÉ d'entrer — `authenticate()` émet bien
son signal d'échec — mais jamais qui était entré.
"""
import pytest
from rest_framework.test import APIClient

from core.models import AuditLog
from tests.factories.auth import UserFactory

URL_LOGIN = '/api/v1/auth/login/'
URL_LOGOUT = '/api/v1/auth/logout/'
URL_MDP = '/api/v1/auth/change-password/'

MDP = 'MotDePasse!2026'


# `transactional_db` et non `db` : `write_audit` diffère l'écriture à
# `transaction.on_commit`, et une transaction annulée — ce que fait `db` après
# chaque test — n'appelle jamais ces callbacks. Le journal resterait vide ici
# alors qu'il se remplit en service.
@pytest.fixture
def compte(transactional_db):
    # Le sérialiseur de connexion refuse si aucune année n'existe en base.
    from tests.factories.parametres import YearFactory
    YearFactory(annee='2026-2027')
    u = UserFactory(username='u_audit', role='scolarite')
    u.set_password(MDP)
    u.save()
    return u


def lignes(action):
    return AuditLog.objects.filter(action=action)


# ── Connexion ────────────────────────────────────────────────────────────────

class TestConnexion:

    def test_une_connexion_reussie_laisse_une_trace_NOMMEE(self, compte):
        """Le défaut mesuré : les 4 lignes existantes portaient `user=None`,
        car le contexte de requête est posé AVANT l'authentification."""
        r = APIClient().post(URL_LOGIN, {'username': 'u_audit', 'password': MDP},
                             format='json')
        assert r.status_code == 200, r.data

        trace = lignes('LOGIN_SUCCESS').order_by('-timestamp').first()
        assert trace is not None
        assert trace.user_id == compte.pk          # et non None
        assert 'u_audit' in trace.label

    def test_un_echec_reste_trace(self, compte):
        """Il l'était déjà : on ne doit pas le casser en ajoutant la réussite."""
        APIClient().post(URL_LOGIN, {'username': 'u_audit', 'password': 'faux'},
                         format='json')
        assert lignes('LOGIN_FAILED').exists()

    def test_un_echec_ne_compte_pas_pour_une_reussite(self, compte):
        APIClient().post(URL_LOGIN, {'username': 'u_audit', 'password': 'faux'},
                         format='json')
        assert not lignes('LOGIN_SUCCESS').exists()


# ── Déconnexion ──────────────────────────────────────────────────────────────

class TestDeconnexion:

    def test_une_deconnexion_laisse_une_trace(self, compte):
        c = APIClient()
        c.force_authenticate(compte)
        assert c.post(URL_LOGOUT, {}, format='json').status_code == 200

        trace = lignes('LOGOUT').first()
        assert trace is not None and trace.user_id == compte.pk


# ── Mot de passe ─────────────────────────────────────────────────────────────

class TestMotDePasse:

    def test_un_changement_est_trace_et_conserve(self, compte):
        c = APIClient()
        c.force_authenticate(compte)
        r = c.post(URL_MDP, {'old_password': MDP, 'new_password': 'AutreMdp!2026',
                             'confirm_password': 'AutreMdp!2026'}, format='json')
        assert r.status_code == 200, r.data

        trace = lignes('PASSWORD_CHANGED').first()
        assert trace is not None and trace.user_id == compte.pk
        # La purge des 90 jours ne doit pas l'emporter : c'est l'événement qu'on
        # vient chercher des mois plus tard.
        assert trace.keep_forever is True

    def test_le_mot_de_passe_n_est_JAMAIS_dans_le_journal(self, compte):
        c = APIClient()
        c.force_authenticate(compte)
        c.post(URL_MDP, {'old_password': MDP, 'new_password': 'AutreMdp!2026',
                         'confirm_password': 'AutreMdp!2026'}, format='json')
        for trace in lignes('PASSWORD_CHANGED'):
            contenu = '%s %s' % (trace.changes, trace.label)
            assert MDP not in contenu and 'AutreMdp!2026' not in contenu


# ── Accès refusé, et sa limite ───────────────────────────────────────────────

class TestAccesRefuse:

    @pytest.fixture(autouse=True)
    def _vider_le_cache(self):
        """Le verrou anti-déluge vit dans le cache : deux tests le partageraient."""
        from django.core.cache import cache
        cache.clear()
        yield
        cache.clear()

    URL_INTERDITE = '/api/v1/parametres/semaines/marquer-type/'

    def test_un_refus_est_trace(self, compte):
        c = APIClient()
        c.force_authenticate(compte)          # rôle scolarite : pas admin
        r = c.post(self.URL_INTERDITE, {}, format='json')
        assert r.status_code == 403

        trace = lignes('PERMISSION_DENIED').first()
        assert trace is not None
        assert trace.user_id == compte.pk
        assert 'marquer-type' in trace.label

    def test_le_meme_refus_repete_n_ecrit_QU_UNE_ligne(self, compte):
        """Sans cette borne, un écran qui réessaie en boucle noierait le journal
        — celui-là même qu'on vient lire après un incident."""
        c = APIClient()
        c.force_authenticate(compte)
        for _ in range(5):
            c.post(self.URL_INTERDITE, {}, format='json')
        assert lignes('PERMISSION_DENIED').count() == 1

    def test_deux_adresses_differentes_font_deux_lignes(self, compte):
        """La borne ne doit pas aveugler : ce qui compte, c'est QUOI a été tenté."""
        c = APIClient()
        c.force_authenticate(compte)
        c.post(self.URL_INTERDITE, {}, format='json')
        c.post('/api/v1/parametres/semaines/appliquer-feries-fixes/', {}, format='json')
        assert lignes('PERMISSION_DENIED').count() == 2

    def test_un_visiteur_ANONYME_n_ecrit_rien(self, transactional_db):
        """Un balayage automatisé — il y en a sur ce serveur — produirait des
        milliers de lignes sans jamais nommer personne.

        Éprouvé sur la FONCTION et non par l'API : par cette adresse, un
        visiteur non connecté reçoit 401, et le code ne passe donc jamais devant
        la garde. Un test qui s'arrêterait à l'API la croirait surveillée.
        """
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory
        from core.exceptions import _auditer_acces_refuse

        from django.core.cache import cache

        requete = RequestFactory().post(self.URL_INTERDITE)
        requete.user = AnonymousUser()
        _auditer_acces_refuse(requete, None)

        assert not lignes('PERMISSION_DENIED').exists()
        # Et la fonction sort AVANT de poser son verrou. Sans cette assertion,
        # retirer la garde ne se verrait pas : l'écriture partirait, `AuditLog`
        # refuserait un auteur anonyme, et l'exception serait avalée — aucune
        # ligne, donc aucune différence visible. Le verrou, lui, est consommé.
        assert cache.get('audit403:None:POST:%s' % self.URL_INTERDITE) is None

    def test_la_meme_fonction_ecrit_pour_un_compte_connu(self, compte):
        """Le pendant du test précédent : sans lui, supprimer toute écriture
        passerait pour un succès."""
        from django.test import RequestFactory
        from core.exceptions import _auditer_acces_refuse

        requete = RequestFactory().post(self.URL_INTERDITE)
        requete.user = compte
        _auditer_acces_refuse(requete, None)
        trace = lignes('PERMISSION_DENIED').first()
        assert trace is not None and trace.user_id == compte.pk

    def test_une_requete_permise_n_ecrit_rien(self, compte):
        c = APIClient()
        c.force_authenticate(compte)
        c.get('/api/v1/parametres/semaines/actif/')
        assert not lignes('PERMISSION_DENIED').exists()


# ── Verrouillage, déblocage, suppression en masse ────────────────────────────

class TestVerrouillage:

    def test_le_signal_d_axes_ecrit_un_verrouillage_conserve(self, compte):
        """`user_locked_out` existait et n'était connecté à rien : on voyait
        chaque échec, jamais le moment où le système bloquait."""
        from axes.signals import user_locked_out
        user_locked_out.send('axes', request=None, username='u_audit',
                             ip_address='10.0.0.9')

        trace = lignes('ACCOUNT_LOCKED').first()
        assert trace is not None
        assert trace.object_id == str(compte.pk)
        assert trace.keep_forever is True
        assert trace.changes['ip'] == '10.0.0.9'

    def test_un_nom_inexistant_est_garde_tel_quel(self, transactional_db):
        """C'est le cas d'une attaque par essais de noms : le nom tenté est
        précisément l'information utile."""
        from axes.signals import user_locked_out
        user_locked_out.send('axes', request=None, username='admin2',
                             ip_address='10.0.0.10')

        trace = lignes('ACCOUNT_LOCKED').first()
        assert trace.object_id == '0'
        assert trace.changes['username_tente'] == 'admin2'


class TestDeblocage:

    @pytest.fixture
    def admin(self, transactional_db):
        return UserFactory(username='u_admin_audit', role='admin', is_superuser=True)

    def test_un_deblocage_nomme_son_auteur_et_sa_cible(self, admin):
        c = APIClient()
        c.force_authenticate(admin)
        r = c.post('/api/v1/auth/users/unblock/', {'username': 'quelqu_un'}, format='json')
        assert r.status_code == 200, r.data

        trace = lignes('ACCOUNT_UNLOCKED').first()
        assert trace is not None
        assert trace.user_id == admin.pk
        assert trace.changes['username'] == 'quelqu_un'
        assert trace.keep_forever is True

    def test_debloquer_tout_laisse_aussi_une_trace(self, admin):
        c = APIClient()
        c.force_authenticate(admin)
        c.post('/api/v1/auth/users/unblock/', {'all': True}, format='json')
        assert lignes('ACCOUNT_UNLOCKED').filter(changes__tout=True).exists()


class TestSuppressionEnMasse:

    def test_vider_l_emploi_du_temps_ecrit_UNE_ligne_et_non_une_par_seance(
            self, transactional_db):
        """`delete()` déclenchait `post_delete` sur chaque ligne : vider un
        emploi du temps écrivait des centaines de traces identiques."""
        from apps.emplois.models import Emplois
        from tests.factories.parametres import InstitutionFactory
        inst = InstitutionFactory(acronyme='ISS-T', est_principale=True)
        admin = UserFactory(username='u_vide', role='admin', is_superuser=True)
        for i in range(5):
            Emplois.objects.create(annee_universitaire='2026-2027', type_semestre='I',
                                   institution=inst)
        avant = AuditLog.objects.count()

        c = APIClient()
        c.force_authenticate(admin)
        r = c.post('/api/v1/emplois/vider/',
                   {'annee_universitaire': '2026-2027', 'type_semestre': 'I'},
                   format='json')
        assert r.status_code == 200, r.data
        assert r.data['deleted'] == 5

        assert lignes('BULK_DELETE').count() == 1
        trace = lignes('BULK_DELETE').first()
        assert trace.changes['supprimees'] == 5
        assert trace.user_id == admin.pk
        # Et aucune trace individuelle : c'est tout l'objet de l'agrégation.
        assert not lignes('DELETE').filter(model_name='Emplois').exists()
