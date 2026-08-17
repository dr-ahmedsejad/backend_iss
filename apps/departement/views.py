from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from core.permissions import RBACPermission
from core.mixins import AuditMixin, SelectAllMixin
from core.pagination import StandardPagination
from .models import Departement
from .serializers import DepartementSerializer

class DepartementViewSet(AuditMixin, SelectAllMixin, viewsets.ModelViewSet):
    queryset           = Departement.objects.select_related('niveau', 'filiere', 'institution').all()
    serializer_class   = DepartementSerializer
    permission_classes = [RBACPermission]
    required_module    = 'departements'
    filter_backends    = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields   = ['annee_universitaire', 'niveau', 'filiere', 'institution', 'is_container']
    search_fields      = ['nom', 'code']
    ordering_fields    = ['nom']
    pagination_class   = StandardPagination

    def get_permissions(self):
        # Lecture libre pour tout authentifie : Departement = donnee de reference
        # consommee par de nombreux ecrans (rapports absences, vacations, suivi...).
        # Ecritures (create/update/destroy) restent gatees par RBAC departements.
        if self.action in ('list', 'retrieve', 'all'):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        """Filtre optionnel EDT-scope : si ?edt_scope=1, ne renvoie que les
        groupes que ce user peut gerer (managed_departements). Admin bypass.

        Cible : les selects/chips des pages EDT (emplois, suivi, vacation)
        qui ne doivent montrer que les groupes attribues. Les pages
        non-EDT (admissions, statistiques, parametres) n'envoient pas
        ce parametre et conservent la liste complete.
        """
        qs = super().get_queryset()
        if self.request.query_params.get('edt_scope') in ('1', 'true', 'True'):
            u = self.request.user
            # Superuser fallback : voit tout sans configurer la matrice.
            # Tout autre user (admin role inclus) : filtre sur managed_departements.
            if not (u.is_authenticated and u.is_superuser):
                managed = u.managed_departements.values_list('id', flat=True)
                qs = qs.filter(pk__in=list(managed))
        return qs
