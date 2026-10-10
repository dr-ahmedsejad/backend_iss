"""
Consultation des notes (portail) : le nombre de requêtes ne dépend plus du
nombre d'éléments (correction du N+1, 10/10/2026).

Mesuré avant : 321 requêtes pour un étudiant de 81 éléments — trois par
élément (son dernier résultat relu malgré le préchargement, l'étudiant, le
semestre de l'EM). La réponse, elle, ne change pas : vérifié sur les 146
étudiants de la copie locale (91 008 valeurs identiques avant / après).
"""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from tests.factories.auth import EtudiantUserFactory
from tests.factories.em import EMLegacyFactory
from tests.factories.evaluations import (ResultatElementFactory, SessionNormaleImpairsFactory,
                                         SessionRattrapageImpairsFactory)
from tests.factories.inscriptions import (InscriptionAdministrativeFactory,
                                          InscriptionElementFactory, InscriptionPedagogiqueFactory)
from tests.factories.parametres import SemestreFactory

pytestmark = pytest.mark.django_db

URL = '/api/v1/portail/notes/'


@pytest.fixture
def sessions():
    """Une session normale et un rattrapage, partagés par les étudiants du test."""
    return SessionNormaleImpairsFactory(), SessionRattrapageImpairsFactory()


def _etudiant_avec(nb_elements, sessions):
    """Un étudiant, nb_elements éléments du même semestre, chacun avec deux
    résultats : la session normale (8) puis le rattrapage (13), créé après."""
    ia = InscriptionAdministrativeFactory()
    ip = InscriptionPedagogiqueFactory(inscription_admin=ia)
    semestre = SemestreFactory()
    normale, rattrapage = sessions
    for _ in range(nb_elements):
        el = InscriptionElementFactory(inscription_ped=ip, em=EMLegacyFactory(semestre=semestre))
        ResultatElementFactory(inscription_element=el, session=normale, note_finale=Decimal('8.00'),
                               est_valide=False)
        ResultatElementFactory(inscription_element=el, session=rattrapage, note_finale=Decimal('13.00'),
                               est_valide=True)
    etudiant = ia.etudiant
    etudiant.user = EtudiantUserFactory(username=f'etu_{etudiant.pk}')
    etudiant.save(update_fields=['user'])
    client = APIClient()
    client.force_authenticate(etudiant.user)
    return client


def _consulter(client):
    with CaptureQueriesContext(connection) as requetes:
        r = client.get(URL)
    assert r.status_code == 200, r.data
    return r.data, len(requetes)


def test_la_note_finale_est_celle_du_dernier_resultat(sessions):
    donnees, _ = _consulter(_etudiant_avec(2, sessions))
    assert [n['note_finale'] for n in donnees] == [13.0, 13.0]


def test_le_nombre_de_requetes_ne_croit_pas_avec_les_elements(sessions):
    _, peu = _consulter(_etudiant_avec(2, sessions))
    _, beaucoup = _consulter(_etudiant_avec(8, sessions))
    assert beaucoup == peu, f'{peu} requêtes pour 2 éléments, {beaucoup} pour 8'
