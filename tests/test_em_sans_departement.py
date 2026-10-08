"""
Un EM se crée sans département (demande du 08/10/2026) : son `departement`
(groupe d'une année) est vestigial ; son identité est sa filière, déduite du
module LMD à l'enregistrement (EM.save). Les formulaires ne le demandent plus.
"""
import pytest
from rest_framework.test import APIClient

from tests._edt_decor import monde  # noqa: F401


@pytest.fixture
def admin(db):
    from apps.authentication.models import CustomUser
    c = APIClient()
    c.force_authenticate(CustomUser.objects.create_user(
        username='adm', email='adm@iss.mr', password='x', role='admin', is_superuser=True))
    return c


def test_creation_sans_departement_avec_la_filiere_du_module(admin, monde):
    from apps.modules.models import Module
    module = Module.objects.filter(semestre=monde['s1'], filiere=monde['f_sea']).first()
    r = admin.post('/api/v1/ems/', {
        'code_em': 'SEA19', 'intitule': 'Nouvel élément', 'CM': 10, 'TD': 0, 'TP': 0, 'PR': 0,
        'credits': None, 'coefficient': None, 'has_tp': False,
        'semestre': monde['s1'].pk, 'module_lmd': module.pk,
    }, format='json')
    assert r.status_code == 201, r.data
    assert r.data['departement'] is None
    assert r.data['filiere'] == monde['f_sea'].pk


def test_modifier_sans_toucher_au_departement(admin, monde):
    em = monde['ems']['SEA11']
    r = admin.patch(f'/api/v1/ems/{em.pk}/', {'intitule': 'Renommé'}, format='json')
    assert r.status_code == 200, r.data
    em.refresh_from_db()
    assert em.intitule == 'Renommé'


def test_sans_module_ni_filiere_la_filiere_est_demandee(admin, monde):
    r = admin.post('/api/v1/ems/', {
        'code_em': 'SEA18', 'intitule': 'Sans module', 'CM': 10, 'TD': 0, 'TP': 0, 'PR': 0,
        'has_tp': False, 'semestre': monde['s1'].pk, 'module_lmd': None,
    }, format='json')
    assert r.status_code == 400
    assert 'filiere' in str(r.data)


def test_sans_module_avec_filiere(admin, monde):
    r = admin.post('/api/v1/ems/', {
        'code_em': 'SEA17', 'intitule': 'Sans module', 'CM': 10, 'TD': 0, 'TP': 0, 'PR': 0,
        'has_tp': False, 'semestre': monde['s1'].pk, 'module_lmd': None,
        'filiere': monde['f_sea'].pk,
    }, format='json')
    assert r.status_code == 201, r.data
    assert r.data['departement'] is None and r.data['filiere'] == monde['f_sea'].pk
