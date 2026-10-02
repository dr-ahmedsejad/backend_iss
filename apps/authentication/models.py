import os
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


def validate_avatar(value):
    """Valide la taille (5 Mo max) et le format (JPEG/PNG) de l'avatar uploadé."""
    max_size_bytes = 5 * 1024 * 1024  # 5 Mo
    allowed_extensions = {'.jpg', '.jpeg', '.png'}
    allowed_content_types = {'image/jpeg', 'image/png'}

    if value.size > max_size_bytes:
        mb = value.size // (1024 * 1024)
        raise ValidationError(
            f"La taille de l'avatar ne peut pas dépasser 5 Mo (reçu : {mb} Mo)."
        )

    ext = os.path.splitext(value.name)[1].lower()
    if ext not in allowed_extensions:
        raise ValidationError(
            "Format non supporté. Seuls les fichiers JPEG et PNG sont acceptés."
        )

    # Vérification du Content-Type déclaré par le navigateur (disponible sur InMemoryUploadedFile)
    content_type = getattr(value, 'content_type', None)
    if content_type and content_type not in allowed_content_types:
        raise ValidationError(
            "Le fichier doit être une image JPEG ou PNG valide."
        )


ROLE_CHOICES = [
    ('admin',                'Administrateur'),
    ('DG',                   'Directeur général'),
    ('DA',                   'Direction administrative'),
    ('DE',                   'Direction enseignement'),
    ('AA',                   'Assistant administratif'),
    ('IT',                   'Informatique'),
    ('scolarite',            'Scolarité'),
    ('responsable_filiere',  'Responsable de filière'),
    ('jury_president',       'Président de jury'),
    ('etudiant',             'Étudiant'),
    ('enseignant',           'Enseignant'),
]


class CustomUser(AbstractUser):
    email            = models.EmailField(unique=True)
    role             = models.CharField(max_length=20, choices=ROLE_CHOICES, default='AA')
    name             = models.CharField(max_length=150, blank=True)
    avatar           = models.ImageField(upload_to='avatars/', null=True, blank=True, validators=[validate_avatar])
    doit_changer_mdp = models.BooleanField(default=False)
    # Date du dernier mot de passe fixé sur le SERVEUR DE TRAVAIL (création du
    # compte, réinitialisation par le personnel). PUBLIÉE vers le miroir, où
    # elle tranche avec les mots de passe changés en ligne : le plus récent
    # l'emporte. Vide pour les comptes antérieurs : le changement en ligne
    # l'emporte alors toujours. Voir apps/authentication/identifiants.py.
    mdp_fixe_le      = models.DateTimeField(null=True, blank=True)

    # Delegation EDT : groupes que ce user peut gerer (emplois/suivi/vacation).
    # Signal unique pour autoriser la gestion EDT d'un departement, independant
    # du role. Admin/superuser passent outre. Vide = aucune delegation.
    managed_departements = models.ManyToManyField(
        'departement.Departement',
        blank=True,
        related_name='edt_managers',
        db_table='authentication_user_managed_departements',
        help_text="Groupes que ce user peut gerer (EDT/suivi/vacation). Vide = aucune delegation.",
    )

    class Meta:
        db_table = 'authentication_customuser'

    def __str__(self):
        return f'{self.username} ({self.role})'

    def set_password(self, raw_password):
        """Fixe le mot de passe ET date le geste — voir `mdp_fixe_le`.

        Sur le miroir, un mot de passe de portail ne passe jamais par ici : il
        va dans `IdentifiantPortail`. Ce qui passe ici est fixé sur le serveur
        de travail, et doit l'emporter sur un changement en ligne plus ancien.
        """
        from django.utils import timezone
        super().set_password(raw_password)
        self.mdp_fixe_le = timezone.now()


class Module(models.Model):
    code  = models.CharField(max_length=50, unique=True)
    nom   = models.CharField(max_length=100)
    icone = models.CharField(max_length=50, blank=True)
    ordre = models.IntegerField(default=0)

    class Meta:
        db_table = 'authentication_module'
        ordering = ['ordre']

    def __str__(self):
        return self.code


