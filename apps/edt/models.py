"""
Planification de l'emploi du temps à la semaine.

`emplois.Emplois` est une grille unique par (année, parité de semestre) : elle
dit « lundi, créneau 3 », jamais *quel* lundi. Ni report, ni férié, ni
rattrapage, ni exception ponctuelle — et rien ne distingue la semaine 3 de la
semaine 12.

Ce module ajoute la dimension manquante **sans toucher au socle** :

    GrilleType ──duplication──> SeanceReelle ──projection──> emplois.Emplois
                                                             (inchangé)
                                                                  │
                                              suivi.Suivie ← ─────┘  → charge → vacations

La grille type est le patron d'un groupe ; on la duplique sur les semaines du
semestre, puis chaque semaine se modifie seule. Juste avant que le Suivi ne
génère ses lignes, la semaine considérée est projetée dans `Emplois` — c'est le
seul point de contact, et il est à sens unique.

C'est la démarche d'IPGEI (`apps/ipgei/miroir.py`), retenue pour la même raison :
le pointage, la charge et les vacations sont repris tels quels et ne doivent pas
bouger.

**Le rattachement à la semaine passe par `parametres.Semaine`**, dont une ligne
est un JOUR — `(numero_semaine, jour, date, année, parité, type de semaine)`.
Une séance qui pointe cette ligne obtient sa date, son jour et son type de
semaine sans le moindre calcul. IPGEI, lui, stocke la semaine et recalcule la
date en traduisant un libellé de jour en décalage : une conversion qui rend
`None` sur un libellé inattendu et laisse la séance sans date.
"""
from django.db import models


TYPE_SEMESTRE_CHOICES = [('I', 'Impair'), ('P', 'Pair')]


class GrilleType(models.Model):
    """
    Patron d'emploi du temps d'un groupe, pour une parité de semestre.

    C'est lui qu'on duplique sur chaque semaine. Le modifier ne change aucune
    semaine déjà posée : les semaines vivent leur vie une fois dupliquées.
    """
    departement = models.ForeignKey(
        'departement.Departement', on_delete=models.CASCADE, related_name='grilles_edt',
        help_text='Groupe (ou sous-groupe) dont ceci est la grille.',
    )
    type_semestre = models.CharField(max_length=1, choices=TYPE_SEMESTRE_CHOICES, default='I')
    annee_universitaire = models.CharField(max_length=20, db_index=True)
    libelle = models.CharField(max_length=100, blank=True, default='')
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'edt_grille_type'
        ordering = ['annee_universitaire', 'departement', 'type_semestre']
        constraints = [
            models.UniqueConstraint(
                fields=['departement', 'type_semestre', 'annee_universitaire'],
                name='uniq_edt_grille',
            ),
        ]

    def __str__(self):
        return (f'Grille {self.departement} — semestre '
                f'{self.get_type_semestre_display()} {self.annee_universitaire}')


class SeanceType(models.Model):
    """Case du patron : jour × créneau → élément, enseignant, salle."""
    grille = models.ForeignKey(GrilleType, on_delete=models.CASCADE, related_name='seances')
    jour_fk = models.ForeignKey('parametres.Jour', on_delete=models.PROTECT, related_name='+')
    creneau_fk = models.ForeignKey('parametres.Creneau', on_delete=models.PROTECT, related_name='+')
    # Facultatif : une séance spéciale — sport, formation militaire — bloque un
    # créneau sans relever d'un enseignement de la maquette. Y laisser un
    # enseignant lui compterait des heures, donc une vacation, pour un cours
    # qu'il ne donne pas.
    em = models.ForeignKey(
        'em.EM', on_delete=models.PROTECT, null=True, blank=True, related_name='seances_type_edt')
    prof = models.ForeignKey(
        'prof.Prof', on_delete=models.PROTECT, null=True, blank=True, related_name='seances_type_edt')
    salle = models.ForeignKey(
        'salle.Salle', on_delete=models.SET_NULL, null=True, blank=True, related_name='seances_type_edt')
    type_seance_fk = models.ForeignKey(
        'parametres.Seance', on_delete=models.PROTECT, related_name='+')

    class Meta:
        db_table = 'edt_seance_type'
        ordering = ['grille', 'jour_fk', 'creneau_fk__ordre']
        constraints = [
            models.UniqueConstraint(
                fields=['grille', 'jour_fk', 'creneau_fk'],
                name='uniq_edt_seance_type_case',
            ),
        ]

    def __str__(self):
        return f'{self.grille.departement} {self.jour_fk} {self.creneau_fk}'

    def save(self, *args, **kwargs):
        _vider_si_speciale(self)
        super().save(*args, **kwargs)


