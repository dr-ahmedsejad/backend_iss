"""
Le backend d'authentification de SIGA.

Identique à `ModelBackend` sur le serveur de travail. Sur le miroir, le mot de
passe vérifié est celui qui FAIT FOI (apps/authentication/identifiants.py) :
un mot de passe changé en ligne survit à la publication qui réécrit la table
des comptes, et l'ancien est refusé.

Placé ici plutôt que dans core/authentication.py : la vérification des JETONS
n'est pas touchée. Seul le contrôle du mot de passe, à la connexion, change.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from core.mirror import est_miroir


class IdentifiantsBackend(ModelBackend):

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not est_miroir():
            return super().authenticate(request, username=username, password=password, **kwargs)

        from apps.authentication.identifiants import verifier_mot_de_passe

        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None
        try:
            user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            # Même coût qu'un vrai contrôle : ne pas révéler par le temps de
            # réponse qu'un nom de compte n'existe pas (comme ModelBackend).
            UserModel().set_password(password)
            return None
        if verifier_mot_de_passe(user, password) and self.user_can_authenticate(user):
            return user
        return None
