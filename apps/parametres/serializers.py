from rest_framework import serializers
from .models import (Year, Niveau, Semestre, Seance, Creneau, Jour, Semaine, Paiement,
                     Ramadan, Institution, JourFerieFixe)


class YearSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Year
        fields = '__all__'


class NiveauSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Niveau
        fields = '__all__'


class SemestreSerializer(serializers.ModelSerializer):
    niveau_nom            = serializers.CharField(source='niveau_semestre.niveau', read_only=True)
    decalage_pedagogique  = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Semestre
        fields = '__all__'

    def get_decalage_pedagogique(self, obj):
        """Decalage en semaines applicable a ce semestre (selon le niveau lie).

        Convention : ne s'applique qu'au semestre Impair. Retourne 0 pour
        le semestre Pair ou si aucun dept du niveau n'a de decalage.
        """
        from apps.departement.semaine_helpers import decalage_for_code_semestre
        return decalage_for_code_semestre(obj.code_semestre, obj.type_semestre)


class SeanceSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Seance
        fields = '__all__'


class CreneauSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Creneau
        fields = '__all__'


class JourSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Jour
        fields = '__all__'


class SemaineSerializer(serializers.ModelSerializer):
    jour                 = serializers.CharField(source='jour_fk.jour', read_only=True)
    type_semaine_display = serializers.CharField(source='get_type_semaine_display', read_only=True)

    class Meta:
        model  = Semaine
        fields = '__all__'


class PaiementSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Paiement
        fields = '__all__'


class JourFerieFixeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = JourFerieFixe
        fields = ['id', 'jour', 'mois', 'libelle', 'actif']
        # L'unicite (jour, mois) est verifiee dans `validate`, avec un message
        # lisible ; le validateur automatique de DRF la doublerait.
        validators = []

    def validate(self, attrs):
        from .feries import date_valide
        inst  = self.instance
        jour  = attrs.get('jour',  inst.jour if inst else None)
        mois  = attrs.get('mois',  inst.mois if inst else None)
        if 'libelle' in attrs:
            attrs['libelle'] = (attrs['libelle'] or '').strip()
            if not attrs['libelle']:
                raise serializers.ValidationError({'libelle': 'Le libellé est requis.'})
        # Le 29 février est valide : il existe les années bissextiles.
        if not date_valide(jour, mois):
            raise serializers.ValidationError(
                {'jour': f'{jour}/{mois} n’est pas une date du calendrier.'})
        doublon = JourFerieFixe.objects.filter(jour=jour, mois=mois)
        if inst:
            doublon = doublon.exclude(pk=inst.pk)
        if doublon.exists():
            raise serializers.ValidationError(
                {'jour': f'Un férié existe déjà au {jour:02d}/{mois:02d}.'})
        return attrs


class RamadanSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Ramadan
        fields = '__all__'


class InstitutionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Institution
        fields = '__all__'

    def to_representation(self, instance):
        data    = super().to_representation(instance)
        request = self.context.get('request')
        if instance.logo:
            url = instance.logo.url
        else:
            from django.templatetags.static import static
            url = static('assets/img/logo_iss.png')
        data['logo_url'] = request.build_absolute_uri(url) if request else url
        return data


class GenerateSemainesSerializer(serializers.Serializer):
    annee_universitaire = serializers.CharField()
    date_debut          = serializers.DateField()
    date_fin            = serializers.DateField()
    type_semestre       = serializers.ChoiceField(choices=[('I', 'Impair'), ('P', 'Pair')])
    jours_actifs        = serializers.ListField(child=serializers.CharField(), required=False)


class AddBatchSemainesSerializer(serializers.Serializer):
    annee_universitaire = serializers.CharField()
    date_debut          = serializers.DateField()
    nombre_semaines     = serializers.IntegerField(min_value=1, max_value=52)
    type_semestre       = serializers.ChoiceField(choices=[('I', 'Impair'), ('P', 'Pair')])
