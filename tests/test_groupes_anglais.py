"""
Groupes d'anglais — étape 1 : les groupes et l'affectation (09/10/2026).

Deux groupes d'anglais au plus par niveau et par année ; l'étudiant garde son
groupe habituel et il est affecté, pour l'anglais seul, à l'un des groupes de
son niveau. Voir apps/edt/anglais.py.
"""
from io import BytesIO

import openpyxl
import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.edt import anglais
from apps.edt.models import AffectationAnglais, AnnonceEmploi, GroupeAnglais
from tests._edt_decor import ANNEE, monde  # noqa: F401

URL = '/api/v1/edt/anglais/'


@pytest.fixture
def etu(monde):
    """Deux étudiants de L1 (G1, G2), un de L2 (SDID L2)."""
    from apps.absence.models import Etudiant
    d = monde['depts']
    return {
        'a': Etudiant.objects.create(matricule='24601', nom='Aicha', departement=d['G1'], genre='F'),
        'b': Etudiant.objects.create(matricule='24602', nom='Brahim', departement=d['G2'], genre='M'),
        'c': Etudiant.objects.create(matricule='24603', nom='Cheikh', departement=d['SDID L2'], genre='M'),
    }


@pytest.fixture
def deux(monde):
    """Les deux groupes d'anglais de L1."""
    return (anglais.creer_groupe(ANNEE, monde['l1'].pk),
            anglais.creer_groupe(ANNEE, monde['l1'].pk, 'Anglais L1 — Advanced'))


def _client(role='admin'):
    from django.contrib.auth import get_user_model
    u = get_user_model().objects.create_user(username=f'u_{role}', email=f'{role}@t.l',
                                             password='x', role=role,
                                             is_superuser=(role == 'admin'))
    c = APIClient()
    c.force_authenticate(u)
    return c


class TestGroupes:

    def test_les_niveaux_de_l_annee_sans_le_transversal(self, monde):
        assert [n.niveau for n in anglais.niveaux(ANNEE)] == ['L1', 'L2']

    def test_deux_groupes_par_niveau_pas_trois(self, monde, deux):
        g1, g2 = deux
        assert (g1.rang, g1.departement.nom) == (1, 'Anglais L1 — Groupe 1')
        assert (g2.rang, g2.departement.nom) == (2, 'Anglais L1 — Advanced')
        with pytest.raises(anglais.RegleAnglais, match='déjà ses 2 groupes'):
            anglais.creer_groupe(ANNEE, monde['l1'].pk)

    def test_la_base_refuse_un_troisieme_rang(self, monde, deux):
        dep = deux[0].departement
        with pytest.raises(IntegrityError), transaction.atomic():
            GroupeAnglais.objects.create(departement=monde['depts']['G1'], niveau=dep.niveau,
                                         annee_universitaire=ANNEE, rang=3)

    def test_un_groupe_d_anglais_est_un_groupe_ordinaire_sans_filiere(self, monde, deux):
        dep = deux[0].departement
        assert (dep.niveau_id, dep.filiere_id, dep.annee_universitaire, dep.institution_id) == \
               (monde['l1'].pk, None, ANNEE, monde['inst'].pk)
        # … et il ne compte pas parmi les groupes habituels.
        assert dep not in anglais.groupes_habituels(ANNEE)

    def test_pas_de_groupe_d_anglais_sans_groupe_du_niveau(self, monde):
        with pytest.raises(anglais.RegleAnglais, match='Aucun groupe de L2 en 2030-2031'):
            anglais.creer_groupe('2030-2031', monde['l2'].pk)

    def test_un_nom_par_annee_sans_compter_casse_ni_accents(self, monde, deux):
        # Le nom sert à reconnaître le groupe dans un fichier Excel.
        with pytest.raises(anglais.RegleAnglais, match='déjà'):
            anglais.creer_groupe(ANNEE, monde['l2'].pk, 'ANGLAIS  L1 — GROUPE 1')
        anglais.renommer(deux[0], 'Avancé')
        with pytest.raises(anglais.RegleAnglais, match='déjà'):
            anglais.renommer(deux[1], 'avance')
        deux[0].departement.refresh_from_db()
        assert deux[0].departement.nom == 'Avancé'


