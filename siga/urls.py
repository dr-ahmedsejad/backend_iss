"""
SIGA — URL racine
Toutes les routes API sont préfixées par /api/v1/
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

from core.media_auth import MediaAuthView
from core.mirror import InstanceView

# La generation du suivi projette d'abord la semaine reclamee. Voir plus bas
# pour la capture d'URL, et apps/edt/generation.py pour ce qu'elle supprime.
from apps.edt.generation import SuivieAvecProjectionViewSet

urlpatterns = [
    # Admin Django
    path('admin/', admin.site.urls),

    # Contrôle d'accès aux fichiers /media/ (sous-requête Nginx auth_request).
    path('internal/media-auth/', MediaAuthView.as_view(), name='media-auth'),

    # ── API v1 ───────────────────────────────────────────────────────────────
    path('api/v1/auth/',        include('apps.authentication.urls')),
    path('api/v1/parametres/',  include('apps.parametres.urls')),
    path('api/v1/banques/',     include('apps.banque.urls')),
    path('api/v1/salles/',      include('apps.salle.urls')),
    path('api/v1/departements/',include('apps.departement.urls')),
    path('api/v1/ems/',         include('apps.em.urls')),
    path('api/v1/profs/',       include('apps.prof.urls')),
    path('api/v1/prof-type-history/', include('apps.prof.history_urls')),
    path('api/v1/emplois/',     include('apps.emplois.urls')),
    # Planification hebdomadaire (nouveau moteur). Les ecrans du socle
    # ci-dessus restent en place et fonctionnels : les deux coexistent.
    path('api/v1/edt/',         include('apps.edt.urls')),
    # `suivies/ajouter/` est capte AVANT le routeur du socle : Django resout
    # dans l'ordre de cette liste, la premiere route qui correspond gagne. La
    # vue heritee projette la semaine RECLAMEE PAR LA REQUETE, puis delegue au
    # socle, qui n'est pas modifie.
    # Voir apps/edt/generation.py pour la panne que cela supprime.
    path('api/v1/suivi/suivies/ajouter/',
         SuivieAvecProjectionViewSet.as_view({'post': 'ajouter_suivie'}),
         name='suivie-ajouter'),
    path('api/v1/suivi/',       include('apps.suivi.urls')),
    path('api/v1/absences/',    include('apps.absence.urls')),
    path('api/v1/vacations/',   include('apps.vacation.urls')),
    path('api/v1/avancement/',  include('apps.avancement.urls')),
    # ── Scolarite LMD ────────────────────────────────────────────────────────
    path('api/v1/scolarite/',    include('apps.scolarite.urls')),
    path('api/v1/',              include('apps.modules.urls')),
    path('api/v1/inscriptions/', include('apps.inscriptions.urls')),
    path('api/v1/evaluations/',  include('apps.evaluations.urls')),
    path('api/v1/stages/',       include('apps.stages.urls')),
    path('api/v1/documents/',    include('apps.documents.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    # Portail étudiant
    path('api/v1/portail/',       include('apps.portail.urls')),
    path('api/v1/reclamations/',  include('apps.reclamations.urls')),
    # Portail en ligne : rôle de l'instance (public), publication vers le
    # miroir, brouillon de notes saisi en ligne.
    path('api/v1/instance/',        InstanceView.as_view(), name='instance'),
    path('api/v1/synchronisation/', include('apps.publication.urls')),
    path('api/v1/saisie-en-ligne/', include('apps.saisie_en_ligne.urls')),
    # Journal d'audit (lecture seule)
    path('api/v1/audit/',         include('apps.audit.urls')),
    # Sauvegardes BD (liste, download, matrice grants, audit log)
    path('api/v1/backups/',       include('apps.backup.urls')),
]

# OpenAPI docs — exposées hors production uniquement (reconnaissance API en prod).
if settings.DEBUG:
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(),                      name='schema'),
        path('api/docs/',   SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/redoc/',  SpectacularRedocView.as_view(url_name='schema'),   name='redoc'),
    ]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
