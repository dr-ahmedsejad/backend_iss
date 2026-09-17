"""
Demandes de libération d'une salle occupée.

La case s'affiche prise, avec le nom du groupe qui la détient, et ne s'écrase
pas. Celui qui la veut adresse une demande ; **le détenteur seul décide** —
personne ne passe outre, pas même la direction. C'est la règle arrêtée avec
l'ESP : le partage du temps entre planificateurs est une convention entre eux,
que le système n'a pas à trancher à leur place.

Accorder libère la **salle**, jamais la séance : le cours a toujours lieu, il se
tiendra ailleurs. Supprimer la séance de quelqu'un d'autre serait une décision
pédagogique, hors de portée d'une demande de salle.
"""
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from .notifier import notifier
from core.mixins import AuditMixin
from core.permissions import RBACPermission

from .models import DemandeLiberation
from .serializers import DemandeLiberationSerializer
from .services.partage import propager


def detenteurs(seance):
    """
    Qui peut décider du sort de cette séance.

    Ceux à qui son groupe est délégué — exactement les personnes qui peuvent
    déjà la modifier. La direction n'y figure pas, par choix : le détenteur
    seul libère.

    L'ESP y ajoutait les responsables du pôle dont relevait l'enseignement.
    L'ISS n'a pas de pôles : le groupe délégué est le seul titre à faire valoir.
    """
    from apps.authentication.models import CustomUser

    ids = set(seance.departement.edt_managers.values_list('id', flat=True))
    return list(CustomUser.objects.filter(pk__in=ids, is_active=True))


class DemandeLiberationViewSet(AuditMixin, viewsets.ModelViewSet):
    queryset = DemandeLiberation.objects.select_related(
        'seance', 'seance__departement', 'seance__semaine',
        'seance__semaine__jour_fk', 'seance__creneau_fk', 'seance__em',
        'seance__em__module_lmd', 'seance__prof', 'seance__type_seance_fk',
        'salle', 'demandeur', 'decidee_par').all()
    serializer_class   = DemandeLiberationSerializer
    permission_classes = [RBACPermission]
    required_module    = 'emplois'
    filter_backends    = [DjangoFilterBackend, OrderingFilter]
    filterset_fields   = ['statut', 'salle', 'seance', 'demandeur']
    ordering           = ['-date_demande']
    pagination_class   = None

    # ── Demander ─────────────────────────────────────────────────────────────
    def perform_create(self, serializer):
        seance = serializer.validated_data['seance']
        if seance.salle_id is None:
            raise ValidationError(
                {'seance': "Cette séance n'occupe aucune salle : "
                           "il n'y a rien à libérer."})

        proprietaires = detenteurs(seance)
        if self.request.user.pk in {u.pk for u in proprietaires}:
            raise ValidationError(
                {'seance': "Cette séance relève déjà de votre périmètre : "
                           "modifiez-la directement."})

        # Une seule demande en cours par (séance, demandeur). La contrainte de
        # base le garantit, mais la laisser lever une IntegrityError donnerait
        # une 500 — et surtout empoisonnerait la transaction en cours. Relancer
        # n'apporte rien : le détenteur est déjà prévenu.
        if DemandeLiberation.objects.filter(
                seance=seance, demandeur=self.request.user,
                statut=DemandeLiberation.DEMANDEE).exists():
            raise ValidationError(
                {'seance': "Vous avez déjà une demande en cours sur cette "
                           "séance. Le responsable en a été informé."})

        demande = serializer.save(
            demandeur=self.request.user, salle=seance.salle,
            statut=DemandeLiberation.DEMANDEE)

        notifier(
            proprietaires, auteur=self.request.user, type='avertissement',
            titre=f'Salle {demande.salle.nom} demandée — {seance.departement.nom}',
            message=(
                f'{self.request.user} demande la libération de la salle '
                f'{demande.salle.nom}, occupée par votre séance du '
                f'{seance.semaine.jour_fk.jour} {seance.semaine.date} '
                f'({seance.creneau_fk.creneau}). '
                f'Motif : {demande.motif or "(non précisé)"}. '
                f'Vous seul pouvez accorder ou refuser.'),
            lien='/dashboard/edt/semaine',
        )

    # ── Décider ──────────────────────────────────────────────────────────────
    def _exiger_detenteur(self, demande):
        user = self.request.user
        if user.is_superuser:
            return
        if user.pk not in {u.pk for u in detenteurs(demande.seance)}:
            raise PermissionDenied(
                "Seul le responsable de la séance qui occupe cette salle peut "
                "décider de la libérer.")

    def _clore(self, request, statut, libere):
        demande = self.get_object()
        self._exiger_detenteur(demande)
        if demande.statut != DemandeLiberation.DEMANDEE:
            return Response(
                {'detail': f'Cette demande est déjà '
                           f'{demande.get_statut_display().lower()}.'},
                status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if libere:
                # La SALLE est libérée, pas la séance : le cours a toujours lieu.
                seance = demande.seance
                # Sur un férié isolé la séance est figée : elle doit revenir
                # telle quelle, salle comprise, au retrait du férié.
                from apps.parametres.feries import refuser_sur_ferie_isole
                refuser_sur_ferie_isole(seance.semaine, 'libérer la salle d’')
                seance.salle = None
                seance.save(update_fields=['salle'])
                # Un cours partagé se tient dans une seule salle : les séances
                # sœurs doivent suivre, sinon la clé de fusion du socle les
                # sépare et l'enseignant se retrouve payé deux fois.
                if seance.cle_partage:
                    propager(seance)

            demande.statut        = statut
            demande.decidee_par   = request.user
            demande.date_decision = timezone.now()
            demande.reponse       = (request.data.get('reponse') or '').strip()
            demande.save(update_fields=['statut', 'decidee_par',
                                        'date_decision', 'reponse'])

        notifier(
            demande.demandeur, auteur=request.user,
            type='succes' if libere else 'avertissement',
            titre=(f'Salle {demande.salle.nom} '
                   f'{"libérée" if libere else "non libérée"}'),
            message=(
                f'{request.user} a {"accordé" if libere else "refusé"} votre '
                f'demande sur la salle {demande.salle.nom}. '
                + (f'Réponse : {demande.reponse}' if demande.reponse else '')),
            lien='/dashboard/edt/semaine',
        )
        return Response(self.get_serializer(demande).data)

    @action(detail=True, methods=['post'], url_path='accorder')
    def accorder(self, request, pk=None):
        return self._clore(request, DemandeLiberation.ACCORDEE, libere=True)

    @action(detail=True, methods=['post'], url_path='refuser')
    def refuser(self, request, pk=None):
        return self._clore(request, DemandeLiberation.REFUSEE, libere=False)

    @action(detail=False, methods=['get'], url_path='a-traiter')
    def a_traiter(self, request):
        """Les demandes que CET utilisateur peut trancher."""
        en_cours = self.get_queryset().filter(statut=DemandeLiberation.DEMANDEE)
        miennes = [d for d in en_cours
                   if request.user.is_superuser
                   or request.user.pk in {u.pk for u in detenteurs(d.seance)}]
        return Response(self.get_serializer(miennes, many=True).data)
