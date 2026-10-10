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

Débit : les envois partent par 10 en parallèle (fils), chacun sur sa
connexion réutilisée — 1000 téléphones en quelques secondes au lieu de
plusieurs minutes. La base n'est lue et écrite que par le fil principal.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone

from apps.authentication.models import CustomUser
from apps.notifications import push
from apps.notifications.models import AppareilPush, Notification, NotificationLecture, PushEnvoye


ENVOIS_SIMULTANES = 10


def _envoyer(job):
    """Un envoi (dans un fil, sans base) : 'ok', 'invalide' ou le message d'erreur."""
    n, appareil, cle, profil = job
    titre = n.titre
    donnees = {'notification_id': n.pk, 'type': n.type, 'lien': n.lien}
    if appareil.projet == 'gp':
        # Une app pour tous les établissements : le sigle en tête du titre, et
        # l'établissement (et le profil) pour que le toucher ouvre le bon espace.
        titre = f'{settings.ETABLISSEMENT_SIGLE} — {n.titre}'
        donnees.update(etablissement=settings.ETABLISSEMENT_CODE, profil=profil)
    try:
        # Pas de version arabe des notifications à l'ISS (le modèle n'en porte
        # pas, contrairement au privé) : le français pour tous.
        push.envoyer(appareil.jeton, titre, n.message, donnees, chemin=cle)
        return 'ok'
    except push.JetonInvalide:
        return 'invalide'
    except Exception as e:  # réseau, quota : on réessaiera
        return f'{e}' or e.__class__.__name__


class Command(BaseCommand):
    help = 'Envoie les notifications push en attente (Firebase Cloud Messaging).'

    def add_arguments(self, parser):
        parser.add_argument('--depuis-heures', type=int, default=48,
                            help='Fenêtre : notifications créées depuis N heures (défaut 48).')
        parser.add_argument('--essai', action='store_true',
                            help="Affiche ce qui serait envoyé, sans rien envoyer ni noter.")

    def handle(self, *args, depuis_heures, essai, **opts):
        # Deux apps, deux projets Firebase : la clé suit le rôle du destinataire.
        # Valeur : chemin de la clé (None = celle de l'app étudiante) ; absente
        # = pas de clé pour cette app, ses téléphones sont ignorés.
        cles = {}
        if push.configure():
            cles['etudiant'] = None
        if push.cle_enseignant() and push.configure(push.cle_enseignant()):
            cles['enseignant'] = push.cle_enseignant()
        if push.cle_gp() and push.configure(push.cle_gp()):
            cles['gp'] = push.cle_gp()
        if not cles:
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
        enseignants = set(CustomUser.objects.filter(pk__in=appareils, role='enseignant')
                          .values_list('pk', flat=True))

        envoyees = sans_appareil = erreurs = 0
        a_envoyer = []   # (notification, sa trace PushEnvoye, ses appareils, clé)
        for n in notifs:
            if (n.pk, n.created_at) in deja:
                continue
            app = 'enseignant' if n.destinataire_id in enseignants else 'etudiant'
            cle = cles.get(app)
            # La clé suit l'app du téléphone : Groupe Polytechnique (projet 'gp')
            # ou, pour les anciennes apps, le rôle du destinataire.
            cibles = [a for a in appareils.get(n.destinataire_id, [])
                      if (('gp' in cles) if a.projet == 'gp' else (app in cles))]
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
            a_envoyer.append((n, trace, cibles, cle))

        # Envois en parallèle (sans base) ; résultats dans l'ordre des jobs.
        jobs = [(n, a, cles['gp'] if a.projet == 'gp' else cle,
                 'enseignant' if n.destinataire_id in enseignants else 'etudiant')
                for n, _, cibles, cle in a_envoyer for a in cibles]
        with ThreadPoolExecutor(max_workers=ENVOIS_SIMULTANES) as pool:
            resultats = list(pool.map(_envoyer, jobs))
        par_appareil = {(id(n), a.pk): r for (n, a, _, _), r in zip(jobs, resultats)}

        # Bilan par notification (fil principal : base).
        for n, trace, cibles, _ in a_envoyer:
            ok = echecs = 0
            for a in cibles:
                r = par_appareil[(id(n), a.pk)]
                if r == 'ok':
                    ok += 1
                elif r == 'invalide':
                    a.delete()  # app désinstallée / jeton périmé
                else:
                    echecs += 1
                    erreurs += 1
                    self.stderr.write(f'#{n.pk} : {r}')
            if ok == 0 and echecs:
                trace.delete()  # rendue : réessayée au prochain passage
            else:
                trace.nb_appareils = ok
                trace.save(update_fields=['nb_appareils'])
                envoyees += ok

        if envoyees or erreurs:
            self.stdout.write(f'{envoyees} envoi(s), {sans_appareil} sans appareil, {erreurs} erreur(s).')
