"""
L'emploi du temps validé est annoncé aux étudiants.

Tant que le suivi d'une semaine n'est pas généré, son emploi du temps est
provisoire ; la génération en fait le vrai — celui que le portail et l'app
étudiante affichent (ils lisent le suivi). Demande du 05/10/2026 : prévenir
l'étudiant à ce moment-là, sur son téléphone. La notification va dans la
cloche ; `envoyer_push` la pousse vers l'app (tests/test_push.py).
"""
import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL_GENERER   = '/api/v1/suivi/suivies/ajouter/'
URL_SUPPRIMER = '/api/v1/suivi/suivies/par-semaine/'


def poser(monde, dept, semaine_num):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept], semaine=monde['semaines'][(semaine_num, 'Lundi')],
        creneau_fk=monde['creneaux']['08h00-09h30'], em=monde['ems']['SEA11'],
        prof=monde['profs']['Moustapha'], salle=monde['salles']['101'],
        type_seance_fk=monde['cm'], origine='grille')


def generer(user, semaine):
    return api(user).post(URL_GENERER, {
        'annee_universitaire': ANNEE, 'type_semestre': 'I', 'numero_semaine': semaine},
        format='json')


def supprimer(user, semaine):
    return api(user).delete(f'{URL_SUPPRIMER}?numero_semaine={semaine}'
                            f'&annee_universitaire={ANNEE}&type_semestre=I&force=1')


def etudiant(monde, groupe, matricule, compte=True, actif=True):
    from apps.absence.models import Etudiant
    from apps.authentication.models import CustomUser
    user = None
    if compte:
        user = CustomUser.objects.create_user(
            username='etu%s' % matricule, email='etu%s@iss.mr' % matricule,
            password='x', role='etudiant', is_active=actif)
    return Etudiant.objects.create(matricule=matricule, nom='Nom%s' % matricule,
                                   departement=monde['depts'][groupe], user=user)


@pytest.fixture
def classe(monde):
    return {
        'g1':          etudiant(monde, 'G1', '24601'),
        'g1_bis':      etudiant(monde, 'G1', '24602'),
        'sans_compte': etudiant(monde, 'G1', '24603', compte=False),
        'inactif':     etudiant(monde, 'G1', '24604', actif=False),
        'g2':          etudiant(monde, 'G2', '24701'),
    }


def notifs(etu):
    from apps.notifications.models import Notification
    return list(Notification.objects.filter(destinataire=etu.user).order_by('created_at'))


class TestValide:

    def test_la_generation_previent_les_etudiants_du_groupe(self, monde, gens, classe):
        poser(monde, 'G1', 1)
        r = generer(gens['admin'], 1)
        assert r.status_code < 400, r.data
        [n] = notifs(classe['g1'])
        assert n.titre == 'Emploi du temps de la semaine 1 validé'
        # Le lien finit par « /emploi » : l'app ouvre l'onglet Emploi du temps.
        assert n.lien == '/dashboard/portail/emploi'
        lundi = monde['semaines'][(1, 'Lundi')].date
        mercredi = monde['semaines'][(1, 'Mercredi')].date
        assert 'du %s au %s' % (lundi.strftime('%d/%m'), mercredi.strftime('%d/%m')) in n.message
        assert len(notifs(classe['g1_bis'])) == 1
        assert '2 étudiants prévenus' in r.data['message']

    def test_un_groupe_sans_cours_cette_semaine_n_est_pas_prevenu(self, monde, gens, classe):
        poser(monde, 'G1', 1)
        generer(gens['admin'], 1)
        assert notifs(classe['g2']) == []

    def test_ni_sans_compte_ni_compte_desactive(self, monde, gens, classe):
        from apps.notifications.models import Notification
        poser(monde, 'G1', 1)
        generer(gens['admin'], 1)
        assert Notification.objects.count() == 2       # g1 et g1_bis, pas les deux autres
        assert notifs(classe['inactif']) == []

    def test_l_emploi_provisoire_n_est_pas_annonce(self, monde, gens, classe):
        """Planifier n'annonce rien : seule la génération valide."""
        from apps.notifications.models import Notification
        poser(monde, 'G1', 1)
        assert Notification.objects.count() == 0


