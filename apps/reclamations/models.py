from django.db import models

from core.validators import validate_document


TYPE_CHOICES = [
    ('absence', 'Absence'),
    ('note',    'Note'),
    ('autre',   'Autre'),
]

STATUT_CHOICES = [
    ('soumise',  'Soumise'),
    ('en_cours', 'En cours'),
    ('acceptee', 'Acceptée'),
    ('rejetee',  'Rejetée'),
]


class Reclamation(models.Model):
    """Réclamation d'un ÉTUDIANT sur une note ou une absence — boîte de réception.

    Écrite EN LIGNE, sur le miroir. La publication remplace tout le reste de la
    base du miroir : cette table en est exclue TOTALEMENT
    (`settings.BOITE_DE_RECEPTION`), et ses lignes survivent.

    D'où l'absence de toute clé étrangère. Avec une contrainte vers une table
    publiée, le restore ÉCHOUE (pg_dump --clean ne droppe jamais en cascade :
    « n'a pas pu supprimer contrainte … car d'autres objets en dépendent »).
    Sans contrainte mais avec une relation Django, l'affichage CASSE dès que la
    cible a disparu — l'étudiant ou l'inscription peuvent être supprimés ou
    régénérés sur le serveur de travail entre deux publications.

    Donc : des identifiants BRUTS, et un INSTANTANÉ lisible figé au dépôt (nom,
    matricule, élément). Les identifiants sont quand même vérifiés contre la
    base au dépôt — on ne réclame pas sur une note qui n'existe pas.

    Une réclamation ne CORRIGE rien : elle informe. La correction se fait sur
    le serveur de travail et redescend à la publication suivante.

    Gardé par tests/test_miroir_invariant.py et tests/test_miroir_boite.py.
    Rendue sans clé étrangère par reclamations/0004, sans perdre une ligne.
    """
    # ── L'étudiant ─────────────────────────────────────────────────────────
    etudiant_id          = models.BigIntegerField(db_index=True)
    etudiant_nom         = models.CharField(max_length=200, blank=True, default='')
    etudiant_matricule   = models.CharField(max_length=50, blank=True, default='')

    type_reclamation     = models.CharField(max_length=20, choices=TYPE_CHOICES, default='autre')
    statut               = models.CharField(max_length=20, choices=STATUT_CHOICES, default='soumise')

    # ── Ce qui est réclamé — identifiants bruts, selon le type ─────────────
    presence_id            = models.BigIntegerField(null=True, blank=True, db_index=True)
    inscription_element_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    session_evaluation_id  = models.BigIntegerField(null=True, blank=True, db_index=True)
    # L'élément : il dit à quel enseignant la réclamation est montrée.
    em_id                  = models.BigIntegerField(null=True, blank=True, db_index=True)
    em_code                = models.CharField(max_length=50, blank=True, default='')
    em_intitule            = models.CharField(max_length=200, blank=True, default='')

    motif                = models.TextField()
    justificatif         = models.FileField(upload_to='reclamations/justificatifs/', null=True, blank=True,
                                             validators=[validate_document])

    # ── Traitement ─────────────────────────────────────────────────────────
    reponse              = models.TextField(blank=True, default='')
    traitee_par_id       = models.BigIntegerField(null=True, blank=True, db_index=True)
    traitee_par_nom      = models.CharField(max_length=150, blank=True, default='')
    date_soumission      = models.DateTimeField(auto_now_add=True)
    date_traitement      = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'reclamations_reclamation'
        ordering = ['-date_soumission']

    def __str__(self):
        return f'{self.etudiant_matricule or self.etudiant_id} — {self.type_reclamation} ({self.statut})'


STATUT_SEANCE_CHOICES = [
    ('en_attente', 'En attente'),
    ('acceptee',   'Acceptée'),
    ('rejetee',    'Rejetée'),
]


