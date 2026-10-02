from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from core.permissions import RBACPermission, IsAdminOrIT
from .models import Reclamation, ReclamationSeance, PeriodeReclamation
from .serializers import (
    ReclamationSerializer, ReclamationTraiterSerializer,
    ReclamationSeanceSerializer, ReclamationSeanceCreateSerializer,
    ReclamationSeanceTraiterSerializer, PeriodeReclamationSerializer,
)

# Une réclamation ne CORRIGE rien. Le dire là où on la traite : la décision se
# reporte à la main sur le serveur de travail, et redescend à la publication.
AVERTISSEMENT_TRAITEMENT = (
    "Traiter une réclamation n'a corrigé aucune note, absence ni séance : "
    "la correction se fait sur le serveur de travail, et apparaîtra sur le "
    "portail à la publication suivante."
)


def _nom(user):
    return (getattr(user, 'name', '') or getattr(user, 'username', '') or '')[:150]


def _ems_du_prof(user, annee=None):
    """Les éléments qu'enseigne ce compte enseignant (lus dans le pointage,
    une table publiée — lecture seule). None si le profil manque."""
    try:
        prof = user.prof_profile
    except Exception:
        return None
    from apps.suivi.models import SuiviePointage
    qs = SuiviePointage.objects.filter(prof=prof)
    if annee:
        qs = qs.filter(annee_universitaire=annee)
    return set(qs.values_list('em_id', flat=True).distinct())


