"""
Prévenir quelqu'un — sans rien ajouter au socle.

`apps.notifications` porte le modèle `Notification` mais pas de fonction pour
l'écrire : à l'ESP, un `apps/notifications/services.py` avait été ajouté. On ne
le reproduit pas ici. Tout ce que ce chantier introduit vit dans `apps/edt/`,
et rien d'autre : c'est ce qui rend le retrait du moteur aussi simple que
supprimer ce dossier.

Le modèle, lui, est lu tel quel — on ne le modifie pas non plus.
"""
import logging

logger = logging.getLogger('siga')


def notifier(destinataires, titre, message, type='info', lien='', auteur=None):
    """
    Crée une notification pour chaque destinataire. Retourne le nombre émis.

    `destinataires` accepte un utilisateur, une liste ou un queryset. Les
    doublons sont écartés, et l'auteur ne s'annonce jamais à lui-même : une
    demande qu'on vient d'émettre n'a pas à revenir dans sa propre cloche.
    """
    from apps.notifications.models import Notification

    if destinataires is None:
        return 0
    if hasattr(destinataires, 'pk'):
        destinataires = [destinataires]

    lignes, vus = [], set()
    for u in destinataires:
        if u is None or u.pk in vus:
            continue
        if auteur is not None and u.pk == auteur.pk:
            continue
        vus.add(u.pk)
        lignes.append(Notification(
            destinataire=u, titre=titre[:200], message=message,
            type=type, lien=lien[:500],
        ))

    if not lignes:
        return 0
    Notification.objects.bulk_create(lignes)
    logger.info('EDT : %s notification(s) — %s', len(lignes), titre)
    return len(lignes)
