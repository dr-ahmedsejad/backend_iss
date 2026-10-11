from django.db import models


TYPE_NOTIFICATION_CHOICES = [
    ('info',           'Info'),
    ('succes',         'Succès'),
    ('avertissement',  'Avertissement'),
    ('erreur',         'Erreur'),
    ('preinscription', 'Pré-inscription'),
    ('inscription',    'Inscription'),
    ('evaluation',     'Évaluation'),
    ('document',       'Document'),
    ('stage',          'Stage'),
    ('annonce',        'Annonce'),
]


class Notification(models.Model):
    destinataire    = models.ForeignKey(
        'authentication.CustomUser', on_delete=models.CASCADE,
        related_name='notifications',
    )
    titre           = models.CharField(max_length=200)
    message         = models.TextField()
    type            = models.CharField(max_length=20, choices=TYPE_NOTIFICATION_CHOICES, default='info')
    lue             = models.BooleanField(default=False)
    lien            = models.CharField(max_length=500, blank=True, default='')
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications_notification'
        ordering = ['-created_at']
        indexes = [
            # Cloche : notifications non lues d'un utilisateur.
            models.Index(fields=['destinataire', 'lue'], name='notif_dest_lue_idx'),
            # envoyer_push : notifications récentes.
            models.Index(fields=['created_at'], name='notif_created_idx'),
        ]

    def __str__(self):
        return f'[{self.type}] {self.titre} → {self.destinataire}'


class NotificationLecture(models.Model):
    """Une notification lue SUR LE MIROIR — boîte de réception.

    `Notification.lue` vit dans une table publiée : sur le miroir, chaque
    publication la remettrait à « non lue ». La lecture faite en ligne est donc
    rangée ici, hors publication, et l'écran fusionne les deux (voir
    NotificationViewSet). Aucune clé étrangère : identifiants bruts. Gardé par
    tests/test_miroir_invariant.py.
    """
    notification_id = models.BigIntegerField(db_index=True)
    user_id         = models.BigIntegerField(db_index=True)
    lue_le          = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications_lecture'
        constraints = [
            models.UniqueConstraint(fields=['notification_id', 'user_id'],
                                    name='notifications_lecture_unique'),
        ]


class AppareilPush(models.Model):
    """Téléphone d'un utilisateur inscrit aux notifications push (jeton FCM).

    L'application étudiante ISS (`mr.iss.etudiant`) s'inscrit ici à chaque
    connexion — POST /notifications/appareils/. Jusqu'au 05/10/2026 l'adresse
    n'existait pas côté ISS : l'app recevait un 404, aucun téléphone n'était
    connu, aucun push ne pouvait partir.

    Repris du SIGA-PRIVE. Écrit EN LIGNE (sur le miroir, l'app s'inscrit
    auprès de lui) : boîte de réception, sans clé étrangère, exclue de la
    publication. Un jeton = un appareil ; s'il change de compte, il le suit.
    """
    user_id    = models.BigIntegerField(db_index=True)
    jeton      = models.CharField(max_length=512, unique=True)
    plateforme = models.CharField(max_length=20, default='android')
    langue     = models.CharField(max_length=5, default='fr')
    # App d'où vient le jeton : '' = anciennes apps ISS (étudiant, enseignant :
    # la clé suit le rôle) ; 'gp' = app Groupe Polytechnique (sa propre clé).
    projet     = models.CharField(max_length=20, blank=True, default='')
    cree_le    = models.DateTimeField(auto_now_add=True)
    vu_le      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notifications_appareil'

    def __str__(self):
        return f'appareil {self.plateforme} de #{self.user_id}'


class PushEnvoye(models.Model):
    """Notification déjà traitée par `envoyer_push` (poussée, ou sans appareil).

    Clé : (identifiant, date de création), et non l'identifiant seul : sur le
    miroir, une notification créée en ligne puis effacée par la publication
    peut voir son identifiant repris par une autre. Boîte de réception.
    """
    notification_id = models.BigIntegerField()
    notification_le = models.DateTimeField()
    nb_appareils    = models.PositiveIntegerField(default=0)
    traite_le       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications_push_envoye'
        indexes = [
            # envoyer_push : déjà traitées depuis…
            models.Index(fields=['notification_le'], name='push_envoye_le_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['notification_id', 'notification_le'],
                                    name='notifications_push_envoye_unique'),
        ]

    def __str__(self):
        return f'push de la notification #{self.notification_id}'
