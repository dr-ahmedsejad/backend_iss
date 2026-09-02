"""
Filtres de la liste des progressions.

L'écran de révision des orientations sert deux gestes, et chacun a besoin de
son axe :

  * traiter une promotion à la fois → filtrer sur la filière SOURCE ;
  * relire les orientations posées, et surtout retrouver celles qui MANQUENT
    → filtrer sur la filière CIBLE, avec la valeur spéciale « aucune ».

Ce dernier cas est le plus utile : une progression sans filière cible est
sautée en silence par `ReinscriptionService.executer`. Sur l'année 2026-2027 de
la base réelle, 41 des 141 progressions sont dans ce cas.
"""
import pytest
from rest_framework.test import APIClient

from tests.factories.auth import UserFactory
from tests.factories.parametres import InstitutionFactory, YearFactory
from tests.factories.scolarite import FiliereFactory

URL = '/api/v1/inscriptions/progressions/'


@pytest.fixture(autouse=True)
def _vider_le_cache_rbac():
    """Le cache RBAC est par PROCESSUS, pas par test.

    `tests/conftest.py` porte déjà cette purge, mais un conftest ne s'applique
    qu'à son propre dossier : les tests placés sous `apps/` n'en bénéficient
    pas. Sans elle, un utilisateur autorisé dans un test précédent laisse une
    entrée `rbac:<pk>:…` que le rollback de la base ne retire pas — et comme
    sqlite réattribue les mêmes identifiants, un autre compte hérite du droit.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _droit(role, module_code, action_code):
    from apps.authentication.models import (Action, Module, ModuleAction,
                                            RoleDefault)
    mod, _ = Module.objects.get_or_create(
        code=module_code, defaults={'nom': module_code})
    act, _ = Action.objects.get_or_create(
        code=action_code, defaults={'nom': action_code})
    ma, _ = ModuleAction.objects.get_or_create(module=mod, action=act)
    RoleDefault.objects.update_or_create(
        role=role, module_action=ma, defaults={'allowed': True})


@pytest.fixture
def monde(db):
    """Quatre progressions : deux filières source, trois sorts différents."""
    from apps.absence.models import Etudiant
    from apps.departement.models import Departement
    from apps.inscriptions.models import Progression
    from apps.parametres.models import Niveau

    inst    = InstitutionFactory(acronyme='ISS', est_principale=True)
    source  = YearFactory(annee='2025-2026')
    cible   = YearFactory(annee='2026-2027')
    niveau  = Niveau.objects.create(niveau='L1')

    stat  = FiliereFactory(code='STAT',  intitule_fr='Statistique')
    lpsea = FiliereFactory(code='LPSEA', intitule_fr='Statistiques, Economie')

    dept = Departement.objects.create(
        nom='G1', annee_universitaire='2025-2026', institution=inst,
        filiere=stat, niveau=niveau, groupe='')

    def progression(matricule, filiere_source, filiere_cible):
        etu = Etudiant.objects.create(
            matricule=matricule, nom=f'Etu{matricule}', nom_fr=f'Etu{matricule}',
            prenom_fr='Test', genre='M', email=f'{matricule}@test.mr',
            nationalite_fr='Mauritanienne', departement=dept)
        return Progression.objects.create(
            etudiant=etu, matricule=matricule,
            annee_source=source, annee_cible=cible, institution=inst,
            filiere_source=filiere_source, filiere_cible=filiere_cible,
            niveau_source=1, niveau_cible=2,
            decision='progression', statut='en_attente')

    return {
        'cible':  cible,
        'stat':   stat,
        'lpsea':  lpsea,
        # STAT → orientée vers LPSEA
        'a': progression('M001', stat,  lpsea),
        # STAT → NON orientée : celle que l'exécution sauterait
        'b': progression('M002', stat,  None),
        # LPSEA → reste chez elle
        'c': progression('M003', lpsea, lpsea),
        # LPSEA → non orientée
        'd': progression('M004', lpsea, None),
    }


@pytest.fixture
def client_scolarite(monde):
    for action in ('voir', 'modifier'):
        _droit('scolarite', 'insc_progression', action)
    c = APIClient()
    c.force_authenticate(user=UserFactory(username='u_scol', role='scolarite'))
    return c


def _matricules(reponse):
    return sorted(p['matricule'] for p in reponse.data)


def _appel(client, monde, **filtres):
    return client.get(URL, {'annee_cible': monde['cible'].pk, **filtres})


class TestFiltreFiliereSource:

    def test_sans_filtre_tout_remonte(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde)
        assert r.status_code == 200
        assert _matricules(r) == ['M001', 'M002', 'M003', 'M004']

    def test_filtre_sur_la_filiere_d_origine(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde, filiere_source=monde['stat'].pk)
        assert r.status_code == 200
        assert _matricules(r) == ['M001', 'M002']

    def test_l_autre_filiere(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde, filiere_source=monde['lpsea'].pk)
        assert _matricules(r) == ['M003', 'M004']

    def test_un_identifiant_invalide_est_refuse(self, client_scolarite, monde):
        """Plutôt qu'un 500 sur la conversion, ou pire, une liste vide qui
        passerait pour « aucun résultat »."""
        r = _appel(client_scolarite, monde, filiere_source='STAT')
        assert r.status_code == 400


class TestFiltreFiliereCible:

    def test_filtre_sur_l_orientation_posee(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde, filiere_cible=monde['lpsea'].pk)
        assert _matricules(r) == ['M001', 'M003']

    def test_les_non_orientes_se_retrouvent(self, client_scolarite, monde):
        """Le cas qui motive ce filtre : ces progressions seront SAUTÉES."""
        r = _appel(client_scolarite, monde, filiere_cible='aucune')
        assert r.status_code == 200
        assert _matricules(r) == ['M002', 'M004']

    def test_une_valeur_inconnue_est_refusee(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde, filiere_cible='non-orientes')
        assert r.status_code == 400


class TestCombinaisons:

    def test_les_deux_axes_se_cumulent(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde,
                   filiere_source=monde['stat'].pk, filiere_cible='aucune')
        assert _matricules(r) == ['M002']

    def test_filiere_et_statut_se_cumulent(self, client_scolarite, monde):
        monde['a'].statut = 'executee'
        monde['a'].save(update_fields=['statut'])
        r = _appel(client_scolarite, monde,
                   filiere_source=monde['stat'].pk, statut='en_attente')
        assert _matricules(r) == ['M002']

    def test_les_filtres_existants_ne_bougent_pas(self, client_scolarite, monde):
        """Non-régression : décision et statut répondent comme avant."""
        assert _matricules(_appel(client_scolarite, monde,
                                  decision='progression')) == \
            ['M001', 'M002', 'M003', 'M004']
        assert _appel(client_scolarite, monde,
                      decision='redoublement').data == []

    def test_annee_cible_reste_obligatoire(self, client_scolarite):
        r = client_scolarite.get(URL, {'filiere_cible': 'aucune'})
        assert r.status_code == 400


class TestGenreDansLaReponse:
    """Le genre est exposé pour que l'écran accorde le statut affiché.

    « Inscription N+1 créée » décrivait une écriture en base — exact, mais ce
    n'est pas ce qu'on lit en face d'un nom. L'écran affiche « Inscrit » ou
    « Inscrite », et il lui faut donc le genre. Sur l'année 2026-2027 de la base
    réelle, 58 des 141 progressions concernent une étudiante.
    """

    def test_le_genre_accompagne_chaque_ligne(self, client_scolarite, monde):
        r = _appel(client_scolarite, monde)
        assert r.status_code == 200
        assert all('genre' in p for p in r.data)

    def test_le_genre_est_celui_de_l_etudiant(self, client_scolarite, monde):
        etu = monde['a'].etudiant
        etu.genre = 'F'
        etu.save(update_fields=['genre'])

        r = _appel(client_scolarite, monde)
        par_matricule = {p['matricule']: p['genre'] for p in r.data}
        assert par_matricule['M001'] == 'F'
        assert par_matricule['M002'] == 'M'
