"""
Ce qui prévient l'ENSEIGNANT (cloche, app « ISS Enseignant ») :

  * une réclamation d'étudiant sur un de ses éléments ;
  * la décision sur sa contestation d'une séance « Non fait » ;
  * l'ouverture d'une session de saisie des notes.

Et son profil : il lit sa fiche et corrige lui-même téléphone et email.
"""
import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401


def _prof(monde, nom='Moustapha', actif=True):
    from apps.authentication.models import CustomUser
    u = CustomUser.objects.create_user(username='prof_%s' % nom, email='%s@iss.mr' % nom,
                                       password='x', role='enseignant', is_active=actif)
    p = monde['profs'][nom]
    p.user = u
    p.save(update_fields=['user'])
    return u


def _pointage(monde, prof='Moustapha', em='SEA11', ts='I'):
    from apps.suivi.models import SuiviePointage
    return SuiviePointage.objects.create(
        annee_universitaire=ANNEE, numero_semaine=1, type_semestre=ts,
        prof=monde['profs'][prof], em=monde['ems'][em], type_seance_fk=monde['cm'],
        creneau_fk=monde['creneaux']['08h00-09h30'], institution=monde['inst'])


def _notifs(user):
    from apps.notifications.models import Notification
    return list(Notification.objects.filter(destinataire=user).order_by('created_at'))


@pytest.fixture
def annee_active(monde):
    from apps.parametres.models import Year
    y, _ = Year.objects.get_or_create(annee=ANNEE)
    Year.objects.exclude(pk=y.pk).update(est_active=False)
    y.est_active = True
    y.save(update_fields=['est_active'])
    return y


class TestReclamationDeposee:

    def test_l_enseignant_de_l_element_est_prevenu(self, monde, annee_active):
        from apps.notifications.enseignants import reclamation_deposee
        from apps.reclamations.models import Reclamation
        prof = _prof(monde)
        autre = _prof(monde, 'Abderahmane')
        _pointage(monde)
        _pointage(monde, prof='Abderahmane', em='SEA12')
        em = monde['ems']['SEA11']
        r = Reclamation.objects.create(etudiant_id=1, etudiant_nom='Aminata', type_reclamation='note',
                                       em_id=em.pk, em_code=em.code_em, em_intitule=em.intitule, motif='m')

        assert reclamation_deposee(r) == 1
        [n] = _notifs(prof)
        assert n.titre.startswith('Nouvelle réclamation') and 'Aminata' in n.message
        assert n.lien.endswith('/reclamations')
        assert _notifs(autre) == []

    def test_sans_element_rien(self, monde, annee_active):
        from apps.notifications.enseignants import reclamation_deposee
        from apps.reclamations.models import Reclamation
        _prof(monde)
        r = Reclamation.objects.create(etudiant_id=1, type_reclamation='autre', motif='m')
        assert reclamation_deposee(r) == 0


class TestContestation:

    def test_la_decision_revient_a_l_enseignant(self, monde):
        from apps.notifications.enseignants import contestation_traitee
        from apps.reclamations.models import ReclamationSeance
        prof = _prof(monde)
        rs = ReclamationSeance.objects.create(
            pointage_id=1, prof_id=monde['profs']['Moustapha'].pk, em_code='SEA11', type_seance='CM',
            numero_semaine=3, motif='j\'étais là', statut='acceptee', reponse='Corrigé.')

        assert contestation_traitee(rs) == 1
        [n] = _notifs(prof)
        assert n.titre == 'Contestation acceptée'
        assert 'semaine 3' in n.message and 'Corrigé.' in n.message
        assert n.lien.endswith('/emploi')


class TestSessionOuverte:

    def test_ouvrir_la_session_previent_les_enseignants_des_semestres(self, monde, gens, annee_active):
        from apps.evaluations.models import SessionEvaluation
        prof = _prof(monde)
        pairs = _prof(monde, 'Abderahmane')
        _pointage(monde)                                   # impairs
        _pointage(monde, prof='Abderahmane', ts='P')      # pairs seulement
        from apps.parametres.models import Institution
        # La liste des sessions ne montre que l'institution principale.
        principale = Institution.objects.filter(est_principale=True).first()
        session = SessionEvaluation.objects.create(
            annee_univ=annee_active, institution=principale, type_session='normale',
            type_semestre='Impairs', intitule='Session normale S1')

        r = api(gens['admin']).post('/api/v1/evaluations/sessions/%s/ouvrir/' % session.pk)

        assert r.status_code == 200, r.data
        [n] = _notifs(prof)
        assert n.titre == 'Saisie des notes ouverte' and 'Session normale S1' in n.message
        assert n.lien.endswith('/notes')
        assert _notifs(pairs) == []
        # Rouvrir une session déjà ouverte ne renvoie rien.
        api(gens['admin']).post('/api/v1/evaluations/sessions/%s/ouvrir/' % session.pk)
        assert len(_notifs(prof)) == 1


class TestProfil:
    URL = '/api/v1/portail/enseignant/profil/'

    def test_lire_et_corriger_ses_coordonnees(self, monde):
        user = _prof(monde)
        r = api(user).get(self.URL)
        assert r.status_code == 200 and r.data['nom'] == 'Moustapha'

        r = api(user).patch(self.URL, {'telephone': '22 33 44 55', 'email': 'm@iss.mr'}, format='json')
        assert r.status_code == 200, r.data
        assert (r.data['telephone'], r.data['email']) == ('22334455', 'm@iss.mr')

        assert api(user).patch(self.URL, {'email': 'pas-un-email'}, format='json').status_code == 400
        assert api(user).patch(self.URL, {'telephone': '12'}, format='json').status_code == 400

    def test_reserve_aux_enseignants(self, monde, gens):
        assert api(gens['de']).get(self.URL).status_code == 403