class Action(models.Model):
    code  = models.CharField(max_length=50, unique=True)
    nom   = models.CharField(max_length=100)
    icone = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = 'authentication_action'

    def __str__(self):
        return self.code


class ModuleAction(models.Model):
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='actions')
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name='modules')

    class Meta:
        db_table = 'authentication_moduleaction'
        unique_together = ('module', 'action')

    def __str__(self):
        return f'{self.module.code}:{self.action.code}'


class RoleDefault(models.Model):
    role          = models.CharField(max_length=20, choices=ROLE_CHOICES)
    module_action = models.ForeignKey(ModuleAction, on_delete=models.CASCADE, related_name='role_defaults')
    allowed       = models.BooleanField(default=False)

    class Meta:
        db_table = 'authentication_roledefault'
        unique_together = ('role', 'module_action')


class UserPermission(models.Model):
    user          = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='permissions_rbac')
    module_action = models.ForeignKey(ModuleAction, on_delete=models.CASCADE)
    allowed       = models.BooleanField(default=False)
    departement   = models.ForeignKey(
        'departement.Departement', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='user_permissions_rbac',
    )
    filiere       = models.ForeignKey(
        'scolarite.Filiere', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='user_permissions',
        help_text="Périmètre filière pour responsable_filiere / jury_president.",
    )

    class Meta:
        db_table = 'authentication_userpermission'
        unique_together = ('user', 'module_action')


SEMESTRE_CHOICES = [('Pairs', 'Pairs'), ('Impairs', 'Impairs')]


class UserContexte(models.Model):
    """Contexte de session persisté par utilisateur : année universitaire et semestre actifs.
    Découplé de l'authentification JWT pour permettre la mise à jour sans re-connexion.
    Créé automatiquement au premier login et mis à jour à chaque sélection."""
    user                = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE, related_name='contexte',
    )
    annee_universitaire = models.CharField(max_length=9, blank=True, default='')
    semestre            = models.CharField(max_length=10, choices=SEMESTRE_CHOICES, default='Pairs')
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'authentication_usercontexte'

    def __str__(self):
        return f'{self.user.username} — {self.annee_universitaire} / {self.semestre}'


class IdentifiantPortail(models.Model):
    """Un mot de passe changé SUR LE MIROIR — boîte de réception.

    Le danger qu'elle écarte : la publication remplace la table des comptes du
    miroir par celle du serveur de travail. Un mot de passe changé en ligne y
    serait remis à l'ancien, et un mot de passe initial — souvent distribué
    sur papier, donc connu d'autres — redeviendrait valable, premier accès
    compris. Rangé ici, il ne passe jamais par la publication.

    AUCUNE clé étrangère : `user_id` est un identifiant brut. La table est en
    exclusion TOTALE (`settings.BOITE_DE_RECEPTION`) ; une contrainte vers les
    comptes ferait échouer le restore (pg_dump --clean ne droppe pas en
    cascade). Gardé par tests/test_miroir_invariant.py.

    La règle de lecture — qui, de la ligne ou du mot de passe publié, fait
    foi — est dans apps/authentication/identifiants.py, et nulle part ailleurs.
    """
    ORIGINE_PREMIER_ACCES = 'premier_acces'
    ORIGINE_CHANGEMENT    = 'changement'
    ORIGINES = [
        (ORIGINE_PREMIER_ACCES, 'Premier accès'),
        (ORIGINE_CHANGEMENT,    'Changement de mot de passe'),
    ]

    user_id    = models.BigIntegerField(unique=True)
    # Instantané lisible : le nom du compte au moment du changement.
    username   = models.CharField(max_length=150, blank=True, default='')
    # L'EMPREINTE (algorithme de Django), jamais le mot de passe en clair.
    password   = models.CharField(max_length=128)
    origine    = models.CharField(max_length=20, choices=ORIGINES)
    modifie_le = models.DateTimeField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = 'portail_identifiant'

    def __str__(self):
        return f'identifiant en ligne #{self.user_id} ({self.modifie_le:%Y-%m-%d %H:%M})'
