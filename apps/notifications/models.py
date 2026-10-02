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
