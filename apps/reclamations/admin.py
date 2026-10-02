from django.contrib import admin
from .models import Reclamation

@admin.register(Reclamation)
class ReclamationAdmin(admin.ModelAdmin):
    # Instantané, et non relation : la réclamation n'a plus de clé étrangère.
    list_display  = ['id', 'etudiant_matricule', 'etudiant_nom', 'type_reclamation', 'statut', 'date_soumission']
    list_filter   = ['statut', 'type_reclamation']
    search_fields = ['etudiant_matricule', 'etudiant_nom']
