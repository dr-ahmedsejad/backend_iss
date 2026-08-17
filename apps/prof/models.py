from django.db import models
from django.conf import settings

from core.validators import validate_document

GRADE_CHOICES = [
    # Civils
    ('Professeur', 'Professeur'),
    ('Maitre de conférences A', 'Maitre de conférences A'),
    ('Maitre de conférences B', 'Maitre de conférences B'),
    ('Maitre assistant A', 'Maitre assistant A'),
    ('Maitre assistant B', 'Maitre assistant B'),
    ('Assistant', 'Assistant'),
    ('Vacataire', 'Vacataire'),
    ('Ingénieur', 'Ingénieur'),
    ('Autre', 'Autre'),
    # Militaires (type = 'militaire')
    ('Lieutenant',          'Lieutenant'),
    ('Capitaine',           'Capitaine'),
    ('Commandant',          'Commandant'),
    ('Lieutenant Colonel',  'Lieutenant Colonel'),
    ('Colonel',             'Colonel'),
]

# Sous-ensemble des grades reserves aux enseignants militaires (filtre cote UI).
GRADES_MILITAIRES = [
    'Lieutenant', 'Capitaine', 'Commandant', 'Lieutenant Colonel', 'Colonel',
]

DIPLOME_CHOICES = [
    ('Master', 'Master'),
    ('Ingénieur', 'Ingénieur'),
    ('Doctorat', 'Doctorat'),
    ('Autre', 'Autre'),
]

TYPE_CHOICES = [
    # Enseignants (charge pedagogique + vacation eligibles)
    ('vacataire',   'Vacataire'),
    ('permanent',   'Permanent'),
    ('contractuel', 'Contractuel'),
    ('militaire',   'Enseignant militaire'),
    # Personnel non-enseignant (surveillance / encadrement / mission via vacation seulement)
    ('personnel_militaire', 'Personnel militaire'),
    ('personnel_admin',     'Personnel administratif'),
]

# Sous-ensemble des types qui dispensent un enseignement (utilise pour filtrer
# les autocompletes d'attribution d'EM, emplois, charge enseignante...).
TYPES_ENSEIGNANTS = ['vacataire', 'permanent', 'contractuel', 'militaire']
# Sous-ensemble des types a charge reglementaire (avancement / charge annuelle).
TYPES_CHARGE_REG  = ['permanent', 'contractuel', 'militaire']

GENRE_CHOICES = [('M', 'Masculin'), ('F', 'Féminin')]


class Prof(models.Model):
    NNI                         = models.BigIntegerField(unique=True)
    nom                         = models.CharField(max_length=200)
    user                        = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='prof_profile',
    )
    telephone                   = models.BigIntegerField(null=True, blank=True, db_column='téléphone')
    email                       = models.EmailField(blank=True, default='')
    genre                       = models.CharField(max_length=1, choices=GENRE_CHOICES, default='M')
    type                        = models.CharField(max_length=20, choices=TYPE_CHOICES, default='vacataire')
    niveau_de_diplome           = models.CharField(max_length=20, choices=DIPLOME_CHOICES, default='Master', db_column='niveau_de_diplôme')
    description_dernier_diplome = models.CharField(max_length=200, blank=True, default='', db_column='description_dernier_diplôme')
    banque                      = models.ForeignKey('banque.Banque', on_delete=models.SET_NULL, null=True, blank=True, related_name='profs')
    numero_de_compte            = models.CharField(max_length=100, blank=True, default='', db_column='numéro_de_compte')
    cv                          = models.FileField(upload_to='cvs/', null=True, blank=True,
                                                   validators=[validate_document])
    diplome                     = models.FileField(upload_to='diplomes/', null=True, blank=True,
                                                   validators=[validate_document])
    grade                       = models.CharField(max_length=100, choices=GRADE_CHOICES, blank=True, null=True)
    charge                      = models.PositiveIntegerField(null=True, blank=True)
    decharge                    = models.PositiveIntegerField(default=0, db_column='décharge')
    # Archivage : alternative à la suppression. Un prof ayant des vacations /
    # surveillances / charges (données de paie protégées en PROTECT) ne doit pas
    # être supprimé mais désactivé. L'UI filtre sur actif=True par défaut.
    actif                       = models.BooleanField(
        default=True,
        help_text="Prof actif. Décocher pour archiver au lieu de supprimer.",
    )

    class Meta:
        db_table = 'prof'
        ordering = ['nom']

    def __str__(self):
        return self.nom


class ProfTypeHistory(models.Model):
    """Historique des changements de statut d'un prof (vacataire/contractuel/permanent).
    La table est créée et peuplée hors-Django (cf. backfill SQL 2026-05-03).
    `managed = False` : Django ne tente jamais de migrer/altérer cette table.
    """
    prof       = models.ForeignKey(Prof, on_delete=models.CASCADE,
                                    related_name='type_history', db_column='prof_id')
    type       = models.CharField(max_length=50, choices=TYPE_CHOICES)
    date_debut = models.DateField()
    date_fin   = models.DateField(null=True, blank=True)
    motif      = models.TextField(blank=True, default='')
    cree_par   = models.CharField(max_length=100, blank=True, default='')
    cree_le    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'prof_type_history'
        managed         = False
        ordering        = ['prof_id', '-date_debut']
        verbose_name        = 'Historique statut prof'
        verbose_name_plural = 'Historique des statuts prof'

    def __str__(self):
        return f'{self.prof_id} | {self.type} | {self.date_debut} → {self.date_fin or "actuel"}'
