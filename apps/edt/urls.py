from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (EmploiArchiveViewSet, GrilleTypeViewSet,
                    SeanceReelleViewSet, SeanceTypeViewSet)
from .views_liberation import DemandeLiberationViewSet
from .views_anglais import (AffecterAnglaisView, AnglaisView, EtudiantsAnglaisView,
                            GroupeAnglaisView, GroupesAnglaisView, ImporterAnglaisView)

router = DefaultRouter()
router.register(r'grilles',      GrilleTypeViewSet,   basename='edt-grille')
router.register(r'seances-type', SeanceTypeViewSet,   basename='edt-seance-type')
router.register(r'seances',      SeanceReelleViewSet, basename='edt-seance')
# Verrou de salle : la case occupee ne s'ecrase pas, elle se demande.
router.register(r'liberations',  DemandeLiberationViewSet, basename='edt-liberation')

# Emplois du temps figes a la transmission au suivi : lecture seule.
router.register(r'archives',     EmploiArchiveViewSet, basename='edt-archive')

urlpatterns = router.urls + [
    # Groupes d'anglais : deux par niveau, l'étudiant y est affecté pour
    # l'anglais seul (apps/edt/anglais.py).
    path('anglais/',                     AnglaisView.as_view(),          name='edt-anglais'),
    path('anglais/groupes/',             GroupesAnglaisView.as_view(),   name='edt-anglais-groupes'),
    path('anglais/groupes/<int:pk>/',    GroupeAnglaisView.as_view(),    name='edt-anglais-groupe'),
    path('anglais/etudiants/',           EtudiantsAnglaisView.as_view(), name='edt-anglais-etudiants'),
    path('anglais/affecter/',            AffecterAnglaisView.as_view(),  name='edt-anglais-affecter'),
    path('anglais/importer/',            ImporterAnglaisView.as_view(),  name='edt-anglais-importer'),
]
