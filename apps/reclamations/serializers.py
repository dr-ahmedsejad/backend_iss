from rest_framework import serializers
from .models import Reclamation, ReclamationSeance, PeriodeReclamation


def _ou_null(valeur):
    """Un instantané vide se dit `null`, là où l'écran attendait `null` : il
    distingue « pas de valeur » d'une chaîne vide (`?? '—'`)."""
    return valeur if valeur else None


class ReclamationSerializer(serializers.ModelSerializer):
    """Le contrat de l'API ne change pas : mêmes clés qu'avant la refonte sans
    clé étrangère. Les valeurs viennent de l'INSTANTANÉ figé au dépôt — la
    réclamation reste lisible quand l'étudiant ou l'élément ont disparu."""
    etudiant            = serializers.IntegerField(source='etudiant_id', read_only=True)
    presence            = serializers.IntegerField(source='presence_id', read_only=True)
    inscription_element = serializers.IntegerField(source='inscription_element_id', read_only=True)
    session_evaluation  = serializers.IntegerField(source='session_evaluation_id', read_only=True)
    traitee_par         = serializers.IntegerField(source='traitee_par_id', read_only=True)
    traitee_par_nom     = serializers.SerializerMethodField()
    em_code             = serializers.SerializerMethodField()
    em_intitule         = serializers.SerializerMethodField()

    def get_traitee_par_nom(self, obj):
        # Avant : `traitee_par.name`, null sans traitement, chaîne (même vide)
        # une fois traitée. On garde la même chose.
        return obj.traitee_par_nom if obj.traitee_par_id else None

    def get_em_code(self, obj):
        return _ou_null(obj.em_code)

    def get_em_intitule(self, obj):
        return _ou_null(obj.em_intitule)

    class Meta:
        model  = Reclamation
        fields = [
            'id', 'etudiant', 'etudiant_nom', 'etudiant_matricule',
            'type_reclamation', 'statut',
            'presence', 'inscription_element', 'session_evaluation',
            'motif', 'justificatif',
            'em_code', 'em_intitule',
            'reponse', 'traitee_par', 'traitee_par_nom',
            'date_soumission', 'date_traitement',
        ]
        read_only_fields = [
            'id', 'etudiant_nom', 'etudiant_matricule', 'statut', 'reponse',
            'date_soumission', 'date_traitement',
        ]


class ReclamationCreateSerializer(serializers.Serializer):
    """Le dépôt d'un étudiant. Les identifiants sont des entiers bruts, mais
    ils sont VÉRIFIÉS contre la base : on ne réclame pas sur une note ou une
    présence qui n'existe pas. Le sérialiseur rend les objets trouvés, dont la
    vue fige l'instantané."""
    type_reclamation    = serializers.ChoiceField(choices=[c[0] for c in Reclamation._meta.get_field('type_reclamation').choices],
                                                  default='autre')
    presence            = serializers.IntegerField(required=False, allow_null=True)
    inscription_element = serializers.IntegerField(required=False, allow_null=True)
    session_evaluation  = serializers.IntegerField(required=False, allow_null=True)
    motif               = serializers.CharField()
    justificatif        = serializers.FileField(required=False, allow_null=True)

    def validate_justificatif(self, value):
        if value:
            for v in Reclamation._meta.get_field('justificatif').validators:
                v(value)
        return value

    def validate(self, attrs):
        from apps.absence.models import Presence
        from apps.evaluations.models import SessionEvaluation
        from apps.inscriptions.models import InscriptionElement

        def charger(champ, modele, select=()):
            pk = attrs.get(champ)
            if pk in (None, ''):
                attrs[champ] = None
                return
            obj = modele.objects.select_related(*select).filter(pk=pk).first()
            if obj is None:
                raise serializers.ValidationError({champ: 'Introuvable.'})
            attrs[champ] = obj

        charger('presence', Presence, ('suivi__em',))
        charger('inscription_element', InscriptionElement,
                ('em', 'inscription_ped__inscription_admin__annee_univ',
                 'inscription_ped__semestre'))
        charger('session_evaluation', SessionEvaluation)
        return attrs


class ReclamationTraiterSerializer(serializers.Serializer):
    statut  = serializers.ChoiceField(choices=['en_cours', 'acceptee', 'rejetee'])
    reponse = serializers.CharField(required=False, allow_blank=True, default='')


# ── Réclamation de séance (enseignant) ────────────────────────────────────────

class ReclamationSeanceSerializer(serializers.ModelSerializer):
    traitee_par_nom = serializers.SerializerMethodField()

    def get_traitee_par_nom(self, obj):
        return obj.traitee_par_nom if obj.traitee_par_id else None

    class Meta:
        model  = ReclamationSeance
        fields = [
            'id', 'pointage_id', 'prof_id', 'prof_nom',
            'annee_universitaire', 'numero_semaine', 'jour', 'creneau', 'type_seance',
            'em_id', 'em_code', 'em_intitule', 'salle_nom', 'groupes',
            'motif', 'statut', 'reponse', 'traitee_par_id', 'traitee_par_nom',
            'date_soumission', 'date_traitement',
        ]
        read_only_fields = fields


class ReclamationSeanceCreateSerializer(serializers.Serializer):
    pointage = serializers.IntegerField()
    motif    = serializers.CharField()

    def validate_motif(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Le motif est obligatoire.')
        return value


class ReclamationSeanceTraiterSerializer(serializers.Serializer):
    statut  = serializers.ChoiceField(choices=['acceptee', 'rejetee'])
    reponse = serializers.CharField(required=False, allow_blank=True, default='')


class PeriodeReclamationSerializer(serializers.ModelSerializer):
    annee_univ_label  = serializers.CharField(source='annee_univ.annee', read_only=True)
    institution_nom   = serializers.CharField(source='institution.nom',  read_only=True)
    filiere_nom       = serializers.CharField(source='filiere.intitule_fr', read_only=True, default=None)
    cree_par_nom      = serializers.CharField(source='cree_par.name',    read_only=True, default=None)
    statut_temporel   = serializers.CharField(read_only=True)
    est_en_cours      = serializers.BooleanField(read_only=True)
    type_session_label  = serializers.CharField(source='get_type_session_display',  read_only=True)
    type_semestre_label = serializers.CharField(source='get_type_semestre_display', read_only=True)

    class Meta:
        model  = PeriodeReclamation
        fields = [
            'id', 'annee_univ', 'annee_univ_label',
            'type_session', 'type_session_label',
            'type_semestre', 'type_semestre_label',
            'institution', 'institution_nom',
            'filiere', 'filiere_nom',
            'niveau',
            'date_ouverture', 'date_fermeture', 'actif',
            'motif',
            'cree_par', 'cree_par_nom',
            'statut_temporel', 'est_en_cours',
            'date_creation', 'date_modification',
        ]
        read_only_fields = ['id', 'cree_par', 'date_creation', 'date_modification']

    def validate(self, attrs):
        do = attrs.get('date_ouverture') or getattr(self.instance, 'date_ouverture', None)
        df = attrs.get('date_fermeture') or getattr(self.instance, 'date_fermeture', None)
        if do and df and df <= do:
            raise serializers.ValidationError({
                'date_fermeture': 'La date de fermeture doit etre posterieure a la date d ouverture.',
            })
        return attrs
