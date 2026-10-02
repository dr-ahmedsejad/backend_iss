"""
La boîte de réception du miroir, par les VRAIES adresses.

Une « publication » est simulée comme elle se produit réellement : les tables
publiées sont réécrites depuis le serveur de travail (ici, on remet à la main
ce que la publication y remettrait), les tables de la boîte de réception ne
sont pas touchées.
"""
from datetime import timedelta

import pytest
from django.contrib.auth import authenticate
from django.utils import timezone

from tests._miroir_decor import api, decor, miroir  # noqa: F401

pytestmark = pytest.mark.django_db

# Le contrat de l'API, AVANT la refonte sans clé étrangère — mêmes clés.
CLES_RECLAMATION = {
    'id', 'etudiant', 'etudiant_nom', 'etudiant_matricule',
    'type_reclamation', 'statut',
    'presence', 'inscription_element', 'session_evaluation',
    'motif', 'justificatif', 'em_code', 'em_intitule',
    'reponse', 'traitee_par', 'traitee_par_nom',
    'date_soumission', 'date_traitement',
}


def deposer(decor, i=0, **donnees):
    corps = {'type_reclamation': 'note', 'motif': 'Note manquante',
             'inscription_element': decor['ies'][i].pk, **donnees}
    return api(decor['users_etu'][i]).post('/api/v1/portail/reclamations/', corps)


@pytest.fixture
def periode_ouverte(decor):
    from apps.reclamations.models import PeriodeReclamation
    return PeriodeReclamation.objects.create(
        annee_univ=decor['annee'], type_semestre='I', institution=decor['inst'],
        date_ouverture=timezone.now() - timedelta(days=1),
        date_fermeture=timezone.now() + timedelta(days=1), actif=True)


# ── Réclamation d'un étudiant ─────────────────────────────────────────────────

