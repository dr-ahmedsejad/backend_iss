"""
Prévenir les étudiants que l'emploi du temps de leur semaine est validé.

L'emploi du temps planifié reste provisoire tant que le suivi de la semaine
n'est pas généré : la génération en fait le vrai, celui que le portail et
l'application étudiante affichent (ils lisent le suivi). C'est donc elle qui
déclenche l'annonce — appelée par `generation.py`, après une génération
réussie, jamais avant.

La notification va dans la cloche ; `envoyer_push` (apps/notifications) la
pousse ensuite vers le téléphone, et l'app ouvre l'onglet Emploi du temps au
toucher (le lien finit par « /emploi »).

Rien ici ne touche au suivi, au pointage ni à la paie : on LIT le suivi généré,
on ÉCRIT des notifications et la trace `AnnonceEmploi`.
"""
import logging

from django.db import transaction
from django.db.models import F, Max, Min
from django.utils import timezone

from .notifier import notifier

logger = logging.getLogger('siga')

LIEN_EMPLOI = '/dashboard/portail/emploi'


def groupes_generes(annee, type_semestre, numero):
    """Les groupes qui ont un suivi pour cette semaine."""
    from apps.suivi.models import Suivie
    return set(Suivie.objects.filter(
        annee_universitaire=annee, type_semestre=type_semestre, numero_semaine=numero,
        departement__isnull=False).values_list('departement_id', flat=True).distinct())


def _periode(annee, type_semestre, numero):
    """« du 03/11 au 08/11 », ou '' si le calendrier ne connaît pas la semaine."""
    from apps.parametres.models import Semaine
    b = Semaine.objects.filter(annee_universitaire=annee, type_semestre=type_semestre,
                               numero_semaine=numero).aggregate(debut=Min('date'), fin=Max('date'))
    if not b['debut']:
        return ''
    return 'du %s au %s' % (b['debut'].strftime('%d/%m'), b['fin'].strftime('%d/%m'))


def _textes(numero, periode, modifie):
    if modifie:
        titre = 'Emploi du temps de la semaine %s modifié' % numero
        message = ('Votre emploi du temps %s a été modifié. Consultez la nouvelle '
                   'version dans l\'onglet Emploi du temps.'
                   % (periode or 'de la semaine %s' % numero))
    else:
        titre = 'Emploi du temps de la semaine %s validé' % numero
        message = ('Votre emploi du temps %s est validé. Consultez-le dans '
                   'l\'onglet Emploi du temps.' % (periode or 'de la semaine %s' % numero))
    return titre, message


@transaction.atomic
def annoncer_semaine(annee, type_semestre, numero, departements):
    """Annonce la semaine aux étudiants (avec un compte actif) de ces groupes.

    Une première annonce dit « validé », une suivante « modifié ». Retourne
    {'valides': n, 'modifies': n} — le nombre d'étudiants prévenus.
    """
    from apps.absence.models import Etudiant
    from .models import AnnonceEmploi

    premiers, de_nouveau = [], []
    for dep in sorted({int(d) for d in departements}):
        trace, cree = AnnonceEmploi.objects.get_or_create(
            annee_universitaire=annee, type_semestre=type_semestre,
            numero_semaine=numero, departement_id=dep)
        if cree:
            premiers.append(dep)
        else:
            AnnonceEmploi.objects.filter(pk=trace.pk).update(
                nb_annonces=F('nb_annonces') + 1, derniere_le=timezone.now())
            de_nouveau.append(dep)

    periode = _periode(annee, type_semestre, numero)
    bilan = {}
    for cle, groupes, modifie in (('valides', premiers, False), ('modifies', de_nouveau, True)):
        if not groupes:
            bilan[cle] = 0
            continue
        etudiants = [e.user for e in Etudiant.objects
                     .filter(departement_id__in=groupes, user__isnull=False, user__is_active=True)
                     .select_related('user')]
        titre, message = _textes(numero, periode, modifie)
        bilan[cle] = notifier(etudiants, titre, message, type='info', lien=LIEN_EMPLOI)
    logger.info('Emploi S%s %s %s annoncé : %s', numero, annee, type_semestre, bilan)
    return bilan
