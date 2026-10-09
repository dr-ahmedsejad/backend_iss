from rest_framework import serializers
from .models import Departement

class DepartementSerializer(serializers.ModelSerializer):
    niveau_nom   = serializers.CharField(source='niveau.niveau',          read_only=True)
    filiere_nom  = serializers.CharField(source='filiere.intitule_fr',    read_only=True, default=None)
    filiere_code = serializers.CharField(source='filiere.code',           read_only=True, default=None)
    groupe_display = serializers.SerializerMethodField()
    # Rang (1 ou 2) si c'est un groupe d'anglais, sinon None : l'emploi du
    # temps n'y propose que l'anglais du niveau (apps/edt/anglais.py).
    groupe_anglais = serializers.SerializerMethodField()

    class Meta:
        model  = Departement
        fields = '__all__'
        extra_kwargs = {'institution': {'required': False, 'allow_null': True}}

    def get_groupe_display(self, obj) -> str:
        parts = []
        if obj.filiere:
            parts.append(obj.filiere.intitule_fr or '')
        if obj.groupe:
            parts.append(obj.groupe)
        return ' — '.join(p for p in parts if p) or obj.nom

    def get_groupe_anglais(self, obj):
        from apps.edt.anglais import groupe_anglais_de
        g = groupe_anglais_de(obj)
        return g.rang if g else None

    def validate(self, attrs):
        # Le niveau et l'année d'un groupe d'anglais sont recopiés dans sa
        # ligne GroupeAnglais (la limite de deux par niveau en dépend) : ils ne
        # changent pas ici. Le nom, lui, reste libre.
        from apps.edt.anglais import groupe_anglais_de
        g = groupe_anglais_de(self.instance) if self.instance else None
        if g is not None:
            for champ in ('niveau', 'annee_universitaire', 'filiere'):
                if champ in attrs and attrs[champ] != getattr(self.instance, champ):
                    raise serializers.ValidationError({champ: (
                        f"« {self.instance.nom} » est un groupe d'anglais : son niveau, "
                        "son année et sa filière ne changent pas. Créez-en un autre "
                        "depuis « Étudiants → Groupes d'anglais ».")})
        return attrs

    def create(self, validated_data):
        # Auto-fill institution depuis filiere.institution sinon institution principale
        # (Section 1bis institution_V1 — institution NOT NULL en base, transparente cote UI)
        if not validated_data.get('institution'):
            from apps.parametres.models import Institution
            filiere = validated_data.get('filiere')
            validated_data['institution'] = (
                (filiere.institution if filiere and filiere.institution_id else None)
                or Institution.objects.filter(est_principale=True).first()
            )
        return super().create(validated_data)
