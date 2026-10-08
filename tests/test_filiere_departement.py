"""
Une filière appartient toujours à un département académique (décision du
08/10/2026). Le formulaire d'ajout ne le demandait pas : la filière restait sans
département, et disparaissait des listes filtrées par département.
"""
import pytest
from rest_framework.test import APIClient

URL = '/api/v1/scolarite/filieres/'


@pytest.fixture
def admin(db):
    from apps.authentication.models import CustomUser
    c = APIClient()
    c.force_authenticate(CustomUser.objects.create_user(
        username='adm', email='adm@iss.mr', password='x', role='admin', is_superuser=True))
    return c


@pytest.fixture
def dept(db):
    from apps.scolarite.models import DepartementAcademique
    return DepartementAcademique.objects.create(code='STAT', intitule_fr='Statistique')


def donnees(**kw):
    return {'code': 'NOUV', 'intitule_fr': 'Nouvelle filière', 'type_diplome': 'LP',
            'nb_semestres': 6, 'niveau_debut': 1, 'niveau_fin': 3, 'credits_total': 180,
            'est_active': True, **kw}


class TestCreation:

    def test_sans_departement_refusee(self, admin):
        r = admin.post(URL, donnees(), format='json')
        assert r.status_code == 400
        assert 'departement_academique' in str(r.data)
        r = admin.post(URL, donnees(departement_academique=None), format='json')
        assert r.status_code == 400

    def test_avec_departement_acceptee(self, admin, dept):
        r = admin.post(URL, donnees(departement_academique=dept.pk), format='json')
        assert r.status_code == 201, r.data
        assert r.data['departement_academique'] == dept.pk


class TestModification:

    @pytest.fixture
    def filiere(self, dept):
        from apps.scolarite.models import Filiere
        return Filiere.objects.create(code='STAT', intitule_fr='Statistique', type_diplome='LP',
                                      nb_semestres=6, credits_total=180, departement_academique=dept)

    def test_on_ne_peut_pas_retirer_le_departement(self, admin, filiere):
        r = admin.patch(f'{URL}{filiere.pk}/', {'departement_academique': None}, format='json')
        assert r.status_code == 400
        filiere.refresh_from_db()
        assert filiere.departement_academique_id is not None

    def test_une_modification_qui_n_y_touche_pas_passe(self, admin, filiere):
        r = admin.patch(f'{URL}{filiere.pk}/', {'intitule_fr': 'Statistique appliquée'}, format='json')
        assert r.status_code == 200, r.data
