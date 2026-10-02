from rest_framework import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Notification
        fields = '__all__'
        read_only_fields = ['destinataire', 'created_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Sur le miroir, une lecture faite EN LIGNE vaut lecture (voir
        # NotificationViewSet) ; ailleurs l'annotation n'existe pas.
        if getattr(instance, 'lue_en_ligne', False):
            data['lue'] = True
        return data