class TestAffectation:

    def test_affecter_changer_retirer(self, monde, etu, deux):
        g1, g2 = deux
        a = etu['a']
        r = anglais.affecter(ANNEE, [(a.pk, g1.pk)])
        assert r['lignes'][0]['statut'] == anglais.AFFECTE
        assert anglais.affecter(ANNEE, [(a.pk, g1.pk)])['lignes'][0]['statut'] == anglais.INCHANGE
        assert anglais.affecter(ANNEE, [(a.pk, g2.pk)])['lignes'][0]['statut'] == anglais.CHANGE
        assert AffectationAnglais.objects.get(etudiant=a).groupe == g2
        assert anglais.affecter(ANNEE, [(a.pk, None)])['lignes'][0]['statut'] == anglais.RETIRE
        assert not AffectationAnglais.objects.exists()

    def test_le_groupe_habituel_ne_bouge_pas(self, monde, etu, deux):
        anglais.affecter(ANNEE, [(etu['a'].pk, deux[0].pk)])
        etu['a'].refresh_from_db()
        assert etu['a'].departement == monde['depts']['G1']

    def test_un_groupe_d_anglais_n_est_jamais_le_groupe_habituel(self, monde, etu, deux):
        from apps.absence.serializers import EtudiantSerializer
        s = EtudiantSerializer(etu['a'], data={'departement': deux[0].departement_id}, partial=True)
        assert not s.is_valid()
        assert 'groupe d\'anglais' in str(s.errors['departement'][0])
        s = EtudiantSerializer(etu['a'], data={'departement': monde['depts']['G2'].pk}, partial=True)
        assert s.is_valid(), s.errors

    def test_un_etudiant_d_un_autre_niveau_est_refuse(self, monde, etu, deux):
        r = anglais.affecter(ANNEE, [(etu['c'].pk, deux[0].pk)])
        assert r['lignes'][0]['statut'] == anglais.HORS_NIVEAU
        assert not AffectationAnglais.objects.exists()

    def test_l_apercu_n_ecrit_rien(self, monde, etu, deux):
        r = anglais.affecter(ANNEE, [(etu['a'].pk, deux[0].pk)], apercu=True)
        assert r['bilan'] == {anglais.AFFECTE: 1}
        assert not AffectationAnglais.objects.exists()

    def test_doublon_et_groupe_inconnu(self, monde, etu, deux):
        a, b = etu['a'], etu['b']
        r = anglais.affecter(ANNEE, [(a.pk, deux[0].pk), (a.pk, deux[1].pk), (b.pk, 999999)])
        assert [l['statut'] for l in r['lignes']] == [anglais.DOUBLON, anglais.DOUBLON,
                                                     anglais.GROUPE_INCONNU]
        assert not AffectationAnglais.objects.exists()


def _classeur(lignes):
    wb = openpyxl.Workbook()
    for l in lignes:
        wb.active.append(l)
    b = BytesIO()
    wb.save(b)
    b.seek(0)
    b.name = 'anglais.xlsx'
    return b


class TestImport:

    def test_lecture_et_resolution(self, monde, etu, deux):
        fichier = _classeur([
            ['Liste anglais'],
            ['N°', 'Matricule', 'Nom', 'Groupe anglais'],
            [1, 24601.0, 'Aicha', '1'],          # numéro du groupe ; matricule lu en nombre
            [2, '24602', 'Brahim', 'advanced'],  # morceau du nom, sans casse
            [3, '24603', 'Cheikh', '1'],         # L2 : aucun groupe d'anglais pour lui
            [4, '99999', 'Inconnu', '2'],
            [5, '24601', 'Aicha', '2'],          # le même matricule deux fois
            [6, '', '', ''],
        ])
        rangs = anglais.lire_classeur(fichier)
        assert rangs[0] == (3, '24601', '1')
        r = anglais.importer(ANNEE, rangs, apercu=True)
        assert [(l['ligne'], l['statut']) for l in r['lignes']] == [
            (3, anglais.DOUBLON), (4, anglais.AFFECTE), (5, anglais.HORS_NIVEAU),
            (6, anglais.INCONNU), (7, anglais.DOUBLON)]
        assert r['lignes'][1]['groupe']['nom'] == 'Anglais L1 — Advanced'
        assert not AffectationAnglais.objects.exists()

        anglais.importer(ANNEE, rangs)
        assert list(AffectationAnglais.objects.values_list('etudiant__matricule', 'groupe__rang')) \
            == [('24602', 2)]

    def test_sans_en_tete_a_et_b(self, monde, etu, deux):
        rangs = anglais.lire_classeur(_classeur([['24601', 'Anglais L1 — Groupe 1']]))
        assert rangs == [(1, '24601', 'Anglais L1 — Groupe 1')]
        assert anglais.importer(ANNEE, rangs)['bilan'] == {anglais.AFFECTE: 1}

    def test_un_morceau_ambigu_ne_designe_aucun_groupe(self, monde, deux):
        assert anglais.trouver_groupe('anglais l1', list(deux)) is None
        assert anglais.trouver_groupe('Advanced', list(deux)) == deux[1]

    def test_fichier_illisible(self):
        with pytest.raises(anglais.RegleAnglais, match='illisible'):
            anglais.lire_classeur(BytesIO(b'pas un classeur'))


