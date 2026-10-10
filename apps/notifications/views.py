from django.db.models import Exists, OuterRef, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import AnonRateThrottle
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from core.mirror import est_miroir

from .models import AppareilPush, Notification, NotificationLecture
from .serializers import NotificationSerializer


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    # Pas de RBAC ici : le queryset isole strictement par destinataire,
    # donc tout user authentifié ne voit que ses propres notifications.
    #
    # Sur le MIROIR, `lue` vit dans une table publiée : chaque publication la
    # remettrait à faux. La lecture faite en ligne est rangée dans
    # `NotificationLecture` (boîte de réception), et « lue » = lue sur le
    # serveur de travail OU lue en ligne. Sur le serveur de travail, rien ne
    # change.
    serializer_class   = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [DjangoFilterBackend]
    filterset_fields   = ['lue', 'type']

    def get_queryset(self):
        qs = Notification.objects.filter(destinataire=self.request.user)
        if est_miroir():
            qs = qs.annotate(lue_en_ligne=Exists(NotificationLecture.objects.filter(
                notification_id=OuterRef('pk'), user_id=self.request.user.pk)))
        return qs

    def filter_queryset(self, queryset):
        if not est_miroir():
            return super().filter_queryset(queryset)
        p = self.request.query_params
        if p.get('type'):
            queryset = queryset.filter(type=p['type'])
        lue = (p.get('lue') or '').lower()
        if lue in ('true', '1'):
            queryset = queryset.filter(Q(lue=True) | Q(lue_en_ligne=True))
        elif lue in ('false', '0'):
            queryset = queryset.filter(lue=False, lue_en_ligne=False)
        return queryset

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        qs = self.get_queryset().filter(lue=False)
        if est_miroir():
            qs = qs.filter(lue_en_ligne=False)
        return Response({'count': qs.count()})

    @action(detail=True, methods=['post'], url_path='lire')
    def lire(self, request, pk=None):
        notif = self.get_object()
        if est_miroir():
            NotificationLecture.objects.get_or_create(notification_id=notif.pk,
                                                      user_id=request.user.pk)
            notif.lue_en_ligne = True
        else:
            notif.lue = True
            notif.save(update_fields=['lue'])
        return Response(NotificationSerializer(notif).data)

    @action(detail=False, methods=['post', 'delete'], url_path='appareils')
    def appareils(self, request):
        """POST : inscrit le téléphone aux notifications push (jeton FCM).
        DELETE : le désinscrit (déconnexion). Corps : {"jeton", "plateforme", "langue"}.

        L'application étudiante appelle cette adresse à chaque connexion. Écrit
        dans la boîte de réception (notifications_appareil) : autorisé sur le
        miroir et jamais effacé par la publication."""
        jeton = str(request.data.get('jeton') or '').strip()
        if not jeton or len(jeton) > 512:
            return Response({'detail': 'Jeton requis.'}, status=status.HTTP_400_BAD_REQUEST)
        if request.method == 'DELETE':
            AppareilPush.objects.filter(jeton=jeton, user_id=request.user.pk).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        plateforme = str(request.data.get('plateforme') or 'android')[:20]
        langue = 'ar' if str(request.data.get('langue') or '').startswith('ar') else 'fr'
        # App Groupe Polytechnique : son propre projet Firebase (voir AppareilPush.projet).
        projet = 'gp' if str(request.data.get('projet') or '') == 'gp' else ''
        # Un jeton = un appareil : s'il passe à un autre compte, il le suit.
        _, cree = AppareilPush.objects.update_or_create(
            jeton=jeton, defaults={'user_id': request.user.pk, 'plateforme': plateforme,
                                   'langue': langue, 'projet': projet})
        return Response({'detail': 'Appareil inscrit.'},
                        status=status.HTTP_201_CREATED if cree else status.HTTP_200_OK)

    # ── Sans session : l'app Groupe Polytechnique n'a qu'une session à la fois,
    # mais reste inscrite auprès de chaque établissement où l'on s'est connecté.
    # Le jeton FCM (long, connu du seul téléphone et des serveurs) fait foi.
    # Réponse 204 dans tous les cas : rien à apprendre en interrogeant.
    @action(detail=False, methods=['post'], url_path='appareils/oublier',
            permission_classes=[AllowAny], authentication_classes=[],
            throttle_classes=[AnonRateThrottle])
    def oublier_appareil(self, request):
        """Session expirée, « Ce n'est pas moi » : ce téléphone ne reçoit plus
        les notifications de CET établissement. Corps : {"jeton"}."""
        jeton = str(request.data.get('jeton') or '').strip()
        if not jeton or len(jeton) > 512:
            return Response({'detail': 'Jeton requis.'}, status=status.HTTP_400_BAD_REQUEST)
        AppareilPush.objects.filter(jeton=jeton).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='appareils/remplacer',
            permission_classes=[AllowAny], authentication_classes=[],
            throttle_classes=[AnonRateThrottle])
    def remplacer_jeton(self, request):
        """Firebase a renouvelé le jeton du téléphone : l'inscription suit le
        nouveau, sans session. Corps : {"ancien", "nouveau"}."""
        ancien = str(request.data.get('ancien') or '').strip()
        nouveau = str(request.data.get('nouveau') or '').strip()
        if not ancien or not nouveau or len(ancien) > 512 or len(nouveau) > 512:
            return Response({'detail': 'Jetons requis.'}, status=status.HTTP_400_BAD_REQUEST)
        appareil = AppareilPush.objects.filter(jeton=ancien).first()
        if appareil is not None and ancien != nouveau:
            AppareilPush.objects.filter(jeton=nouveau).exclude(pk=appareil.pk).delete()
            appareil.jeton = nouveau
            appareil.save(update_fields=['jeton'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='tout-lire')
    def tout_lire(self, request):
        if est_miroir():
            non_lues = list(self.get_queryset().filter(lue=False, lue_en_ligne=False)
                            .values_list('pk', flat=True))
            NotificationLecture.objects.bulk_create(
                [NotificationLecture(notification_id=pk, user_id=request.user.pk) for pk in non_lues],
                ignore_conflicts=True)
            return Response({'updated': len(non_lues)})
        updated = self.get_queryset().filter(lue=False).update(lue=True)
        return Response({'updated': updated})
