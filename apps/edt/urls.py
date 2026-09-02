from rest_framework.routers import DefaultRouter

from .views import (EmploiArchiveViewSet, GrilleTypeViewSet,
                    SeanceReelleViewSet, SeanceTypeViewSet)
from .views_liberation import DemandeLiberationViewSet

router = DefaultRouter()
router.register(r'grilles',      GrilleTypeViewSet,   basename='edt-grille')
router.register(r'seances-type', SeanceTypeViewSet,   basename='edt-seance-type')
router.register(r'seances',      SeanceReelleViewSet, basename='edt-seance')
# Verrou de salle : la case occupee ne s'ecrase pas, elle se demande.
router.register(r'liberations',  DemandeLiberationViewSet, basename='edt-liberation')

# Emplois du temps figes a la transmission au suivi : lecture seule.
router.register(r'archives',     EmploiArchiveViewSet, basename='edt-archive')

urlpatterns = router.urls
