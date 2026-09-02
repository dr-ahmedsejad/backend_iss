"""
Vues API pour la gestion de la table Progression et l'exécution des réinscriptions.

Endpoints :
  POST   /inscriptions/admin/generer-progressions/    → ProgressionService.generer_progressions()
  GET    /inscriptions/admin/progressions/            → Liste filtrée par annee_cible,
                                                        décision, statut et filière
                                                        (source et cible)
  PATCH  /inscriptions/admin/progressions/<pk>/       → ModificationProgressionService.modifier()
  POST   /inscriptions/admin/executer-reinscriptions/ → ReinscriptionService.executer()
"""
from django.db import IntegrityError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.permissions import RBACPermission

from apps.evaluations.models import PVDeliberation
from apps.inscriptions.models import Progression
from apps.inscriptions.services.progression import (
    ProgressionService,
    ModificationProgressionService,
)
from apps.inscriptions.services.reinscription import ReinscriptionService


def _serialiser_progression(p: Progression) -> dict:
    return {
        'id':              p.pk,
        'matricule':       p.matricule,
        'etudiant':        str(p.etudiant),
        # Le genre sert à accorder le statut affiché — « Inscrite », « Diplômée ».
        # Sur 2026-2027, 58 des 141 progressions concernent une étudiante : le
        # masculin par défaut se remarque.
        'genre':           p.etudiant.genre,
        'filiere_source':  {'id': p.filiere_source_id, 'code': p.filiere_source.code},
        'filiere_cible':   (
            {'id': p.filiere_cible_id, 'code': p.filiere_cible.code}
            if p.filiere_cible else None
        ),
        'niveau_source':   p.niveau_source,
        'niveau_cible':    p.niveau_cible,
        'decision':        p.decision,
        'decision_label':  p.get_decision_display(),
        'statut':          p.statut,
        'statut_label':    p.get_statut_display(),
        'consomme_droit_redoublement': p.consomme_droit_redoublement,
        'motif_modification': p.motif_modification or '',
        'annee_source':    str(p.annee_source),
        'annee_cible':     str(p.annee_cible),
    }


class GenererProgressionsView(APIView):
    """
    POST /inscriptions/admin/generer-progressions/
    Body : { "pv_id": <int> }
    Génère la table Progression à partir d'un PV annuel clos.
    """
    permission_classes = [RBACPermission]
    required_module    = 'insc_progression'

    def post(self, request):
        pv_id = request.data.get('pv_id')
        if not pv_id:
            return Response({'error': 'pv_id est requis.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            pv = PVDeliberation.objects.select_related('filiere', 'annee_univ', 'session').get(pk=pv_id)
        except PVDeliberation.DoesNotExist:
            return Response({'error': f'PV #{pv_id} introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            stats = ProgressionService(pv).generer_progressions()
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'stats': stats, 'pv_id': pv.pk})


class ListeProgressionsView(APIView):
    """
    GET /inscriptions/admin/progressions/?annee_cible=<id>
        [&decision=<d>][&statut=<s>][&filiere_source=<id>][&filiere_cible=<id|aucune>]

    Liste les progressions pour une année cible, avec filtres optionnels.

    La filière se filtre sur SES DEUX AXES, parce que l'écran sert à deux
    gestes différents :

      * `filiere_source` — traiter une promotion à la fois. C'est la filière
        d'origine de l'étudiant, celle du PV, et elle ne change pas ;
      * `filiere_cible` — relire une orientation déjà posée. La valeur
        spéciale `aucune` isole les progressions NON ORIENTÉES, celles dont
        `filiere_cible` est vide : ce sont précisément celles que
        `ReinscriptionService.executer` saute en silence, et le seul moyen de
        les retrouver était de parcourir la liste à l'œil.
    """
    permission_classes = [RBACPermission]
    required_module    = 'insc_progression'

    # Ce que l'on écrit dans `filiere_cible` pour demander les non orientés.
    # Un identifiant ne peut pas exprimer « aucun », et `filiere_cible=` vide
    # est indistinguable d'un filtre absent — c'est la même chaîne que le
    # navigateur enverrait pour « Toutes les filières ».
    CIBLE_NON_ORIENTES = 'aucune'

    def get(self, request):
        annee_id = request.query_params.get('annee_cible')
        if not annee_id:
            return Response(
                {'error': 'Le paramètre annee_cible est requis.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = (
            Progression.objects
            .filter(annee_cible_id=annee_id)
            .select_related(
                'etudiant', 'filiere_source', 'filiere_cible',
                'annee_source', 'annee_cible',
            )
            .order_by('matricule')
        )

        if decision := request.query_params.get('decision'):
            qs = qs.filter(decision=decision)
        if statut := request.query_params.get('statut'):
            qs = qs.filter(statut=statut)

        if source := request.query_params.get('filiere_source'):
            if not str(source).isdigit():
                return Response(
                    {'error': 'filiere_source doit être un identifiant.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(filiere_source_id=source)

        cible = request.query_params.get('filiere_cible')
        if cible == self.CIBLE_NON_ORIENTES:
            qs = qs.filter(filiere_cible__isnull=True)
        elif cible:
            if not str(cible).isdigit():
                return Response(
                    {'error': "filiere_cible doit être un identifiant "
                              f"ou « {self.CIBLE_NON_ORIENTES} »."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(filiere_cible_id=cible)

        return Response([_serialiser_progression(p) for p in qs])


class ModifierProgressionView(APIView):
    """
    PATCH /inscriptions/admin/progressions/<pk>/
    Body : { "filiere_cible_id": <int|null>, "niveau_cible": <int|null>, "motif": <str> }
    Modifie la filière et/ou le niveau cible d'une progression avant exécution.
    """
    permission_classes = [RBACPermission]
    required_module    = 'insc_progression'

    def patch(self, request, pk):
        try:
            prog = ModificationProgressionService.modifier(
                progression_id=pk,
                nouvelle_filiere_id=request.data.get('filiere_cible_id'),
                nouveau_niveau=request.data.get('niveau_cible'),
                motif=request.data.get('motif', ''),
                user=request.user,
            )
        except Progression.DoesNotExist:
            return Response({'error': f'Progression #{pk} introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialiser_progression(
            Progression.objects.select_related(
                'etudiant', 'filiere_source', 'filiere_cible',
                'annee_source', 'annee_cible',
            ).get(pk=prog.pk)
        ))


class ExecuterReinscriptionsView(APIView):
    """
    POST /inscriptions/admin/executer-reinscriptions/
    Body : { "annee_cible_id": <int> }
    Exécute toutes les progressions en attente pour une année cible.
    """
    permission_classes = [RBACPermission]
    required_module    = 'insc_progression'

    def post(self, request):
        annee_id = request.data.get('annee_cible_id')
        if not annee_id:
            return Response(
                {'error': 'annee_cible_id est requis.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.parametres.models import Year
        try:
            annee = Year.objects.get(pk=annee_id)
        except Year.DoesNotExist:
            return Response(
                {'error': f'Année #{annee_id} introuvable.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            stats = ReinscriptionService.executer(annee)
        except (ValueError, IntegrityError) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'stats': stats, 'annee': str(annee)})