class TestReclamationEtudiant:

    def test_le_depot_range_des_ids_bruts_et_un_instantane(self, decor, miroir, periode_ouverte):
        from apps.reclamations.models import Reclamation
        r = deposer(decor)
        assert r.status_code == 201, r.content
        rec = Reclamation.objects.get()
        e, ie = decor['etudiants'][0], decor['ies'][0]
        assert (rec.etudiant_id, rec.inscription_element_id) == (e.pk, ie.pk)
        assert (rec.etudiant_nom, rec.etudiant_matricule) == (e.nom, e.matricule)
        assert (rec.em_id, rec.em_code, rec.em_intitule) == (decor['em_a'].pk, 'ST11', 'Probabilités')

    def test_un_identifiant_inexistant_est_refuse(self, decor, miroir, periode_ouverte):
        assert deposer(decor, inscription_element=999999).status_code == 400
        assert deposer(decor, presence=999999).status_code == 400

    def test_on_ne_reclame_pas_sur_la_note_d_un_autre(self, decor, miroir, periode_ouverte):
        assert deposer(decor, i=0, inscription_element=decor['ies'][1].pk).status_code == 403

    def test_le_contrat_json_a_exactement_les_memes_cles(self, decor, miroir, periode_ouverte):
        r = deposer(decor)
        assert set(r.data) == CLES_RECLAMATION

    def test_un_instantane_vide_se_dit_null(self, decor, miroir):
        r = api(decor['users_etu'][0]).post('/api/v1/portail/reclamations/',
                                            {'type_reclamation': 'autre', 'motif': 'Autre'})
        assert r.status_code == 201
        for cle in ('em_code', 'em_intitule', 'traitee_par', 'traitee_par_nom',
                    'presence', 'inscription_element', 'session_evaluation'):
            assert r.data[cle] is None, cle

    def test_la_reclamation_reste_lisible_apres_la_suppression_de_l_etudiant(
            self, decor, miroir, periode_ouverte):
        from apps.inscriptions.models import InscriptionAdministrative
        deposer(decor)
        # Supprimé sur le serveur de travail — inscriptions comprises —, absent
        # du miroir après publication.
        e = decor['etudiants'][0]
        for ia in InscriptionAdministrative.objects.filter(etudiant=e):
            for ip in ia.inscriptions_ped.all():
                ip.inscriptions_elements.all().delete()
                ip.delete()
            ia.delete()
        e.delete()
        r = api(decor['admin']).get('/api/v1/reclamations/')
        assert r.status_code == 200
        lignes = r.data['results'] if isinstance(r.data, dict) else r.data
        assert lignes[0]['etudiant_nom'] == 'Etudiant 23001'
        assert lignes[0]['em_code'] == 'ST11'

    def test_l_etudiant_ne_voit_que_les_siennes(self, decor, miroir, periode_ouverte):
        deposer(decor, i=0)
        deposer(decor, i=1)
        r = api(decor['users_etu'][0]).get('/api/v1/portail/reclamations/')
        assert [x['etudiant_matricule'] for x in r.data] == ['23001']

    def test_l_enseignant_ne_voit_que_celles_de_ses_elements(self, decor, miroir, periode_ouverte):
        deposer(decor)
        assert len(api(decor['ens_a']).get('/api/v1/reclamations/pour-enseignant/').data) == 1
        assert api(decor['ens_b']).get('/api/v1/reclamations/pour-enseignant/').data == []

    def test_l_enseignant_ne_traite_que_dans_son_perimetre(self, decor, miroir, periode_ouverte):
        pk = deposer(decor).data['id']
        refus = api(decor['ens_b']).post(f'/api/v1/reclamations/{pk}/traiter/',
                                         {'statut': 'rejetee', 'reponse': 'Non'})
        assert refus.status_code == 403

    def test_le_traitement_ecrit_decision_reponse_auteur_date(self, decor, miroir, periode_ouverte):
        from apps.reclamations.models import Reclamation
        pk = deposer(decor).data['id']
        r = api(decor['ens_a']).post(f'/api/v1/reclamations/{pk}/traiter/',
                                     {'statut': 'acceptee', 'reponse': 'Corrigé'})
        assert r.status_code == 200, r.content
        rec = Reclamation.objects.get(pk=pk)
        assert (rec.statut, rec.reponse, rec.traitee_par_id) == ('acceptee', 'Corrigé', decor['ens_a'].pk)
        assert rec.traitee_par_nom and rec.date_traitement is not None

    def test_un_etudiant_ne_traite_pas(self, decor, miroir, periode_ouverte):
        pk = deposer(decor).data['id']
        r = api(decor['users_etu'][0]).post(f'/api/v1/reclamations/{pk}/traiter/',
                                            {'statut': 'acceptee'})
        assert r.status_code == 403


# ── Réclamation de séance (enseignant) ────────────────────────────────────────

class TestReclamationSeance:
    URL = '/api/v1/reclamations/seances/'

    def test_l_enseignant_reclame_sur_sa_seance_avec_instantane(self, decor, miroir):
        from apps.reclamations.models import ReclamationSeance
        r = api(decor['ens_a']).post(self.URL, {'pointage': decor['pointage_a'].pk,
                                                'motif': 'Séance faite'})
        assert r.status_code == 201, r.content
        rs = ReclamationSeance.objects.get()
        assert (rs.pointage_id, rs.prof_id, rs.em_code, rs.numero_semaine) == \
               (decor['pointage_a'].pk, decor['prof_a'].pk, 'ST11', 3)

    def test_pas_sur_la_seance_d_un_autre(self, decor, miroir):
        r = api(decor['ens_a']).post(self.URL, {'pointage': decor['pointage_b'].pk, 'motif': 'x'})
        assert r.status_code == 403

    def test_une_seance_inexistante_est_refusee(self, decor, miroir):
        assert api(decor['ens_a']).post(self.URL, {'pointage': 999999, 'motif': 'x'}).status_code == 400

    def test_le_pointage_n_est_pas_touche(self, decor, miroir):
        api(decor['ens_a']).post(self.URL, {'pointage': decor['pointage_a'].pk, 'motif': 'x'})
        decor['pointage_a'].refresh_from_db()
        assert decor['pointage_a'].reclamation_statut == ''

    def test_chacun_son_perimetre(self, decor, miroir):
        api(decor['ens_a']).post(self.URL, {'pointage': decor['pointage_a'].pk, 'motif': 'x'})
        assert len(api(decor['ens_a']).get(self.URL).data) == 1
        assert api(decor['ens_b']).get(self.URL).data == []
        assert len(api(decor['it']).get(self.URL).data) == 1
        assert api(decor['de']).get(self.URL).status_code == 403

    def test_traitement_par_l_informatique_et_avertissement(self, decor, miroir):
        pk = api(decor['ens_a']).post(self.URL, {'pointage': decor['pointage_a'].pk,
                                                 'motif': 'x'}).data['id']
        assert api(decor['ens_a']).post(f'{self.URL}{pk}/traiter/', {'statut': 'acceptee'}).status_code == 403
        r = api(decor['it']).post(f'{self.URL}{pk}/traiter/', {'statut': 'acceptee', 'reponse': 'Ok'})
        assert r.status_code == 200
        assert r.data['traitee_par_nom'] is not None and 'serveur de travail' in r.data['avertissement']