def _vider_si_speciale(seance):
    """Une séance spéciale n'a ni enseignant, ni salle, ni élément.

    Sport, formation militaire : le créneau est bloqué pour tout le groupe, mais
    aucun enseignant du référentiel ne l'assure et cela ne relève d'aucun
    enseignement. Y laisser un professeur lui compterait des heures — donc une
    vacation — pour un cours qu'il ne donne pas.
    """
    if seance.type_seance_fk_id and getattr(seance.type_seance_fk, 'is_special', False):
        seance.prof = None
        seance.salle = None
        seance.em = None


class SeanceReelle(models.Model):
    """
    Séance effectivement programmée une semaine donnée.

    Produite par duplication de la grille type, puis librement modifiable — sur
    une semaine ou sur un lot. C'est elle qui est projetée dans `emplois.Emplois`
    avant la génération du Suivi.
    """
    ORIGINE_GRILLE = 'grille'
    ORIGINE_MANUELLE = 'manuelle'
    ORIGINE_PERMUTATION = 'permutation'
    #: Posée en recopiant une AUTRE semaine — pas le patron.
    #:
    #: Elle ne pouvait pas hériter de l'origine de sa source. Une copie de
    #: séance manuelle aurait porté « manuelle », donc aurait été protégée de
    #: l'écrasement : recopier à nouveau après avoir corrigé la semaine source
    #: n'aurait plus rien changé. Et l'étiqueter « grille » aurait menti sur sa
    #: provenance, rouvrant le défaut que la migration 0002 répare.
    #:
    #: Une valeur à elle donne la règle juste : l'écrasement reprend ce qu'une
    #: DUPLICATION a posé — patron ou semaine — et épargne les saisies et les
    #: permutations. L'original de la semaine source garde son origine.
    ORIGINE_RECOPIE = 'recopie'
    ORIGINE_CHOICES = [
        (ORIGINE_GRILLE, 'Dupliquée de la grille type'),
        (ORIGINE_MANUELLE, 'Ajoutée manuellement'),
        (ORIGINE_PERMUTATION, 'Issue d\'une permutation'),
        (ORIGINE_RECOPIE, 'Recopiée d\'une autre semaine'),
    ]
    #: Ce qu'une duplication a posé, et qu'elle peut donc reprendre.
    ORIGINES_DUPLIQUEES = (ORIGINE_GRILLE, ORIGINE_RECOPIE)

    departement = models.ForeignKey(
        'departement.Departement', on_delete=models.CASCADE, related_name='seances_edt')
    # Une ligne de `parametres.Semaine` est un JOUR : elle porte à la fois le
    # numéro de semaine, le jour, la date et le type de semaine (cours, férié,
    # vacances, examens). Un seul lien, et la séance est datée.
    semaine = models.ForeignKey(
        'parametres.Semaine', on_delete=models.CASCADE, related_name='seances_edt')
    creneau_fk = models.ForeignKey('parametres.Creneau', on_delete=models.PROTECT, related_name='+')
    em = models.ForeignKey(
        'em.EM', on_delete=models.PROTECT, null=True, blank=True, related_name='seances_edt')
    prof = models.ForeignKey(
        'prof.Prof', on_delete=models.PROTECT, null=True, blank=True, related_name='seances_edt')
    salle = models.ForeignKey(
        'salle.Salle', on_delete=models.SET_NULL, null=True, blank=True, related_name='seances_edt')
    type_seance_fk = models.ForeignKey(
        'parametres.Seance', on_delete=models.PROTECT, related_name='+')

    # Séance PARTAGÉE : un même cours réunissant plusieurs groupes.
    #
    # Le lien est STOCKÉ, non deviné. L'écran actuel de l'emploi du temps le
    # reconstitue après coup, en comparant `(prof, EM, type, salle)` d'une
    # grille à l'autre : changez la salle pour un seul groupe et le cours se
    # scinde silencieusement en deux. Ici, les séances qui portent la même clé
    # SONT le même cours, quoi qu'on modifie ensuite.
    #
    # Une séance par groupe reste la bonne forme : c'est ce que le socle attend
    # — il refusionne les lignes au pointage (sa clé de regroupement exclut le
    # département) et compte les heures une seule fois. Une relation
    # plusieurs-à-plusieurs ferait perdre la contrainte d'unicité de la case,
    # sans rien gagner en aval.
    cle_partage = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text="Séances d'un même cours partagé entre plusieurs groupes. "
                  "Vide pour une séance ordinaire.",
    )
    # Défaut : MANUELLE. Une séance créée sans préciser son origine vient d'une
    # saisie — la duplication, elle, pose son origine explicitement. Le défaut
    # était « grille », et toute saisie à la main en héritait : la promesse de
    # la case « Rétablir le patron » — « les séances ajoutées à la main ne sont
    # jamais écrasées » — était alors fausse, et le premier écrasement
    # détruisait du travail. Voir la migration 0002, qui réétiquette l'existant.
    origine = models.CharField(max_length=12, choices=ORIGINE_CHOICES, default=ORIGINE_MANUELLE)
    seance_type = models.ForeignKey(
        SeanceType, on_delete=models.SET_NULL, null=True, blank=True, related_name='occurrences')
    # Une séance annulée n'est PAS projetée : il n'y a ni cours à pointer, ni
    # heure à payer.
    annulee = models.BooleanField(default=False)
    # POURQUOI la séance est annulée, quand ce n'est pas une décision à la main.
    #
    # Un jour marqué férié annule ses séances ; retirer le férié doit les
    # rétablir — CELLES-LÀ, et jamais une annulation décidée à la main (un
    # enseignant malade le même jour). Sans le motif, le retrait ne pourrait
    # pas les distinguer. Vide pour une annulation manuelle ; une annulation
    # ou un rétablissement faits à la main l'effacent.
    MOTIF_FERIE = 'ferie'
    MOTIFS_ANNULATION = [('', 'Manuelle'), (MOTIF_FERIE, 'Jour férié')]
    motif_annulation = models.CharField(
        max_length=10, choices=MOTIFS_ANNULATION, blank=True, default='')
    # Qui devait assurer la séance avant permutation. La charge suit `prof`,
    # c'est-à-dire l'enseignant effectif ; ceci ne sert qu'à la traçabilité.
    prof_initial = models.ForeignKey(
        'prof.Prof', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='seances_edt_cedees')
    observations = models.CharField(max_length=300, blank=True, default='')
    modifiee_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'edt_seance_reelle'
        ordering = ['semaine__date', 'creneau_fk__ordre']
        constraints = [
            models.UniqueConstraint(
                fields=['departement', 'semaine', 'creneau_fk'],
                name='uniq_edt_seance_reelle_case',
            ),
        ]
        indexes = [
            models.Index(fields=['semaine', 'departement'], name='edt_seance_sem_dept_idx'),
            models.Index(fields=['prof', 'semaine'], name='edt_seance_prof_sem_idx'),
        ]

    def __str__(self):
        return f'{self.departement} {self.semaine} {self.creneau_fk}'

    # Raccourcis de lecture : tout vient de la ligne `Semaine`, sans calcul.
    @property
    def date(self):
        return self.semaine.date if self.semaine_id else None

    @property
    def jour_fk_id(self):
        return self.semaine.jour_fk_id if self.semaine_id else None

    def save(self, *args, **kwargs):
        _vider_si_speciale(self)
        super().save(*args, **kwargs)

    @property
    def est_partagee(self):
        return self.cle_partage is not None

    def soeurs(self):
        """Les autres séances du même cours partagé."""
        if not self.cle_partage:
            return SeanceReelle.objects.none()
        return (SeanceReelle.objects
                .filter(cle_partage=self.cle_partage)
                .exclude(pk=self.pk)
                .select_related('departement'))


