"""
Pic de connexions depuis le campus : des centaines d'étudiants partagent UNE
adresse publique (Wi-Fi). Mesuré le 10/10/2026 :
- le renouvellement de session était limité à 5 par quart d'heure et par
  adresse : dès le 6e étudiant, l'app recevait 429 et le déconnectait ;
- 20 échecs de mot de passe depuis l'adresse, tous comptes confondus,
  bloquaient tout le campus 15 minutes.
"""
import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient

CAMPUS = '41.188.10.20'


@pytest.fixture(autouse=True)
def cache_vide():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_renouvellements_du_campus_jamais_limites():
    from apps.authentication.models import CustomUser
    from rest_framework_simplejwt.tokens import RefreshToken

    statuts = []
    for i in range(30):
        u = CustomUser.objects.create_user(
            username=f'etu{i}', email=f'etu{i}@iss.mr', password='x', role='etudiant')
        c = APIClient(REMOTE_ADDR='172.18.0.5', HTTP_X_REAL_IP=CAMPUS)
        c.cookies['refresh_token'] = str(RefreshToken.for_user(u))
        statuts.append(c.post('/api/v1/auth/token/refresh/').status_code)
    assert 429 not in statuts
    assert set(statuts) == {200}


def test_seuil_par_adresse_adapte_au_campus():
    assert settings.LOGIN_ECHECS_PAR_IP >= 300