class ReclamationAdminViewSet(viewsets.ModelViewSet):
    """
    Gestion des réclamations côté staff.
    Accessible aux rôles SCOLARITE : admin, DE, scolarite, responsable_filiere.

    Sans clé étrangère (voir le modèle) : on filtre sur les identifiants bruts,
    on affiche l'instantané.
    """
    queryset           = Reclamation.objects.all()
    serializer_class   = ReclamationSerializer
    permission_classes = [RBACPermission]
    required_module    = 'reclamations'
    parser_classes     = [MultiPartParser, FormParser, JSONParser]
    filter_backends    = [DjangoFilterBackend, SearchFilter]
    filterset_fields   = ['statut', 'type_reclamation']
    search_fields      = ['etudiant_matricule', 'etudiant_nom', 'motif']

    def get_permissions(self):
        if self.action in ('pour_enseignant', 'traiter'):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        # Un enseignant ne voit QUE les réclamations de ses éléments — y
        # compris pour retrouver celle qu'il traite (get_object).
        if getattr(self.request.user, 'role', None) == 'enseignant':
            ems = _ems_du_prof(self.request.user) or set()
            qs = qs.filter(em_id__in=ems)
        return qs

    @action(detail=False, methods=['get'], url_path='pour-enseignant')
    def pour_enseignant(self, request):
        """
        GET /api/v1/reclamations/pour-enseignant/
        Retourne les réclamations des EMs enseignés par le prof connecté.
        """
        if getattr(request.user, 'role', None) != 'enseignant':
            return Response({'detail': 'Réservé aux enseignants.'}, status=403)

        annee = request.query_params.get('annee_universitaire')
        if not annee:
            try:
                annee = request.user.contexte.annee_universitaire
            except Exception:
                pass

        ems = _ems_du_prof(request.user, annee)
        if ems is None:
            return Response({'detail': 'Profil enseignant introuvable.'}, status=404)

        qs = Reclamation.objects.filter(em_id__in=ems).order_by('-date_soumission')
        statut = request.query_params.get('statut')
        if statut:
            qs = qs.filter(statut=statut)
        return Response(ReclamationSerializer(qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='traiter')
    def traiter(self, request, pk=None):
        """POST /api/v1/reclamations/{id}/traiter/ — décision, réponse, auteur, date."""
        role = getattr(request.user, 'role', None)
        if role != 'enseignant':
            # Le personnel : le droit RBAC du module, comme le reste du viewset.
            perm = RBACPermission()
            if not perm.has_permission(request, self):
                return Response({'detail': 'Accès refusé.'}, status=403)

        reclamation = Reclamation.objects.filter(pk=pk).first()
        if reclamation is None:
            return Response({'detail': 'Réclamation introuvable.'}, status=404)

        # Un enseignant ne traite que les réclamations de SES éléments.
        if role == 'enseignant':
            ems = _ems_du_prof(request.user)
            if ems is None:
                return Response({'detail': 'Profil enseignant introuvable.'}, status=404)
            if reclamation.em_id not in ems:
                return Response({'detail': 'Accès refusé.'}, status=403)

        serializer = ReclamationTraiterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reclamation.statut          = serializer.validated_data['statut']
        reclamation.reponse         = serializer.validated_data.get('reponse', '')
        reclamation.traitee_par_id  = request.user.pk
        reclamation.traitee_par_nom = _nom(request.user)
        reclamation.date_traitement = timezone.now()
        reclamation.save()

        return Response(ReclamationSerializer(reclamation).data)


# ── Réclamations de séance (enseignant) ───────────────────────────────────────

class ReclamationsSeanceView(APIView):
    """
    GET  /api/v1/reclamations/seances/        — admin et IT : toutes ;
                                                enseignant : les siennes.
    POST /api/v1/reclamations/seances/        — un enseignant réclame sur une
                                                séance pointée QUI EST LA SIENNE.

    La séance est vérifiée contre la base, puis l'instantané est figé : la
    réclamation reste lisible si le pointage est régénéré ou supprimé.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role = getattr(request.user, 'role', None)
        qs = ReclamationSeance.objects.all()
        if role == 'enseignant':
            try:
                prof = request.user.prof_profile
            except Exception:
                return Response({'detail': 'Profil enseignant introuvable.'}, status=404)
            qs = qs.filter(prof_id=prof.pk)
        elif role not in ('admin', 'IT'):
            return Response({'detail': 'Accès refusé.'}, status=403)
        statut = request.query_params.get('statut')
        if statut:
            qs = qs.filter(statut=statut)
        return Response(ReclamationSeanceSerializer(qs, many=True).data)

    def post(self, request):
        if getattr(request.user, 'role', None) != 'enseignant':
            return Response({'detail': 'Réservé aux enseignants.'}, status=403)
        try:
            prof = request.user.prof_profile
        except Exception:
            return Response({'detail': 'Profil enseignant introuvable.'}, status=403)

        ser = ReclamationSeanceCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        from apps.suivi.models import SuiviePointage
        sp = (SuiviePointage.objects
              .select_related('em', 'salle', 'jour_fk', 'creneau_fk', 'type_seance_fk', 'prof')
              .prefetch_related('departements')
              .filter(pk=ser.validated_data['pointage']).first())
        if sp is None:
            return Response({'pointage': ['Séance introuvable.']}, status=400)
        if sp.prof_id != prof.pk:
            return Response({'detail': 'Acces non autorise.'}, status=403)
        if ReclamationSeance.objects.filter(
                pointage_id=sp.pk, statut__in=('en_attente', 'acceptee')).exists():
            return Response({'detail': 'Une reclamation est deja en cours ou acceptee.'}, status=400)

        r = ReclamationSeance.objects.create(
            pointage_id=sp.pk,
            prof_id=prof.pk,
            prof_nom=(getattr(sp.prof, 'nom', '') or '')[:200],
            annee_universitaire=getattr(sp, 'annee_universitaire', '') or '',
            numero_semaine=getattr(sp, 'numero_semaine', None),
            jour=(sp.jour_fk.jour if sp.jour_fk_id else '')[:20],
            creneau=(sp.creneau_fk.creneau if sp.creneau_fk_id else '')[:50],
            type_seance=(sp.type_seance_fk.type_seance if sp.type_seance_fk_id else '')[:50],
            em_id=sp.em_id,
            em_code=(sp.em.code_em if sp.em_id else '')[:50],
            em_intitule=(sp.em.intitule if sp.em_id else '')[:200],
            salle_nom=(sp.salle.nom if sp.salle_id else '')[:100],
            groupes=', '.join(sorted(d.nom for d in sp.departements.all() if d.nom))[:500],
            motif=ser.validated_data['motif'],
        )
        return Response(ReclamationSeanceSerializer(r).data, status=status.HTTP_201_CREATED)


class TraiterReclamationSeanceView(APIView):
    """POST /api/v1/reclamations/seances/{id}/traiter/ — admin et IT.

    N'ajuste NI le pointage, NI la charge, NI la paie : la décision se reporte
    à la main sur le serveur de travail.
    """
    permission_classes = [IsAdminOrIT]

    def post(self, request, pk):
        r = ReclamationSeance.objects.filter(pk=pk).first()
        if r is None:
            return Response({'detail': 'Réclamation introuvable.'}, status=404)
        ser = ReclamationSeanceTraiterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        r.statut          = ser.validated_data['statut']
        r.reponse         = ser.validated_data.get('reponse', '')
        r.traitee_par_id  = request.user.pk
        r.traitee_par_nom = _nom(request.user)
        r.date_traitement = timezone.now()
        r.save()
        data = ReclamationSeanceSerializer(r).data
        data['avertissement'] = AVERTISSEMENT_TRAITEMENT
        return Response(data)


class PeriodeReclamationViewSet(viewsets.ModelViewSet):
    """
    Gestion des periodes de reclamation (cote SG).
    Permet a l administration d ouvrir/fermer manuellement des fenetres
    pendant lesquelles les etudiants peuvent reclamer leurs notes.
    """
    queryset = PeriodeReclamation.objects.select_related(
        'annee_univ', 'institution', 'filiere', 'cree_par',
    ).all()
    serializer_class   = PeriodeReclamationSerializer
    permission_classes = [RBACPermission]
    required_module    = 'reclamations'
    filter_backends    = [DjangoFilterBackend]
    filterset_fields   = ['annee_univ', 'type_session', 'type_semestre', 'institution', 'filiere', 'niveau', 'actif']

    def perform_create(self, serializer):
        serializer.save(cree_par=self.request.user)

    @action(detail=True, methods=['post'], url_path='fermer-maintenant')
    def fermer_maintenant(self, request, pk=None):
        """POST /api/v1/reclamations/periodes/{id}/fermer-maintenant/
        Force date_fermeture = now() pour fermer la periode immediatement.
        """
        periode = self.get_object()
        periode.date_fermeture = timezone.now()
        periode.save(update_fields=['date_fermeture', 'date_modification'])
        return Response(PeriodeReclamationSerializer(periode).data)

    @action(detail=True, methods=['post'], url_path='basculer-actif')
    def basculer_actif(self, request, pk=None):
        """POST /api/v1/reclamations/periodes/{id}/basculer-actif/
        Active ou desactive la periode (toggle).
        """
        periode = self.get_object()
        periode.actif = not periode.actif
        periode.save(update_fields=['actif', 'date_modification'])
        return Response(PeriodeReclamationSerializer(periode).data)