# ── Saisie de notes en ligne ──────────────────────────────────────────────────

class TestSaisieEnLigne:
    URL = '/api/v1/saisie-en-ligne/'

    def corps(self, decor, **notes):
        return {'session': decor['session'].pk,
                'rows': [{'inscription_element': decor['ies'][0].pk, **notes}]}

    def test_le_brouillon_ne_cree_aucune_note_officielle(self, decor, miroir):
        from apps.evaluations.models import Note
        from apps.saisie_en_ligne.models import SaisieNoteEnLigne
        avant = Note.objects.count()
        r = api(decor['ens_a']).post(self.URL, self.corps(decor, cc='12.5', exam='14'), format='json')
        assert r.status_code == 200, r.content
        assert Note.objects.count() == avant
        s = SaisieNoteEnLigne.objects.get()
        assert (float(s.cc), s.tp, float(s.exam), s.etudiant_matricule) == (12.5, None, 14.0, '23001')

    def test_un_element_non_enseigne_est_refuse(self, decor, miroir):
        r = api(decor['ens_b']).post(self.URL, self.corps(decor, cc='10'), format='json')
        assert r.status_code == 403

    def test_une_session_close_est_refusee(self, decor, miroir):
        decor['session'].est_close = True
        decor['session'].save()
        assert api(decor['ens_a']).post(self.URL, self.corps(decor, cc='10'),
                                        format='json').status_code == 400

    def test_vider_les_notes_supprime_le_brouillon(self, decor, miroir):
        from apps.saisie_en_ligne.models import SaisieNoteEnLigne
        api(decor['ens_a']).post(self.URL, self.corps(decor, cc='10'), format='json')
        api(decor['ens_a']).post(self.URL, self.corps(decor, cc=''), format='json')
        assert SaisieNoteEnLigne.objects.count() == 0

    def test_l_export_est_reserve_a_l_administration(self, decor, miroir):
        api(decor['ens_a']).post(self.URL, self.corps(decor, cc='10'), format='json')
        assert api(decor['ens_a']).get(self.URL + 'export/').status_code == 403
        r = api(decor['it']).get(self.URL + 'export/')
        assert r.status_code == 200 and r['Content-Type'].startswith('application/vnd.openxml')

    def test_sur_le_miroir_la_saisie_officielle_est_fermee(self, decor, miroir):
        r = api(decor['ens_a']).post('/api/v1/evaluations/notes/saisir-bulk/',
                                     self.corps(decor, cc='10'), format='json')
        assert r.status_code == 403


# ── Mots de passe changés en ligne ────────────────────────────────────────────

def publier_comptes(user, *, mot_de_passe_publie=None, doit_changer=True, reinitialise=False):
    """Ce que la publication fait à la table des comptes du miroir : elle la
    réécrit depuis le serveur de travail."""
    from apps.authentication.models import CustomUser
    if mot_de_passe_publie:
        user.set_password(mot_de_passe_publie)       # date le geste (mdp_fixe_le)
    champs = {'password': user.password, 'doit_changer_mdp': doit_changer}
    if reinitialise:
        champs['mdp_fixe_le'] = timezone.now() + timedelta(seconds=1)
    CustomUser.objects.filter(pk=user.pk).update(**champs)
    user.refresh_from_db()