class TestSuppression:

    def test_un_groupe_vierge_se_supprime_avec_ses_affectations(self, monde, etu, deux):
        from apps.departement.models import Departement
        anglais.affecter(ANNEE, [(etu['a'].pk, deux[0].pk)])
        dep_id = deux[0].departement_id
        anglais.supprimer(deux[0])
        assert not Departement.objects.filter(pk=dep_id).exists()
        assert not AffectationAnglais.objects.exists()
        etu['a'].refresh_from_db()                   # l'étudiant, lui, reste
        assert etu['a'].departement == monde['depts']['G1']

    def test_un_groupe_deja_utilise_ne_se_supprime_pas(self, monde, deux):
        AnnonceEmploi.objects.create(annee_universitaire=ANNEE, type_semestre='I',
                                     numero_semaine=1, departement=deux[0].departement)
        with pytest.raises(anglais.RegleAnglais, match='déjà utilisé'):
            anglais.supprimer(deux[0])
        assert GroupeAnglais.objects.filter(pk=deux[0].pk).exists()


class TestAdresses:

    def test_le_tableau_de_l_annee(self, monde, etu, deux):
        anglais.affecter(ANNEE, [(etu['a'].pk, deux[0].pk)])
        r = _client().get(URL, {'annee': ANNEE})
        assert r.status_code == 200, r.data
        l1 = next(n for n in r.data['niveaux'] if n['niveau'] == 'L1')
        assert (l1['etudiants'], l1['affectes']) == (2, 1)
        assert [(g['rang'], g['effectif']) for g in l1['groupes']] == [(1, 1), (2, 0)]

    def test_creer_renommer_supprimer(self, monde):
        c = _client()
        r = c.post(URL + 'groupes/', {'annee': ANNEE, 'niveau': monde['l2'].pk}, format='json')
        assert r.status_code == 201, r.data
        gid = r.data['id']
        r = c.patch(f'{URL}groupes/{gid}/', {'nom': 'Intermediate L2'}, format='json')
        assert r.data['nom'] == 'Intermediate L2'
        assert c.delete(f'{URL}groupes/{gid}/').status_code == 204

    def test_etudiants_et_affectation(self, monde, etu, deux):
        c = _client()
        r = c.get(URL + 'etudiants/', {'annee': ANNEE, 'niveau': monde['l1'].pk})
        assert [e['matricule'] for e in r.data['etudiants']] == ['24601', '24602']
        r = c.post(URL + 'affecter/', {'annee': ANNEE, 'affectations': [
            {'etudiant': etu['a'].pk, 'groupe': deux[1].pk}]}, format='json')
        assert r.status_code == 200 and r.data['bilan'] == {anglais.AFFECTE: 1}

    def test_import_par_l_adresse(self, monde, etu, deux):
        fichier = _classeur([['Matricule', 'Groupe'], ['24602', '2']])
        r = _client().post(URL + 'importer/', {'annee': ANNEE, 'fichier': fichier, 'apercu': '1'},
                           format='multipart')
        assert r.status_code == 200, r.data
        assert r.data['apercu'] is True and r.data['bilan'] == {anglais.AFFECTE: 1}

    def test_sans_droit(self, monde):
        c = _client('enseignant')
        assert c.get(URL, {'annee': ANNEE}).status_code == 403
        assert c.post(URL + 'groupes/', {'annee': ANNEE, 'niveau': monde['l1'].pk},
                      format='json').status_code == 403
