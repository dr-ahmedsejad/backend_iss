"""
Le journal des publications, et la trace de leur réception.

Deux tables, une de chaque côté :

  * `publication_journal` — sur le SERVEUR DE TRAVAIL : chaque appui sur
    « Publier », succès comme échec. Une publication qui échoue sans laisser de
    trace laisse croire que le portail est à jour ;
  * `publication_recue`   — sur le MIROIR : écrite par le script de réception
    après chaque restauration réussie. Exclusion TOTALE
    (`settings.TABLES_PROPRES_A_L_INSTANCE`) : la publication ne l'écrase pas.
    Elle date la dernière publication — le bandeau l'affiche, et
    CookieTokenRefreshView refuse tout jeton émis avant (la publication vide
    la liste des jetons révoqués).
"""
from django.db import models


class PublicationJournal(models.Model):
    STATUT_CONSTRUIT = 'construit'   # dump construit, rien transféré (pas de cible)
    STATUT_PUBLIE    = 'publie'      # transféré, et le miroir a répondu OK
    STATUT_ECHEC     = 'echec'
    STATUTS = [
        (STATUT_CONSTRUIT, 'Construit, non transféré'),
        (STATUT_PUBLIE,    'Publié'),
        (STATUT_ECHEC,     'Échec'),
    ]

    cree_le     = models.DateTimeField(auto_now_add=True, db_index=True)
    # Identifiant brut + nom : le journal se lit même si le compte disparaît.
    par_id      = models.BigIntegerField(null=True, blank=True)
    par_nom     = models.CharField(max_length=150, blank=True, default='')
    statut      = models.CharField(max_length=20, choices=STATUTS)
    transfere   = models.BooleanField(default=False)
    taille      = models.BigIntegerField(null=True, blank=True)
    sha256      = models.CharField(max_length=64, blank=True, default='')
    duree_s     = models.FloatField(null=True, blank=True)
    reponse_vps = models.TextField(blank=True, default='')
    erreur      = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'publication_journal'
        ordering = ['-cree_le']


class PublicationRecue(models.Model):
    """Écrite par deploy/miroir/recevoir-publication.sh, jamais par l'API."""
    recue_le = models.DateTimeField(db_index=True)
    sha256   = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        db_table = 'publication_recue'
        ordering = ['-recue_le']
