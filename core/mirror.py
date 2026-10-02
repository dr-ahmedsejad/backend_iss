"""
Le rôle de l'instance : serveur de TRAVAIL ou MIROIR.

`MIRROR_MODE` (.env) :
  * serveur de travail (MIRROR_MODE=False, défaut) — l'autorité, le personnel
    y écrit tout. Ici, RIEN ne change : l'intercepteur laisse tout passer ;
  * miroir (MIRROR_MODE=True) — un autre VPS, consulté par les étudiants et
    les enseignants. LECTURE SEULE, sauf les adresses de la liste blanche
    (`settings.MIRROR_WRITE_ALLOWLIST`) : l'authentification et la boîte de
    réception, dépôt ET traitement.

Pourquoi refuser plutôt que laisser écrire : sur le miroir, toute table qui
n'est pas dans la boîte de réception est REMPLACÉE à chaque publication. Une
écriture acceptée là serait effacée sans un message — c'est pire qu'un refus
qui dit où aller.
"""
import re

from django.conf import settings
from django.http import JsonResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

METHODES_EN_ECRITURE = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

MESSAGE_REFUS = (
    "Ce portail est en consultation seule : cette opération est à effectuer "
    "sur le serveur de travail de l'établissement."
)


def est_miroir() -> bool:
    return bool(getattr(settings, 'MIRROR_MODE', False))


def _motifs():
    # Compilés à chaque appel : la liste vient des réglages, que les tests
    # surchargent. Une dizaine d'expressions, le coût est négligeable.
    return [re.compile(m) for m in getattr(settings, 'MIRROR_WRITE_ALLOWLIST', [])]


def ecriture_autorisee(chemin: str) -> bool:
    """Le chemin figure-t-il dans la liste blanche du miroir ?"""
    return any(m.match(chemin) for m in _motifs())


class MirrorReadOnlyMiddleware:
    """Sur le miroir, refuse toute écriture hors liste blanche (403).

    Sur le serveur de travail, ne fait RIEN — pas même lire la liste.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (est_miroir()
                and request.method in METHODES_EN_ECRITURE
                and not ecriture_autorisee(request.path)):
            return JsonResponse(
                {'error': MESSAGE_REFUS, 'code': 'miroir_lecture_seule'},
                status=403,
            )
        return self.get_response(request)


class InstanceView(APIView):
    """GET /api/v1/instance/ — le rôle de cette instance.

    PUBLIC : l'écran de connexion le lit avant toute authentification, pour
    afficher le bandeau « consultation seule » sur le miroir.
    """
    permission_classes     = [AllowAny]
    authentication_classes = []

    def get(self, request):
        miroir = est_miroir()
        derniere = None
        if miroir:
            from apps.publication.models import PublicationRecue
            recue = PublicationRecue.objects.order_by('-recue_le').first()
            derniere = recue.recue_le if recue else None
        return Response({
            'mode':                 'miroir' if miroir else 'travail',
            'lecture_seule':        miroir,
            'derniere_publication': derniere,
        })