class TestEnseignant:
    """L'enseignant qui fait cours cette semaine est prévenu aussi (app enseignant)."""

    def _compte(self, monde, nom='Moustapha', actif=True):
        from apps.authentication.models import CustomUser
        u = CustomUser.objects.create_user(username='prof_%s' % nom, email='%s@iss.mr' % nom,
                                           password='x', role='enseignant', is_active=actif)
        p = monde['profs'][nom]
        p.user = u
        p.save(update_fields=['user'])
        return u

    def _notifs(self, user):
        from apps.notifications.models import Notification
        return list(Notification.objects.filter(destinataire=user).order_by('created_at'))

    def test_l_enseignant_de_la_semaine_est_prevenu_une_fois(self, monde, gens, classe):
        prof = self._compte(monde)
        poser(monde, 'G1', 1)
        poser(monde, 'G2', 1)
        assert generer(gens['admin'], 1).status_code < 400
        [n] = self._notifs(prof)
        assert n.titre == 'Emploi du temps de la semaine 1 validé'
        assert n.lien == '/dashboard/portail/emploi'

    def test_une_semaine_regeneree_lui_est_annoncee_modifiee(self, monde, gens, classe):
        prof = self._compte(monde)
        poser(monde, 'G1', 1)
        generer(gens['admin'], 1)
        supprimer(gens['admin'], 1)
        generer(gens['admin'], 1)
        assert [n.titre for n in self._notifs(prof)] == [
            'Emploi du temps de la semaine 1 validé', 'Emploi du temps de la semaine 1 modifié']

    def test_compte_desactive_pas_prevenu(self, monde, gens, classe):
        prof = self._compte(monde, actif=False)
        poser(monde, 'G1', 1)
        generer(gens['admin'], 1)
        assert self._notifs(prof) == []


class TestModifie:

    def test_une_semaine_regeneree_est_annoncee_modifiee(self, monde, gens, classe):
        poser(monde, 'G1', 1)
        generer(gens['admin'], 1)
        assert supprimer(gens['admin'], 1).status_code < 400
        r = generer(gens['admin'], 1)
        assert r.status_code < 400, r.data
        premiere, seconde = notifs(classe['g1'])
        assert premiere.titre.endswith('validé')
        assert seconde.titre == 'Emploi du temps de la semaine 1 modifié'
        assert 'a été modifié' in seconde.message
        from apps.edt.models import AnnonceEmploi
        assert AnnonceEmploi.objects.get(departement=monde['depts']['G1']).nb_annonces == 2

    def test_une_generation_refusee_n_annonce_rien(self, monde, gens, classe):
        poser(monde, 'G1', 1)
        poser(monde, 'G1', 2)
        generer(gens['admin'], 1)
        generer(gens['admin'], 2)
        avant = len(notifs(classe['g1']))
        assert generer(gens['admin'], 1).status_code == 409
        assert len(notifs(classe['g1'])) == avant


class TestPerimetre:

    def test_un_groupe_deja_genere_n_est_pas_reannonce(self, monde, gens, classe):
        """Le directeur des études génère ses groupes ; l'administrateur génère
        ensuite la même semaine pour d'autres : les premiers ne reçoivent pas
        une seconde annonce « modifié » qui ne correspond à rien."""
        poser(monde, 'G1', 1)
        poser(monde, 'SEA L2 G1', 1)
        generer(gens['de'], 1)
        assert len(notifs(classe['g1'])) == 1
        generer(gens['admin'], 1)
        assert len(notifs(classe['g1'])) == 1


class TestJamaisAuDetrimentDuSuivi:

    def test_une_annonce_en_echec_ne_fait_pas_echouer_la_generation(
            self, monde, gens, classe, monkeypatch):
        """Le suivi alimente le pointage et la paie : il passe avant l'annonce."""
        from apps.edt import generation
        from apps.suivi.models import Suivie

        def panne(*a, **k):
            raise RuntimeError('notification impossible')

        monkeypatch.setattr(generation, 'annoncer_semaine', panne)
        poser(monde, 'G1', 1)
        r = generer(gens['admin'], 1)
        assert r.status_code < 400, r.data
        assert Suivie.objects.filter(numero_semaine=1, departement=monde['depts']['G1']).exists()
        assert notifs(classe['g1']) == []
