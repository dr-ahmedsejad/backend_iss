"""
Cache des consultations sur un VRAI PostgreSQL : une écriture en masse
(.update(), aucun signal Django) est vue grâce aux compteurs d'écriture de
PostgreSQL — sans rien simuler.

Lancement explicite seulement (PostgreSQL jetable, ex. Docker) :
    PORTAIL_CACHE_TEST_PG=1 pytest tests/test_cache_portail_pg.py
        --ds=siga.settings.test_pg --migrations --create-db
Vérifié le 10/10/2026 sur PostgreSQL 18 : 2 tests passent (le nettoyage de la
base jetable signale ensuite des tables gérées hors Django — sans rapport).
"""
import os
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection

from apps.portail import cache_portail

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != 'postgresql' or os.environ.get('PORTAIL_CACHE_TEST_PG') != '1',
                       reason='PostgreSQL jetable, lancement explicite'),
]


def _publier_compteurs():
    """PostgreSQL publie ses compteurs en fin de transaction (au plus ~1 s,
    10 s si la connexion reste inactive) : on force la publication."""
    with connection.cursor() as c:
        c.execute('SELECT pg_stat_force_next_flush()')
        c.execute('SELECT 1')


def test_ecriture_en_masse_change_l_empreinte(settings):
    from tests.factories.evaluations import ResultatElementFactory
    settings.PORTAIL_CACHE_SECONDES = 600
    cache.clear()
    r = ResultatElementFactory(note_finale=Decimal('8.00'))
    _publier_compteurs()
    avant = cache_portail.empreinte()
    assert avant is not None

    type(r).objects.filter(pk=r.pk).update(note_finale=Decimal('12.00'))   # aucun signal
    _publier_compteurs()
    assert cache_portail.empreinte() != avant


def test_table_exclue_ne_change_pas_l_empreinte(settings):
    from apps.notifications.models import Notification
    from tests.factories.auth import EtudiantUserFactory
    u = EtudiantUserFactory()
    _publier_compteurs()
    cache.clear()
    avant = cache_portail.empreinte().split('.')[0]
    Notification.objects.filter(pk__in=[]).update(lue=True)
    Notification.objects.bulk_create([Notification(destinataire=u, titre='x', message='y')])
    _publier_compteurs()
    assert cache_portail.empreinte().split('.')[0] == avant