class ReclamationSeance(models.Model):
    """Réclamation d'un ENSEIGNANT sur une séance pointée — boîte de réception.

    Elle vivait dans deux champs du pointage (`suivi_suivie_pointage`), une
    table PUBLIÉE : sur le miroir, chaque publication l'aurait effacée. Les
    champs restent en place, inutilisés — `apps/suivi/` n'est pas modifié.

    Mêmes règles que `Reclamation` : aucune clé étrangère, identifiants bruts,
    instantané figé au dépôt. Elle n'ajuste ni le pointage, ni la charge, ni la
    paie : la décision se reporte à la main sur le serveur de travail.
    """
    pointage_id     = models.BigIntegerField(db_index=True)
    prof_id         = models.BigIntegerField(db_index=True)
    prof_nom        = models.CharField(max_length=200, blank=True, default='')
    # ── Instantané de la séance ────────────────────────────────────────────
    annee_universitaire = models.CharField(max_length=20, blank=True, default='')
    numero_semaine  = models.IntegerField(null=True, blank=True)
    jour            = models.CharField(max_length=20, blank=True, default='')
    creneau         = models.CharField(max_length=50, blank=True, default='')
    type_seance     = models.CharField(max_length=50, blank=True, default='')
    em_id           = models.BigIntegerField(null=True, blank=True)
    em_code         = models.CharField(max_length=50, blank=True, default='')
    em_intitule     = models.CharField(max_length=200, blank=True, default='')
    salle_nom       = models.CharField(max_length=100, blank=True, default='')
    groupes         = models.CharField(max_length=500, blank=True, default='')

    motif           = models.TextField()
    statut          = models.CharField(max_length=20, choices=STATUT_SEANCE_CHOICES, default='en_attente')
    reponse         = models.TextField(blank=True, default='')
    traitee_par_id  = models.BigIntegerField(null=True, blank=True)
    traitee_par_nom = models.CharField(max_length=150, blank=True, default='')
    date_soumission = models.DateTimeField(auto_now_add=True)
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'reclamations_reclamation_seance'
        ordering = ['-date_soumission']

    def __str__(self):
        return f'séance #{self.pointage_id} — {self.prof_nom} ({self.statut})'


TYPE_SESSION_CHOICES = [
    ('normale',    'Normale (SN)'),
    ('rattrapage', 'Rattrapage (SR)'),
]

TYPE_SEMESTRE_CHOICES = [
    ('I', 'Impairs (S1 / S3 / S5)'),
    ('P', 'Pairs (S2 / S4 / S6)'),
]


class PeriodeReclamation(models.Model):
    """
    Fenetre temporelle pendant laquelle les etudiants peuvent reclamer
    sur leurs notes pour un perimetre (annee + parite + session) donne.
    Cree par la SG apres cloture d'une session SR pour donner X jours
    aux etudiants pour formuler des reclamations sur leurs notes finales.
    """
    annee_univ      = models.ForeignKey(
        'parametres.Year', on_delete=models.PROTECT,
        related_name='periodes_reclamation',
    )
    type_session    = models.CharField(max_length=20, choices=TYPE_SESSION_CHOICES, default='rattrapage')
    type_semestre   = models.CharField(max_length=1, choices=TYPE_SEMESTRE_CHOICES)
    institution     = models.ForeignKey(
        'parametres.Institution', on_delete=models.PROTECT,
        related_name='periodes_reclamation',
    )
    filiere         = models.ForeignKey(
        'scolarite.Filiere', on_delete=models.PROTECT,
        null=True, blank=True, related_name='periodes_reclamation',
        help_text="Si vide : toutes les filieres de l'institution.",
    )
    niveau          = models.IntegerField(
        null=True, blank=True,
        help_text="Si vide : tous les niveaux.",
    )

    date_ouverture  = models.DateTimeField()
    date_fermeture  = models.DateTimeField()
    actif           = models.BooleanField(default=True)

    cree_par        = models.ForeignKey(
        'authentication.CustomUser', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='periodes_reclamation_creees',
    )
    motif           = models.CharField(
        max_length=200, blank=True, default='',
        help_text="Texte court affiche aux etudiants (ex : 'Reclamation SR-I 2025-2026').",
    )
    date_creation     = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reclamations_periode'
        ordering = ['-date_ouverture']
        indexes = [
            models.Index(fields=['actif', 'date_ouverture', 'date_fermeture']),
            models.Index(fields=['annee_univ', 'type_semestre']),
        ]

    def __str__(self):
        return (
            f'Periode {self.get_type_session_display()} {self.get_type_semestre_display()} '
            f'{self.annee_univ} — {self.date_ouverture:%d/%m %H:%M} → {self.date_fermeture:%d/%m %H:%M}'
        )

    @property
    def est_en_cours(self):
        from django.utils import timezone
        now = timezone.now()
        return self.actif and self.date_ouverture <= now <= self.date_fermeture

    @property
    def statut_temporel(self):
        """'a_venir' | 'en_cours' | 'fermee' | 'inactive'"""
        if not self.actif:
            return 'inactive'
        from django.utils import timezone
        now = timezone.now()
        if now < self.date_ouverture:
            return 'a_venir'
        if now > self.date_fermeture:
            return 'fermee'
        return 'en_cours'
