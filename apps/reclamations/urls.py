from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (ReclamationAdminViewSet, PeriodeReclamationViewSet,
                    ReclamationsSeanceView, TraiterReclamationSeanceView)

router = DefaultRouter()
# IMPORTANT : enregistrer 'periodes' AVANT '' (sinon ReclamationAdminViewSet
# capture toutes les routes y compris /periodes/).
router.register('periodes', PeriodeReclamationViewSet, basename='reclamations-periodes')
router.register('', ReclamationAdminViewSet, basename='reclamations')

urlpatterns = [
    # Les séances AVANT le routeur : sa route `<pk>/` capturerait `seances/`.
    path('seances/', ReclamationsSeanceView.as_view(), name='reclamations-seances'),
    path('seances/<int:pk>/traiter/', TraiterReclamationSeanceView.as_view(),
         name='reclamations-seance-traiter'),
    path('', include(router.urls)),
]
