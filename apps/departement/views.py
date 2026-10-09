from django.db.models import Count, Q
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
        if self.request.query_params.get('avec_etudiants') in ('1', 'true', 'True'):
            qs = self._groupes_a_planifier(qs)
        return qs

    @staticmethod
    def _groupes_a_planifier(qs):
        """Les groupes qui meritent une place dans les ecrans de planification.

        Un groupe sans etudiant n'a pas d'emploi du temps a construire : le
        proposer allonge la liste et fait remplir le vide. On l'ecarte donc
        quand `?avec_etudiants=1` — un OPT-IN, pour que les autres ecrans
        (admissions, statistiques, parametres, suivi, vacations) gardent la
        liste entiere.

        MAIS on garde celui qui porte DEJA des seances ou un patron, meme sans
        etudiant. Mesure du 30/09/2026 : le groupe #45 (G2, STAT L1) a zero
        etudiant et dix-huit seances. Le masquer le rendrait inatteignable
        depuis la grille alors que ses seances continuent d'alimenter
        `Emplois`, le suivi et les vacations — on cacherait le probleme au lieu
        de le montrer. Le rendre visible est ce qui permet de le corriger.
        """
        # `distinct=True` ne change RIEN au résultat ici — on ne compare qu'à
        # zéro, et trois jointures simultanées gonflent les comptes sans jamais
        # les annuler. Il est gardé parce que ces annotations seraient fausses
        # le jour où on les exposerait (un effectif affiché, un tri dessus).
        return (qs.annotate(
                    _etudiants=Count('etudiants', distinct=True),
                    _seances=Count('seances_edt', distinct=True),
                    _cases=Count('grilles_edt__seances', distinct=True))
                # Un groupe d'anglais n'a aucun étudiant RATTACHÉ — ils y sont
                # affectés (apps/edt/anglais.py) : on le garde toujours.
                .filter(Q(_etudiants__gt=0) | Q(_seances__gt=0) | Q(_cases__gt=0)
                        | Q(groupe_anglais__isnull=False)))
