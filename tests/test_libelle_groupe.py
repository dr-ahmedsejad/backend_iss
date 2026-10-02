"""
Le nom d'un groupe sur une fiche de présence porte son NIVEAU.

En 2026-2027, trois groupes s'appellent « G1 » (L1 de Statistique, L2 et L3 de
SEA). La fiche titrait « Statistique — G1 » : deux fiches d'une même filière ne
se distinguaient par rien, et un surveillant qui prend la mauvaise feuille fait
pointer une promotion sur la liste d'une autre.

Les noms ci-dessous sont ceux de la base `iss`, relevés le 02/10/2026.
"""
import pytest

from apps.absence.libelles import libelle_groupe


class TestRegle:

    @pytest.mark.parametrize('nom, niveau, attendu', [
        ('G1',   'L1', 'L1 G1'),       # le cas signalé
        ('G2',   'L1', 'L1 G2'),
        ('SEA',  'L3', 'L3 SEA'),
        ('SDID', 'L3', 'L3 SDID'),
    ])
    def test_le_niveau_precede_un_nom_qui_ne_le_porte_pas(self, nom, niveau, attendu):
        assert libelle_groupe(nom, niveau) == attendu

    @pytest.mark.parametrize('nom, niveau', [
        ('SEA L2 - G1', 'L2'),
        ('LPSEA L2',    'L2'),
        ('SDID L2',     'L2'),
        ('STAT L1',     'L1'),
        ('sea l2 - g1', 'L2'),          # la casse ne compte pas
    ])
    def test_un_nom_qui_porte_deja_son_niveau_n_est_pas_double(self, nom, niveau):
        """Préfixer donnerait « L2 SEA L2 - G1 »."""
        assert libelle_groupe(nom, niveau) == nom

    @pytest.mark.parametrize('nom', ['HE', 'ST', 'STAGES'])
    def test_un_niveau_sans_rang_n_est_pas_ajoute(self, nom):
        """« Transversal HE » ne dirait rien de plus que « HE »."""
        assert libelle_groupe(nom, 'Transversal') == nom


class TestBords:

    def test_le_niveau_doit_etre_un_MOT_du_nom(self):
        """« L21 » ne porte pas « L2 » : sans frontière de mot, le niveau
        sauterait à tort."""
        assert libelle_groupe('L21', 'L2') == 'L2 L21'

    @pytest.mark.parametrize('nom, niveau, attendu', [
        ('G1', '',   'G1'),
        ('G1', None, 'G1'),
        ('',   'L1', ''),
        (None, 'L1', ''),
    ])
    def test_une_donnee_manquante_ne_fabrique_rien(self, nom, niveau, attendu):
        assert libelle_groupe(nom, niveau) == attendu


class TestAdresse:
    """L'écran lit le libellé ici ; le PDF l'emprunte à la même fonction."""

    def test_l_adresse_rend_le_libelle_avec_le_niveau(self, db):
        from rest_framework.test import APIClient
        from apps.departement.models import Departement
        from tests.factories.auth import UserFactory
        from tests.factories.parametres import InstitutionFactory, NiveauFactory
        from tests.factories.scolarite import FiliereFactory

        inst = InstitutionFactory(acronyme='ISS-L', est_principale=True)
        f = FiliereFactory(code='STATX', intitule_fr='Statistique')
        g = Departement.objects.create(nom='G1', annee_universitaire='2026-2027',
                                       institution=inst, filiere=f,
                                       niveau=NiveauFactory(niveau='L1'))
        c = APIClient()
        c.force_authenticate(UserFactory(username='u_lib', role='admin', is_superuser=True))
        r = c.get('/api/v1/absences/presences/liste-appel/',
                  {'departement': g.pk, 'annee_universitaire': '2026-2027'})
        assert r.status_code == 200
        assert r.data['groupe_libelle'] == 'L1 G1'
        assert r.data['filiere'] == 'Statistique'
