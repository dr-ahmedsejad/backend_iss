"""Portail : revoir une année passée.


marquée « courante ») ; `?annee=` restreint les notes à l'année choisie.
Les notes gardent ici leur forme de liste (portail web de l'ISS).
"""
import pytest
from rest_framework.test import APIClient

from tests.factories.auth import EtudiantUserFactory
from tests.factories.inscriptions import (
    InscriptionAdministrativeFactory, InscriptionElementFactory, InscriptionPedagogiqueFactory,
)
from tests.factories.evaluations import NoteFactory
from tests.factories.parametres import YearFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def deux_annees():
    ancienne = InscriptionAdministrativeFactory(annee_univ=YearFactory(annee='2024-2025', est_active=False))
    etudiant = ancienne.etudiant
    recente = InscriptionAdministrativeFactory(etudiant=etudiant, niveau=2,
                                               annee_univ=YearFactory(annee='2025-2026'))
    for ia in (ancienne, recente):
        InscriptionElementFactory(inscription_ped=InscriptionPedagogiqueFactory(inscription_admin=ia))
    etudiant.user = EtudiantUserFactory(username=f'etu_{etudiant.pk}')
    etudiant.save(update_fields=['user'])
    client = APIClient()
    client.force_authenticate(etudiant.user)
    return client


def test_annees_de_la_plus_recente_a_la_plus_ancienne(deux_annees):
    r = deux_annees.get('/api/v1/portail/annees/')
    assert r.status_code == 200
    assert [(a['annee'], a['niveau'], a['courante']) for a in r.data] == [
        ('2025-2026', 2, True), ('2024-2025', 1, False)]


def test_notes_filtrees_par_annee(deux_annees):
    toutes = deux_annees.get('/api/v1/portail/notes/')
    assert toutes.status_code == 200, toutes.data
    assert {n['annee_univ'] for n in toutes.data} == {'2024-2025', '2025-2026'}
    passee = deux_annees.get('/api/v1/portail/notes/', {'annee': '2024-2025'})
    assert {n['annee_univ'] for n in passee.data} == {'2024-2025'}


def test_notes_portent_leur_date(deux_annees):
    from apps.evaluations.models import Note
    from apps.inscriptions.models import InscriptionElement
    el = InscriptionElement.objects.filter(inscription_ped__inscription_admin__annee_univ__annee='2025-2026').get()
    assert deux_annees.get('/api/v1/portail/notes/', {'annee': '2025-2026'}).data[0]['date_note'] is None
    NoteFactory(inscription_element=el)
    n = deux_annees.get('/api/v1/portail/notes/', {'annee': '2025-2026'}).data[0]
    assert n['date_note'] == Note.objects.get().date_modification.isoformat()
