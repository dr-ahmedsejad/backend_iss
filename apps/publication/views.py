from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsAdmin

from .models import PublicationJournal
from .services import PublicationRefusee, plan, publier


def _ligne(j: PublicationJournal) -> dict:
    return {
        'id': j.id, 'cree_le': j.cree_le, 'par': j.par_nom or None,
        'statut': j.statut, 'transfere': j.transfere, 'taille': j.taille,
        'sha256': j.sha256 or None, 'duree_s': j.duree_s,
        'reponse_vps': j.reponse_vps or None, 'erreur': j.erreur or None,
    }


class PlanView(APIView):
    """GET /api/v1/synchronisation/plan/ — ce qui est publié, préservé, protégé."""
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(plan())


class PublierView(APIView):
    """POST /api/v1/synchronisation/publier/ — administrateur seul, serveur de
    travail seul (l'intercepteur la refuse déjà sur le miroir ; la fonction la
    refuse encore)."""
    permission_classes = [IsAdmin]

    def post(self, request):
        try:
            journal = publier(request.user)
        except PublicationRefusee as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        code = (status.HTTP_502_BAD_GATEWAY if journal.statut == PublicationJournal.STATUT_ECHEC
                else status.HTTP_200_OK)
        return Response(_ligne(journal), status=code)


class HistoriqueView(APIView):
    """GET /api/v1/synchronisation/historique/ — les 50 dernières publications."""
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response([_ligne(j) for j in PublicationJournal.objects.all()[:50]])
