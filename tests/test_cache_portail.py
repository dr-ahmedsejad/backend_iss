"""
Cache des consultations étudiantes (apps/portail/cache_portail.py).

Ce qui compte : après une écriture, l'étudiant voit la NOUVELLE valeur.
  * enregistrement ordinaire (save) → signal → copie périmée sur-le-champ ;
  * écriture en masse (.update(), bulk) → invisible aux signaux, mais les
    compteurs d'écriture de PostgreSQL bougent : simulés ici (la base de test
    est SQLite, qui n'en a pas) ;
  * sans PostgreSQL ou cache coupé (0) : calcul direct, comme avant.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.portail import cache_portail
from tests.factories.auth import EtudiantUserFactory
from tests.factories.em import EMLegacyFactory
from tests.factories.evaluations import ResultatElementFactory, SessionNormaleImpairsFactory
from tests.factories.inscriptions import (InscriptionAdministrativeFactory,
                                          InscriptionElementFactory, InscriptionPedagogiqueFactory)
from tests.factories.parametres import SemestreFactory

pytestmark = pytest.mark.django_db
URL = '/api/v1/portail/notes/'


class CompteursPG:
    """Compteurs d'écriture PostgreSQL simulés."""
    def __init__(self):
        self.valeur = 1000

    def __call__(self):
        return self.valeur


@pytest.fixture
def pg(monkeypatch, settings):
    settings.PORTAIL_CACHE_SECONDES = 600
    cache.clear()
    compteurs = CompteursPG()
    monkeypatch.setattr(cache_portail, '_compteurs_postgres', compteurs)
    yield compteurs
    cache.clear()


@pytest.fixture
def etudiant():
    ia = InscriptionAdministrativeFactory()
    ip = InscriptionPedagogiqueFactory(inscription_admin=ia)
    el = InscriptionElementFactory(inscription_ped=ip, em=EMLegacyFactory(semestre=SemestreFactory()))
    resultat = ResultatElementFactory(inscription_element=el, session=SessionNormaleImpairsFactory(),
                                      note_finale=Decimal('8.00'), est_valide=False)
    e = ia.etudiant
    e.user = EtudiantUserFactory(username=f'etu_cache_{e.pk}')
    e.save(update_fields=['user'])
    client = APIClient()
    client.force_authenticate(e.user)
    return client, resultat


def _notes(client):
    with CaptureQueriesContext(connection) as q:
        r = client.get(URL)
    assert r.status_code == 200, r.data
    return [n['note_finale'] for n in r.data], len(q)


def test_deuxieme_consultation_servie_par_la_copie(pg, etudiant):
    client, _ = etudiant
    notes1, calcul = _notes(client)
    notes2, copie = _notes(client)
    assert notes1 == notes2 == [8.0]
    assert copie < calcul, f'copie : {copie} requêtes, calcul : {calcul}'


def test_enregistrement_ordinaire_visible_tout_de_suite(pg, etudiant):
    client, resultat = etudiant
    assert _notes(client)[0] == [8.0]
    resultat.note_finale = Decimal('14.00')
    resultat.save()                       # signal → empreinte changée
    assert _notes(client)[0] == [14.0]


def test_ecriture_en_masse_visible_via_les_compteurs_postgres(pg, etudiant):
    client, resultat = etudiant
    assert _notes(client)[0] == [8.0]
    type(resultat).objects.filter(pk=resultat.pk).update(note_finale=Decimal('11.00'))  # aucun signal
    pg.valeur += 1                        # PostgreSQL compte la ligne modifiée
    assert _notes(client)[0] == [11.0]


def test_une_notification_n_invalide_pas_les_copies(pg, etudiant):
    from apps.notifications.models import Notification
    client, _ = etudiant
    _notes(client)
    avant = cache_portail.empreinte()
    Notification.objects.create(destinataire=EtudiantUserFactory(), titre='x', message='y')
    assert cache_portail.empreinte() == avant


def test_cache_coupe(pg, etudiant, settings):
    settings.PORTAIL_CACHE_SECONDES = 0
    client, _ = etudiant
    _, a = _notes(client)
    _, b = _notes(client)
    assert a == b                          # recalculé à chaque fois


def test_sans_postgresql_pas_de_cache(etudiant, settings):
    settings.PORTAIL_CACHE_SECONDES = 600
    cache.clear()
    assert cache_portail.empreinte() is None   # SQLite
    client, _ = etudiant
    _, a = _notes(client)
    _, b = _notes(client)
    assert a == b


def test_une_erreur_n_est_jamais_gardee(pg):
    appels = []

    def calcul():
        appels.append(1)
        return Response({'detail': 'x'}, status=500)

    cache_portail.reponse_en_cache('essai', [1], calcul)
    cache_portail.reponse_en_cache('essai', [1], calcul)
    assert len(appels) == 2


def test_tables_exclues():
    assert cache_portail.table_exclue('token_blacklist_outstandingtoken')
    assert cache_portail.table_exclue('authentication_customuser')
    assert not cache_portail.table_exclue('evaluations_resultat_element')
    assert not cache_portail.table_exclue('suivi_suivie')


def test_emploi_du_temps_servi_par_la_copie(pg, etudiant):
    client, _ = etudiant
    r1 = client.get('/api/v1/portail/emploi-du-temps/')
    r2 = client.get('/api/v1/portail/emploi-du-temps/')
    assert r1.status_code == r2.status_code == 200
    assert r1.data == r2.data
