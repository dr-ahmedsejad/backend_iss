"""
Avancement par EM : filtre par filière (demande du 09/10/2026).

Le filtre ne change pas le calcul : il restreint les EM AVANT le
dédoublonnage par code, avec la chaîne de filière habituelle (filière de
l'EM, sinon de son module LMD, sinon de son groupe). Sans filtre, la liste
est celle d'avant.
"""
from decimal import Decimal

import pytest

from apps.avancement.views import _compute_avancement_em
from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL = '/api/v1/avancement/em/'


@pytest.fixture
def sans_filiere(monde):
    """Un EM sans filière propre, rattaché à SDID par son module LMD."""
    from apps.em.models import EM
    from apps.modules.models import Module
    module = Module.objects.create(
        code='MOD-X31', intitule_fr='UE X31', semestre=monde['s3'], filiere=monde['f_sdid'],
        institution=monde['inst'], credits=6, coefficient=Decimal('1.00'),
        seuil_compensation=Decimal('10.00'))
    return EM.objects.create(code_em='X31', intitule='Cours X31', filiere=None,
                             semestre=monde['s3'], module_lmd=module, institution=monde['inst'],
                             seuil_eliminatoire=Decimal('6.00'))


def _codes(**kw):
    return sorted(r['code_em'] for r in _compute_avancement_em(ANNEE, 'I', **kw))


class TestLeCalcul:

    def test_sans_filtre_rien_ne_change(self, monde, sans_filiere):
        assert _codes() == ['HE11', 'SDID31', 'SEA11', 'SEA12', 'SEA31', 'X31']

    def test_une_filiere(self, monde, sans_filiere):
        assert _codes(filiere_id=monde['f_sea'].pk) == ['HE11', 'SEA11', 'SEA12', 'SEA31']

    def test_la_filiere_du_module_compte_pour_un_em_sans_filiere(self, monde, sans_filiere):
        assert _codes(filiere_id=monde['f_sdid'].pk) == ['SDID31', 'X31']

    def test_avec_le_semestre(self, monde, sans_filiere):
        assert _codes(filiere_id=monde['f_sea'].pk, semestre_id=monde['s3'].pk) == ['SEA31']


class TestLesAdresses:

    def test_le_parametre_filiere(self, monde, gens, sans_filiere):
        r = api(gens['admin']).get(URL, {'annee_universitaire': ANNEE, 'type_semestre': 'I',
                                         'filiere': monde['f_sdid'].pk})
        assert r.status_code == 200, r.data
        assert sorted(x['code_em'] for x in r.data) == ['SDID31', 'X31']

    def test_un_parametre_invalide_est_ignore(self, monde, gens, sans_filiere):
        r = api(gens['admin']).get(URL, {'annee_universitaire': ANNEE, 'type_semestre': 'I',
                                         'filiere': 'abc'})
        assert len(r.data) == 6

    def test_la_liste_des_filieres_de_l_annee(self, monde, gens, sans_filiere):
        r = api(gens['admin']).get(URL + 'filieres/', {'annee_universitaire': ANNEE,
                                                       'type_semestre': 'I'})
        assert r.status_code == 200, r.data
        assert [f['code'] for f in r.data] == ['SDID', 'SEA']

    def test_la_liste_demande_le_droit_avancement(self, monde, gens):
        r = api(gens['orphelin']).get(URL + 'filieres/', {'annee_universitaire': ANNEE})
        assert r.status_code == 403
