"""
Quel mot de passe fait foi — la règle, en un seul endroit.

Sur le MIROIR, deux sources se disputent le mot de passe d'un compte :

  * le mot de passe PUBLIÉ, dans la table des comptes, que chaque publication
    réécrit depuis le serveur de travail (mot de passe initial, ou
    réinitialisation faite par le personnel) ;
  * le mot de passe changé EN LIGNE, dans `IdentifiantPortail`, que la
    publication ne touche jamais.

La règle : le plus RÉCENT l'emporte. Une ligne en ligne plus récente que
`mdp_fixe_le` fait foi ; une réinitialisation faite sur le serveur de travail
APRÈS le changement en ligne la remplace. Un compte désactivé ou supprimé sur
le serveur de travail ne se connecte plus, quoi que dise la ligne : elle ne
ressuscite jamais un compte.

Sur le serveur de travail, rien de tout cela ne s'applique : le mot de passe
est celui du compte, comme avant.

TOUTES les vérifications passent par `verifier_mot_de_passe` : la connexion
(core/auth_backends.py), l'ancien mot de passe d'un changement
(ChangePasswordSerializer), et l'indicateur de premier accès
(`doit_changer_mdp`). Une vérification qui lirait `user.check_password`
directement, sur le miroir, accepterait l'ANCIEN mot de passe.
"""
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from core.mirror import est_miroir

# Les comptes dont le mot de passe vit sur le miroir. Le personnel change le
# sien sur le serveur de travail : sur le miroir, ce serait l'écrire là où le
# serveur de travail ne le verra jamais.
ROLES_DU_PORTAIL = ('etudiant', 'enseignant')


def _ligne_en_vigueur(user):
    """La ligne en ligne qui fait foi pour ce compte, ou None."""
    if not est_miroir() or user is None or user.pk is None:
        return None
    from apps.authentication.models import IdentifiantPortail
    ligne = IdentifiantPortail.objects.filter(user_id=user.pk).first()
    if ligne is None:
        return None
    fixe_le = getattr(user, 'mdp_fixe_le', None)
    if fixe_le is not None and fixe_le >= ligne.modifie_le:
        return None          # réinitialisé sur le serveur de travail APRÈS
    return ligne


def empreinte_en_vigueur(user) -> str:
    ligne = _ligne_en_vigueur(user)
    return ligne.password if ligne else user.password


def verifier_mot_de_passe(user, mot_de_passe) -> bool:
    """Le mot de passe saisi est-il celui qui fait foi ?"""
    if user is None or not mot_de_passe:
        return False
    if not est_miroir():
        return user.check_password(mot_de_passe)
    return check_password(mot_de_passe, empreinte_en_vigueur(user))


def doit_changer_mdp(user) -> bool:
    """Le premier accès reste-t-il à faire ?

    Une ligne en ligne qui fait foi, c'est un mot de passe choisi par
    l'utilisateur : le premier accès est fait, même si la publication a remis
    `doit_changer_mdp` à vrai dans la table des comptes.
    """
    if _ligne_en_vigueur(user) is not None:
        return False
    return bool(getattr(user, 'doit_changer_mdp', False))


def peut_changer_en_ligne(user) -> bool:
    return getattr(user, 'role', None) in ROLES_DU_PORTAIL


def enregistrer_en_ligne(user, mot_de_passe, origine, ip=None):
    """Range un mot de passe choisi sur le miroir — jamais dans la table des
    comptes, que la publication réécrit."""
    from apps.authentication.models import IdentifiantPortail
    ligne, _ = IdentifiantPortail.objects.update_or_create(
        user_id=user.pk,
        defaults={
            'username':   user.username,
            'password':   make_password(mot_de_passe),
            'origine':    origine,
            'modifie_le': timezone.now(),
            'ip_address': ip,
        },
    )
    return ligne
