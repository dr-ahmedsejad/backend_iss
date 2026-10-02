"""
Publier : le serveur de travail envoie sa base au miroir.

Le chemin, et pourquoi chaque étape :

  1. REFUSÉ sur le miroir — par l'intercepteur ET ici. Un miroir qui publierait
     écraserait l'autre miroir avec une copie elle-même écrasable ;
  2. pg_dump en SQL, avec --clean --if-exists (le receveur rejoue sur une base
     existante) et les DEUX niveaux d'exclusion des réglages :
       --exclude-table       : boîte de réception et tables propres à
                               l'instance — ni DROP, ni CREATE, ni données ;
       --exclude-table-data  : journaux qu'on accepte de voir vidés ;
  3. empreinte sha256 du dump ;
  4. SANS cible SSH : on s'arrête, rien n'est transféré, et la réponse LE DIT ;
     AVEC cible : le dump part sur l'entrée standard d'une commande SSH, son
     empreinte en ARGUMENT. Côté miroir, la clé n'autorise qu'une commande
     forcée (deploy/miroir/recevoir-publication.sh), qui compare l'empreinte
     AVANT de toucher à quoi que ce soit, sauvegarde, restaure, et revient à la
     sauvegarde en cas d'échec ;
  5. TOUJOURS une ligne de journal, succès comme échec.

Le schéma des tables exclues ne voyage pas : il n'évolue que par les
migrations du miroir. Ordre de déploiement, à chaque changement de schéma de
la boîte de réception : le MIROIR d'abord (code + migrate), le serveur de
travail ensuite, publier en dernier. Publier avant de migrer le miroir fait
échouer la restauration — le receveur revient à sa sauvegarde : rien n'est
perdu, mais rien ne passe.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import time

from django.conf import settings

from core.mirror import est_miroir

from .models import PublicationJournal


class PublicationRefusee(Exception):
    pass


def commande_pg_dump(fichier: str) -> list:
    """La commande exacte passée à pg_dump — testée telle quelle."""
    db = settings.DATABASES['default']
    args = [
        settings.SYNC_PG_DUMP_BIN,
        '--host', str(db.get('HOST') or 'localhost'),
        '--port', str(db.get('PORT') or '5432'),
        '--username', str(db.get('USER') or ''),
        '--dbname', str(db.get('NAME') or ''),
        '--format', 'plain',
        '--clean', '--if-exists',
        '--no-owner', '--no-acl',
        '--file', fichier,
    ]
    for table in settings.SYNC_EXCLUDE_TABLE:
        args += ['--exclude-table', table]
    for table in settings.SYNC_EXCLUDE_TABLE_DATA:
        args += ['--exclude-table-data', table]
    return args


def commande_ssh(sha256: str) -> list:
    """Une seule commande SSH, l'empreinte en argument. Le reste de la ligne
    est ignoré par la commande forcée côté miroir, qui ne lit que l'empreinte
    (SSH_ORIGINAL_COMMAND)."""
    args = [settings.SYNC_SSH_BIN,
            '-p', str(settings.SYNC_SSH_PORT),
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes']
    if settings.SYNC_SSH_KEY:
        args += ['-i', settings.SYNC_SSH_KEY]
    args += [f'{settings.SYNC_SSH_USER}@{settings.SYNC_SSH_HOST}', 'publier', sha256]
    return args


def _empreinte(chemin: str) -> str:
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1024 * 1024), b''):
            h.update(bloc)
    return h.hexdigest()


def _environnement_pg() -> dict:
    env = dict(os.environ)
    env['PGPASSWORD'] = str(settings.DATABASES['default'].get('PASSWORD') or '')
    return env


def publier(utilisateur) -> PublicationJournal:
    """Construit le dump, l'envoie si une cible est configurée, et journalise.

    Lève PublicationRefusee sur le miroir. Pour tout le reste, rend la ligne
    de journal — en échec si quelque chose a cassé, jamais sans trace.
    """
    if est_miroir():
        raise PublicationRefusee(
            "Une publication ne part que du serveur de travail, jamais du miroir.")

    debut = time.monotonic()
    journal = PublicationJournal(
        par_id=getattr(utilisateur, 'pk', None),
        par_nom=(getattr(utilisateur, 'name', '') or getattr(utilisateur, 'username', '') or '')[:150],
        statut=PublicationJournal.STATUT_ECHEC,
    )
    dossier = tempfile.mkdtemp(prefix='publication-', dir=settings.SYNC_WORKDIR)
    try:
        fichier = os.path.join(dossier, 'publication.sql')
        r = subprocess.run(commande_pg_dump(fichier), env=_environnement_pg(),
                           capture_output=True, text=True, timeout=settings.SYNC_TIMEOUT_S)
        if r.returncode != 0:
            raise RuntimeError('pg_dump a échoué : ' + (r.stderr or '').strip()[-2000:])

        journal.taille = os.path.getsize(fichier)
        journal.sha256 = _empreinte(fichier)

        if not settings.SYNC_SSH_HOST:
            journal.statut = PublicationJournal.STATUT_CONSTRUIT
            journal.reponse_vps = ("Aucune cible configurée (SYNC_SSH_HOST vide) : "
                                   "le dump a été construit, RIEN n'a été transféré.")
            return journal

        with open(fichier, 'rb') as entree:
            r = subprocess.run(commande_ssh(journal.sha256), stdin=entree,
                               capture_output=True, timeout=settings.SYNC_TIMEOUT_S)
        sortie = (r.stdout or b'').decode('utf-8', 'replace').strip()
        erreur = (r.stderr or b'').decode('utf-8', 'replace').strip()
        journal.transfere = True
        journal.reponse_vps = (sortie or erreur)[-4000:]
        if r.returncode == 0 and sortie.startswith('OK'):
            journal.statut = PublicationJournal.STATUT_PUBLIE
        else:
            journal.erreur = (f'Le miroir a répondu {r.returncode} : ' + (erreur or sortie))[-4000:]
        return journal
    except Exception as exc:                                  # noqa: BLE001
        journal.statut = PublicationJournal.STATUT_ECHEC
        journal.erreur = str(exc)[-4000:]
        return journal
    finally:
        journal.duree_s = round(time.monotonic() - debut, 2)
        journal.save()
        shutil.rmtree(dossier, ignore_errors=True)


def plan() -> dict:
    """Ce que l'écran affiche avant d'agir — tiré des réglages, pas d'un
    texte écrit à la main qui pourrait promettre ce qu'il ne fait pas."""
    return {
        'publie': "Toute la base du serveur de travail, sauf les tables ci-dessous.",
        'preserve': list(settings.SYNC_EXCLUDE_TABLE),
        'vide': list(settings.SYNC_EXCLUDE_TABLE_DATA),
        'protege': [
            "Empreinte sha256 comparée par le miroir avant toute écriture.",
            "Sauvegarde de la base du miroir avant la restauration.",
            "Restauration en une transaction (ON_ERROR_STOP) ; en cas d'échec, retour à la sauvegarde.",
            "Service redémarré dans tous les cas.",
            "Chaque publication est journalisée, succès comme échec.",
        ],
        'cible_configuree': bool(settings.SYNC_SSH_HOST),
    }