class TestMotsDePasse:
    NOUVEAU = 'Nouveau-mdp-456'

    def changer(self, user, ancien='Ancien-mdp-123'):
        return api(user).post('/api/v1/auth/change-password/',
                              {'old_password': ancien, 'new_password': self.NOUVEAU})

    def test_le_changement_va_dans_la_boite_pas_dans_les_comptes(self, decor, miroir):
        from apps.authentication.models import IdentifiantPortail
        u = decor['users_etu'][0]
        ancien_hash = u.password
        assert self.changer(u).status_code == 200
        u.refresh_from_db()
        assert u.password == ancien_hash
        ligne = IdentifiantPortail.objects.get(user_id=u.pk)
        assert self.NOUVEAU not in ligne.password             # une empreinte, pas le clair

    def test_le_nouveau_mot_de_passe_survit_a_la_publication(self, decor, miroir):
        u = decor['users_etu'][0]
        self.changer(u)
        publier_comptes(u)                                     # rien de nouveau côté travail
        assert authenticate(username=u.username, password=self.NOUVEAU) == u
        assert authenticate(username=u.username, password='Ancien-mdp-123') is None

    def test_par_la_vraie_adresse_de_connexion(self, decor, miroir):
        u = decor['users_etu'][0]
        self.changer(u)
        publier_comptes(u)
        c = api()
        assert c.post('/api/v1/auth/login/', {'username': u.username,
                                              'password': self.NOUVEAU}).status_code == 200
        assert c.post('/api/v1/auth/login/', {'username': u.username,
                                              'password': 'Ancien-mdp-123'}).status_code in (400, 401)

    def test_une_reinitialisation_plus_recente_l_emporte(self, decor, miroir):
        u = decor['users_etu'][0]
        self.changer(u)
        publier_comptes(u, mot_de_passe_publie='Reinit-789', reinitialise=True)
        assert authenticate(username=u.username, password='Reinit-789') == u
        assert authenticate(username=u.username, password=self.NOUVEAU) is None

    def test_un_changement_en_ligne_plus_recent_l_emporte(self, decor, miroir):
        from apps.authentication.models import CustomUser
        u = decor['users_etu'][0]
        CustomUser.objects.filter(pk=u.pk).update(mdp_fixe_le=timezone.now() - timedelta(days=3))
        u.refresh_from_db()
        self.changer(u)
        publier_comptes(u)
        assert authenticate(username=u.username, password=self.NOUVEAU) == u

    def test_un_compte_desactive_ne_se_connecte_plus(self, decor, miroir):
        from apps.authentication.models import CustomUser
        u = decor['users_etu'][0]
        self.changer(u)
        CustomUser.objects.filter(pk=u.pk).update(is_active=False)
        assert authenticate(username=u.username, password=self.NOUVEAU) is None

    def test_le_personnel_ne_change_pas_son_mot_de_passe_sur_le_miroir(self, decor, miroir):
        r = self.changer(decor['de'])
        assert r.status_code == 403 and 'serveur de travail' in r.data['detail']

    def test_l_ancien_mot_de_passe_d_un_changement_est_celui_qui_fait_foi(self, decor, miroir):
        u = decor['users_etu'][0]
        self.changer(u)
        publier_comptes(u)
        r = api(u).post('/api/v1/auth/change-password/',
                        {'old_password': 'Ancien-mdp-123', 'new_password': 'Encore-autre-1'})
        assert r.status_code == 400

    def test_premier_acces_en_ligne(self, decor, miroir):
        u = decor['users_etu'][0]
        ancien_nom = u.username
        r = api(u).post('/api/v1/auth/first-login/',
                        {'new_password': self.NOUVEAU, 'confirm_password': self.NOUVEAU})
        assert r.status_code == 200, r.content
        assert r.data['nouveau_username'] == ancien_nom          # l'identifiant ne bouge pas
        publier_comptes(u, doit_changer=True)                    # la publication remet le drapeau
        me = api(u).get('/api/v1/auth/me/')
        assert me.data['doit_changer_mdp'] is False
        assert authenticate(username=ancien_nom, password=self.NOUVEAU) == u

    def test_sur_le_serveur_de_travail_rien_ne_change(self, decor):
        from apps.authentication.models import IdentifiantPortail
        u = decor['users_etu'][0]
        assert self.changer(u).status_code == 200
        u.refresh_from_db()
        assert u.check_password(self.NOUVEAU)
        assert IdentifiantPortail.objects.count() == 0


