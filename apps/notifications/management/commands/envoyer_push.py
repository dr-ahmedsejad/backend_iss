"""Pousse vers les téléphones les notifications récentes pas encore poussées.

Reprise du SIGA-PRIVE. À lancer régulièrement sur le serveur auprès duquel les
téléphones s'inscrivent — aujourd'hui le VPS de l'ISS ; le miroir le jour où il
existera — cron toutes les 2 minutes (deploy/cron/iss-push). Toute notification
de la cloche part ainsi vers le téléphone de son destinataire, quelle que soit
son origine : emploi du temps validé, réclamation traitée…

Chaque notification n'est traitée qu'une fois (PushEnvoye) : la ligne est prise
AVANT l'envoi (deux exécutions qui se chevauchent ne poussent pas deux fois), et
rendue si l'envoi échoue pour une raison passagère (réseau), pour réessayer.
Une notification déjà lue, ou plus ancienne que la fenêtre, n'est pas poussée.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone

from apps.notifications import push
from apps.notifications.models import AppareilPush, Notification, NotificationLecture, PushEnvoye


class Command(BaseCommand):
    help = 'Envoie les notifications push en attente (Firebase Cloud Messaging).'

    def add_arguments(self, parser):
        parser.add_argument('--depuis-heures', type=int, default=48,
                            help='Fenêtre : notifications créées depuis N heures (défaut 48).')
        parser.add_argument('--essai', action='store_true',
                            help="Affiche ce qui serait envoyé, sans rien envoyer ni noter.")

    def handle(self, *args, depuis_heures, essai, **opts):
        if not push.configure():
            self.stdout.write('Push désactivé (FIREBASE_CREDENTIALS absent ou illisible).')
            return

        depuis = timezone.now() - timedelta(hours=depuis_heures)
        notifs = list(Notification.objects.filter(created_at__gte=depuis).order_by('created_at'))
        if not notifs:
            return
        deja = set(PushEnvoye.objects.filter(notification_le__gte=depuis)
                   .values_list('notification_id', 'notification_le'))
        lues_en_ligne = set(NotificationLecture.objects
                            .filter(notification_id__in=[n.pk for n in notifs])
                            .values_list('notification_id', 'user_id'))
        appareils = {}
        for a in AppareilPush.objects.filter(user_id__in={n.destinataire_id for n in notifs}):
            appareils.setdefault(a.user_id, []).append(a)

        envoyees = sans_appareil = erreurs = 0
        for n in notifs:
            if (n.pk, n.created_at) in deja:
                continue
            cibles = appareils.get(n.destinataire_id, [])
            deja_lue = n.lue or (n.pk, n.destinataire_id) in lues_en_ligne
            if essai:
                self.stdout.write(f'#{n.pk} → user #{n.destinataire_id} : {len(cibles)} appareil(s)'
                                  f'{" (déjà lue)" if deja_lue else ""} — {n.titre}')
                continue
            try:
                trace = PushEnvoye.objects.create(notification_id=n.pk, notification_le=n.created_at)
            except IntegrityError:
                continue  # prise par une exécution concurrente
            if deja_lue or not cibles:
                sans_appareil += 1
                continue
            ok = 0
            for a in cibles:
                # Pas de version arabe des notifications à l'ISS (le modèle
                # n'en porte pas, contrairement au privé) : le français pour tous.
                try:
                    push.envoyer(a.jeton, n.titre, n.message,
                                 {'notification_id': n.pk, 'type': n.type, 'lien': n.lien})
                    ok += 1
                except push.JetonInvalide:
                    a.delete()  # app désinstallée / jeton périmé
                except Exception as e:  # réseau, quota : on réessaiera
                    erreurs += 1
                    self.stderr.write(f'#{n.pk} : {e}')
            if ok == 0 and erreurs:
                trace.delete()
            else:
                trace.nb_appareils = ok
                trace.save(update_fields=['nb_appareils'])
                envoyees += ok

        if envoyees or erreurs:
            self.stdout.write(f'{envoyees} envoi(s), {sans_appareil} sans appareil, {erreurs} erreur(s).')