class DemandeLiberation(models.Model):
    """
    Demande de libération d'une salle occupée par la séance d'un autre.

    La case s'affiche prise, avec le nom du groupe qui la détient, et ne peut
    pas être écrasée. Celui qui la veut adresse une demande ; **le détenteur
    seul décide** — personne ne passe outre, pas même la direction. C'est la
    règle arrêtée avec l'ESP : le partage du temps entre planificateurs est une
    convention entre eux, le système ne la tranche pas à leur place.

    À l'ISS, un seul compte planifie aujourd'hui — le directeur des études. Ce
    circuit n'a donc pas encore de cas d'emploi ; il est porté pour le jour où
    un second groupe sera délégué à quelqu'un d'autre.

    Accorder libère la SALLE de la séance visée, pas la séance : le cours a
    toujours lieu, il se tiendra ailleurs. Supprimer la séance de quelqu'un
    d'autre serait une décision pédagogique, hors de portée d'une demande de
    salle.
    """
    DEMANDEE = 'demandee'
    ACCORDEE = 'accordee'
    REFUSEE  = 'refusee'
    STATUT_CHOICES = [
        (DEMANDEE, 'Demandée'),
        (ACCORDEE, 'Accordée — salle libérée'),
        (REFUSEE,  'Refusée'),
    ]

    seance = models.ForeignKey(
        SeanceReelle, on_delete=models.CASCADE, related_name='demandes_liberation',
        help_text='La séance qui occupe la salle convoitée.')
    # La salle est figée à la demande : si le détenteur en change entre-temps,
    # la demande porterait sinon sur une salle qui n'est plus la bonne.
    salle = models.ForeignKey(
        'salle.Salle', on_delete=models.CASCADE, related_name='demandes_liberation')
    demandeur = models.ForeignKey(
        'authentication.CustomUser', on_delete=models.CASCADE,
        related_name='demandes_liberation_emises')
    motif = models.TextField(blank=True, default='')

    statut = models.CharField(max_length=10, choices=STATUT_CHOICES,
                              default=DEMANDEE, db_index=True)
    decidee_par = models.ForeignKey(
        'authentication.CustomUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='demandes_liberation_decidees')
    date_decision = models.DateTimeField(null=True, blank=True)
    reponse = models.TextField(blank=True, default='')
    date_demande = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'edt_demande_liberation'
        ordering = ['-date_demande']
        constraints = [
            # Une seule demande EN COURS par (séance, demandeur) : relancer
            # n'apporte rien et noierait le détenteur sous les notifications.
            models.UniqueConstraint(
                fields=['seance', 'demandeur'],
                condition=models.Q(statut='demandee'),
                name='uniq_edt_demande_en_cours',
            ),
        ]

    def __str__(self):
        return f'{self.demandeur} demande {self.salle} ({self.get_statut_display()})'


class EmploiArchive(models.Model):
    """
    Photographie de l'emploi du temps d'une semaine, prise au moment où il est
    transmis au suivi.

    `SeanceReelle` est modifiée sur place : la version d'avant est perdue. Or
    c'est sur une version précise que les heures ont été pointées, et parfois
    payées. Sans cette table, vérifier après coup ce qui avait été planifié
    demanderait de relire le journal d'audit ligne à ligne.

    Rien n'y pointe vers le référentiel. Une archive dont le contenu change
    quand on renomme une salle ou qu'on supprime un enseignant n'archive rien :
    les libellés sont donc figés au moment de la prise. Les identifiants sont
    conservés en simples entiers, pour regrouper et ordonner la grille sans
    créer de dépendance.

    Une re-transmission ne remplace pas la précédente : elle ajoute une
    version. Sinon l'archive ne servirait qu'une fois — celle où l'on ne s'est
    pas trompé.

    La semaine n'est pas une clé étrangère : ici une ligne `parametres.Semaine`
    est un JOUR. Une semaine se désigne donc par le triplet
    (année, parité de semestre, numéro), qui est aussi ce qu'emploient tous
    les écrans.
    """

    annee_universitaire = models.CharField(max_length=9)
    type_semestre       = models.CharField(max_length=1)
    numero_semaine      = models.IntegerField()

    departement = models.ForeignKey('departement.Departement',
                                    on_delete=models.CASCADE,
                                    related_name='archives_edt')
    version     = models.PositiveSmallIntegerField(default=1)
    genere_le   = models.DateTimeField()

    # Repères de grille — entiers nus, sans contrainte : ils servent à ordonner
    # et à regrouper, pas à retrouver une ligne du référentiel.
    jour_ref        = models.IntegerField(null=True, blank=True)
    creneau_ref     = models.IntegerField(null=True, blank=True)
    creneau_ordre   = models.IntegerField(default=0)
    em_ref          = models.IntegerField(null=True, blank=True)
    type_seance_ref = models.IntegerField(null=True, blank=True)
    # L'emploi du temps archivé se consulte aussi par enseignant et par salle.
    # Le nom figé ne suffit pas à filtrer : deux homonymes, une orthographe
    # corrigée, et la grille d'un enseignant se mélange à celle d'un autre.
    prof_ref        = models.IntegerField(null=True, blank=True)
    salle_ref       = models.IntegerField(null=True, blank=True)

    # Libellés figés
    jour_libelle        = models.CharField(max_length=30,  blank=True, default='')
    creneau_libelle     = models.CharField(max_length=30,  blank=True, default='')
    departement_nom     = models.CharField(max_length=100, blank=True, default='')
    departement_groupe  = models.CharField(max_length=30,  blank=True, default='')
    em_code             = models.CharField(max_length=30,  blank=True, default='')
    em_intitule         = models.CharField(max_length=200, blank=True, default='')
    type_seance_libelle = models.CharField(max_length=50,  blank=True, default='')
    type_seance_special = models.BooleanField(default=False)
    prof_nom            = models.CharField(max_length=150, blank=True, default='')
    prof_initial_nom    = models.CharField(max_length=150, blank=True, default='')
    salle_nom           = models.CharField(max_length=100, blank=True, default='')

    date_seance = models.DateField(null=True, blank=True)
    origine     = models.CharField(max_length=12, blank=True, default='')
    annulee     = models.BooleanField(default=False)

    class Meta:
        db_table            = 'edt_emploi_archive'
        verbose_name        = 'Emploi du temps archivé'
        verbose_name_plural = 'Emplois du temps archivés'
        ordering            = ['-genere_le', 'jour_ref', 'creneau_ordre']
        indexes = [
            models.Index(fields=['annee_universitaire', 'type_semestre',
                                 'numero_semaine', 'departement', 'version'],
                         name='edt_archive_semaine_idx'),
            models.Index(fields=['annee_universitaire', 'type_semestre',
                                 'numero_semaine', 'prof_ref'],
                         name='edt_archive_prof_idx'),
            models.Index(fields=['annee_universitaire', 'type_semestre',
                                 'numero_semaine', 'salle_ref'],
                         name='edt_archive_salle_idx'),
        ]

    def __str__(self):
        return (f'{self.departement_nom} S{self.numero_semaine} v{self.version} — '
                f'{self.jour_libelle} {self.creneau_libelle}')


class AnnonceEmploi(models.Model):
    """
    L'emploi du temps d'une semaine, VALIDÉ pour un groupe et annoncé à ses
    étudiants.

    Tant que le suivi d'une semaine n'est pas généré, son emploi du temps est
    provisoire ; la génération en fait le vrai — celui que le portail et
    l'application étudiante affichent, puisqu'ils lisent le suivi. C'est donc
    à la génération que les étudiants sont prévenus (apps/edt/annonces.py).

    Cette table dit si la semaine l'a déjà été : la première génération
    annonce « validé », une régénération après correction « modifié ».
    """

    annee_universitaire = models.CharField(max_length=9)
    type_semestre       = models.CharField(max_length=1)
    numero_semaine      = models.IntegerField()
    departement = models.ForeignKey('departement.Departement',
                                    on_delete=models.CASCADE,
                                    related_name='annonces_emploi')
    premiere_le = models.DateTimeField(auto_now_add=True)
    derniere_le = models.DateTimeField(auto_now=True)
    nb_annonces = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'edt_annonce_emploi'
        constraints = [
            models.UniqueConstraint(
                fields=['annee_universitaire', 'type_semestre', 'numero_semaine',
                        'departement'],
                name='uniq_edt_annonce_emploi'),
        ]

    def __str__(self):
        return f'{self.departement} S{self.numero_semaine} ({self.nb_annonces} annonce(s))'