# ── Jetons : la publication vide la liste des révoqués ────────────────────────

class TestJetons:

    def renouveler(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        c = api()
        c.cookies['refresh_token'] = str(RefreshToken.for_user(user))
        return c.post('/api/v1/auth/token/refresh/')

    def test_un_jeton_emis_avant_la_derniere_publication_est_refuse(self, decor, miroir):
        from apps.publication.models import PublicationRecue
        u = decor['users_etu'][0]
        PublicationRecue.objects.create(recue_le=timezone.now() + timedelta(seconds=5))
        assert self.renouveler(u).status_code == 401

    def test_un_jeton_emis_apres_passe(self, decor, miroir):
        from apps.publication.models import PublicationRecue
        PublicationRecue.objects.create(recue_le=timezone.now() - timedelta(hours=1))
        assert self.renouveler(decor['users_etu'][0]).status_code == 200

    def test_sur_le_serveur_de_travail_rien_ne_change(self, decor):
        from apps.publication.models import PublicationRecue
        PublicationRecue.objects.create(recue_le=timezone.now() + timedelta(seconds=5))
        assert self.renouveler(decor['users_etu'][0]).status_code == 200


# ── Notifications lues en ligne ───────────────────────────────────────────────

class TestNotifications:

    def test_une_notification_lue_en_ligne_le_reste_apres_publication(self, decor, miroir):
        from apps.notifications.models import Notification
        u = decor['users_etu'][0]
        n = Notification.objects.create(destinataire=u, titre='T', message='M')
        assert api(u).post(f'/api/v1/notifications/{n.pk}/lire/').status_code == 200
        Notification.objects.filter(pk=n.pk).update(lue=False)   # la publication réécrit
        assert api(u).get('/api/v1/notifications/unread-count/').data['count'] == 0
        r = api(u).get('/api/v1/notifications/', {'lue': 'false'})
        lignes = r.data['results'] if isinstance(r.data, dict) else r.data
        assert lignes == []

    def test_tout_lire(self, decor, miroir):
        from apps.notifications.models import Notification
        u = decor['users_etu'][0]
        for i in range(3):
            Notification.objects.create(destinataire=u, titre=f'T{i}', message='M')
        assert api(u).post('/api/v1/notifications/tout-lire/').data['updated'] == 3
        assert api(u).get('/api/v1/notifications/unread-count/').data['count'] == 0


# ── Le journal tolère un compte disparu ───────────────────────────────────────

def test_le_journal_d_audit_tolere_un_compte_disparu(decor):
    from django.db import connection
    from core.models import AuditLog
    AuditLog.objects.create(action='LOGIN_SUCCESS', model_name='CustomUser', object_id='1',
                            user_id=987654, label='compte disparu')
    with connection.cursor() as c:                     # aucune contrainte : l'insertion passe
        c.execute('SELECT count(*) FROM core_audit_log WHERE user_id = 987654')
        assert c.fetchone()[0] == 1
    r = api(decor['admin']).get('/api/v1/audit/')
    assert r.status_code == 200
    lignes = r.data['results'] if isinstance(r.data, dict) else r.data
    ligne = next(x for x in lignes if x['label'] == 'compte disparu')
    assert ligne['user_username'] is None


# ── Le profil n'est pas modifiable sur le miroir ──────────────────────────────

def test_le_profil_n_est_pas_modifiable_sur_le_miroir(decor, miroir):
    r = api(decor['users_etu'][0]).patch('/api/v1/portail/profil/', {'telephone': '1'})
    assert r.status_code == 403
