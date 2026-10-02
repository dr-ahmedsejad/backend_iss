"""
La période proposée sur la page de CONNEXION.

`/api/v1/parametres/semaines/actif/` est lu avant toute authentification, pour
pré-cocher l'année et le semestre. Mal choisi, il fait saisir dans l'année
révolue — et l'erreur ne se voit qu'au pointage.

Le défaut mesuré le 02/10/2026 : la règle prenait toujours la dernière semaine
PASSÉE. Elle proposait donc le semestre pair de 2025-2026, terminé depuis
104 jours, alors que l'impair de 2026-2027 commençait 3 jours plus tard.

Les dates sont posées en RELATIF par rapport à aujourd'hui : figées, ces tests
passeraient le jour où on les écrit puis tomberaient d'eux-mêmes.
"""
import datetime as dt

import pytest
from rest_framework.test import APIClient

from tests.factories.parametres import JourFactory

URL = '/api/v1/parametres/semaines/actif/'


@pytest.fixture
def jour(db):
    return JourFactory(jour='Lundi')


def semaine(jour, dans_jours, annee, type_semestre, numero=1):
    """Une ligne-jour à `dans_jours` d'aujourd'hui (négatif = passé)."""
    from apps.parametres.models import Semaine
    return Semaine.objects.create(
        numero_semaine=numero, jour_fk=jour,
        date=dt.date.today() + dt.timedelta(days=dans_jours),
        annee_universitaire=annee, type_semestre=type_semestre,
        type_semaine='cours')


def proposee():
    return APIClient().get(URL).data


class TestChoix:

    def test_le_jour_meme_l_emporte_sur_tout(self, jour):
        semaine(jour, 0,    '2026-2027', 'I')
        semaine(jour, -1,   '2025-2026', 'P')
        semaine(jour, +1,   '2027-2028', 'P')
        r = proposee()
        assert (r['type_semestre'], r['annee_universitaire']) == ('I', '2026-2027')
        assert r['source'] == 'exact'

    def test_hors_periode_la_plus_proche_gagne_meme_si_elle_est_a_venir(self, jour):
        """Le cas de production : 104 jours en arrière contre 3 jours en avant."""
        semaine(jour, -104, '2025-2026', 'P', numero=18)
        semaine(jour, +3,   '2026-2027', 'I', numero=1)
        r = proposee()
        assert (r['type_semestre'], r['annee_universitaire']) == ('I', '2026-2027')
        assert r['numero_semaine'] == 1

    def test_une_periode_a_peine_terminee_reste_proposee(self, jour):
        """L'inverse doit tenir aussi : deux jours après la fin d'un semestre,
        c'est lui qu'on rouvre, pas celui qui commence dans trois mois."""
        semaine(jour, -2,  '2026-2027', 'I', numero=16)
        semaine(jour, +90, '2026-2027', 'P', numero=1)
        r = proposee()
        assert (r['type_semestre'], r['numero_semaine']) == ('I', 16)

    def test_a_egale_distance_c_est_celle_qui_vient(self, jour):
        """Entre un semestre qu'on termine et un qu'on entame, on ouvre le second."""
        semaine(jour, -7, '2026-2027', 'I', numero=16)
        semaine(jour, +7, '2026-2027', 'P', numero=1)
        assert proposee()['type_semestre'] == 'P'


class TestBords:

    def test_sans_aucune_semaine_le_defaut_est_impair(self, db):
        r = proposee()
        assert (r['type_semestre'], r['annee_universitaire'], r['source']) == ('I', '', 'default')

    def test_avec_le_passe_seul_on_prend_le_passe(self, jour):
        semaine(jour, -30, '2025-2026', 'P', numero=18)
        assert proposee()['annee_universitaire'] == '2025-2026'

    def test_avec_l_avenir_seul_on_prend_l_avenir(self, jour):
        semaine(jour, +30, '2026-2027', 'I', numero=1)
        assert proposee()['annee_universitaire'] == '2026-2027'

    def test_l_adresse_reste_lisible_sans_connexion(self, jour):
        """La page de connexion l'appelle AVANT d'avoir un compte."""
        semaine(jour, 0, '2026-2027', 'I')
        assert APIClient().get(URL).status_code == 200
