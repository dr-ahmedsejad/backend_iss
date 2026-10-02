from django.urls import path

from .views import ExportSaisieEnLigneView, SaisieEnLigneView

urlpatterns = [
    path('',         SaisieEnLigneView.as_view(),         name='saisie-en-ligne'),
    path('export/',  ExportSaisieEnLigneView.as_view(),   name='saisie-en-ligne-export'),
]
