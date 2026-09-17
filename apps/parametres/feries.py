"""
Les JOURS FÉRIÉS, et ce qu'ils font à l'emploi du temps.

Une ligne de `Semaine` est un JOUR : on peut donc fermer un seul jour sans
toucher à sa semaine. Jusqu'ici on ne pouvait fermer qu'une semaine ENTIÈRE, et
elle perdait son numéro — toutes les suivantes étaient renumérotées. Fermer le
samedi 28 novembre décalait donc tout le semestre, et le suivi avec.

LA CONVENTION — tout repose sur elle
  * un jour férié ISOLÉ garde le numéro de sa semaine et prend le type 'ferie' ;
  * une semaine ENTIÈRE hors cours garde son comportement (numéro NULL).
Appartenir à la séquence pédagogique, c'est AVOIR UN NUMÉRO — pas avoir le type
« cours ». Les traitements qui PLACENT des séances (duplication, permutation)
continuent de ne retenir que les jours « cours ».

Garder le numéro n'est pas un détail : le suivi retrouve les dates d'une semaine
par son numéro, sans filtrer le type (`apps/suivi/views.py`, génération). Un
férié sans numéro perdrait sa date.

Les séances du jour sont ANNULÉES, pas supprimées, avec le motif `ferie` : la
projection vers `Emplois` écarte les séances annulées, donc ni pointage ni paie,
et le retrait du férié peut rendre CES séances-là — jamais une annulation
décidée à la main.
"""
import datetime as dt

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

JOURS_SEMAINE = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']


class Conflit(APIException):
    """409 : l'état du calendrier interdit le geste (un suivi est généré)."""
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'Conflit.'


# ── Lire l'état d'un jour ────────────────────────────────────────────────────

def est_ferie_isole(ligne) -> bool:
    """Un jour férié AU MILIEU d'une semaine de cours : il garde son numéro."""
    from apps.parametres.models import Semaine
    return (ligne.type_semaine == Semaine.TYPE_FERIE
            and ligne.numero_semaine is not None)


def est_ferme(ligne) -> bool:
    """Un jour où l'on ne PLACE pas de séance : tout ce qui n'est pas « cours »."""
    from apps.parametres.models import Semaine
    return ligne.type_semaine != Semaine.TYPE_COURS


def libelle_du_jour(ligne) -> str:
    """« Samedi 28/11/2026 »."""
    nom = ligne.jour_fk.jour if ligne.jour_fk_id else JOURS_SEMAINE[ligne.date.weekday()]
    return '%s %s' % (nom, ligne.date.strftime('%d/%m/%Y'))


def _pluriel(n, mot):
    return '%d %s%s' % (n, mot, 's' if n > 1 else '')


def _refuser_si_suivi(ligne, geste):
    """Un suivi généré sur la semaine fige ce qui a été pointé et payé."""
    from apps.suivi.models import Suivie
    if ligne.numero_semaine is None:
        return
    if Suivie.objects.filter(annee_universitaire=ligne.annee_universitaire,
                             type_semestre=ligne.type_semestre,
                             numero_semaine=ligne.numero_semaine).exists():
        raise Conflit(
            "%s impossible : le suivi de la semaine %s est déjà généré. "
            "Supprimez d'abord ce suivi (Suivi → Générer, suppression de la "
            "semaine), puis recommencez." % (geste, ligne.numero_semaine))


# ── Les refus sur une séance d'un jour fermé ─────────────────────────────────
#
# Appelés depuis les VUES, pas depuis le sérialiseur : une erreur levée dans
# `validate()` arrive au front sous `errors.non_field_errors`, que `apiFetch` ne
# lit pas — le message s'afficherait en JSON brut.

def refuser_ajout(ligne):
    """AJOUTER une séance : refusé sur TOUT jour hors cours."""
    if ligne is not None and est_ferme(ligne):
        raise ValidationError(
            "%s n'est pas un jour de cours (%s) : on n'y ajoute pas de séance."
            % (libelle_du_jour(ligne), ligne.get_type_semaine_display().lower()))


def refuser_sur_ferie_isole(ligne, geste):
    """MODIFIER, SUPPRIMER, PARTAGER : refusé sur un férié ISOLÉ seulement.

    Supprimée, la séance ne pourrait plus être rétablie au retrait du férié ;
    partagée, ses copies ne porteraient pas le motif. Une semaine entière de
    vacances n'a pas de rétablissement : y bloquer la suppression empêcherait de
    la nettoyer.
    """
    if ligne is not None and est_ferie_isole(ligne):
        raise ValidationError(
            "%s est férié%s : on ne peut pas %s une séance de ce jour. Elle sera "
            "rétablie telle quelle si le férié est retiré."
            % (libelle_du_jour(ligne),
               ' (%s)' % ligne.description if ligne.description else '', geste))


# ── Marquer et retirer ───────────────────────────────────────────────────────

@transaction.atomic
def marquer_jour_ferie(ligne, libelle) -> dict:
    """Ferme UN jour. Il garde son numéro ; rien d'autre n'est renuméroté.

    Ses séances sont annulées avec le motif `ferie`. Une séance déjà annulée à
    la main le reste, et garde son motif vide : le retrait ne la rétablira pas.
    """
    from apps.edt.models import SeanceReelle
    from apps.parametres.models import Semaine

    libelle = (libelle or '').strip()
    if not libelle:
        raise ValidationError('Donnez un nom au jour férié (ex. « Fête de l’indépendance »).')
    if ligne.numero_semaine is None:
        raise ValidationError(
            "%s appartient à une semaine entière hors cours (%s) : elle est déjà "
            "fermée." % (libelle_du_jour(ligne), ligne.get_type_semaine_display().lower()))
    if ligne.type_semaine not in (Semaine.TYPE_COURS, Semaine.TYPE_FERIE):
        raise ValidationError("%s n'est pas un jour de cours." % libelle_du_jour(ligne))

    if est_ferie_isole(ligne):
        # Déjà férié : seul le nom peut changer, et il ne touche à aucune séance.
        if ligne.description != libelle:
            ligne.description = libelle
            ligne.save(update_fields=['description'])
        return {'changed': False, 'annulees': 0,
                'message': '%s est déjà férié.' % libelle_du_jour(ligne)}

    _refuser_si_suivi(ligne, 'Marquage du jour férié')

    ligne.type_semaine = Semaine.TYPE_FERIE
    ligne.description = libelle
    ligne.save(update_fields=['type_semaine', 'description'])

    annulees = (SeanceReelle.objects
                .filter(semaine=ligne, annulee=False)
                .update(annulee=True, motif_annulation=SeanceReelle.MOTIF_FERIE))
    deja = SeanceReelle.objects.filter(semaine=ligne, annulee=True,
                                       motif_annulation='').count()
    message = '%s marqué férié — %s annulée%s' % (
        libelle_du_jour(ligne), _pluriel(annulees, 'séance'), 's' if annulees > 1 else '')
    if deja:
        message += ' (%s déjà annulée%s à la main)' % (deja, 's' if deja > 1 else '')
    return {'changed': True, 'annulees': annulees, 'deja_annulees': deja,
            'message': message}


@transaction.atomic
def retirer_jour_ferie(ligne) -> dict:
    """Rend un jour férié isolé aux cours, et rétablit LES séances du férié.

    Une annulation faite à la main (motif vide) reste annulée.
    """
    from apps.edt.models import SeanceReelle
    from apps.parametres.models import Semaine

    if not est_ferie_isole(ligne):
        raise ValidationError("%s n'est pas un jour férié isolé." % libelle_du_jour(ligne))
    _refuser_si_suivi(ligne, 'Retrait du jour férié')

    ligne.type_semaine = Semaine.TYPE_COURS
    ligne.description = ''
    ligne.save(update_fields=['type_semaine', 'description'])

    retablies = _retablir(SeanceReelle.objects.filter(semaine=ligne))
    manuelles = SeanceReelle.objects.filter(semaine=ligne, annulee=True).count()
    message = '%s rendu aux cours — %s rétablie%s' % (
        libelle_du_jour(ligne), _pluriel(retablies, 'séance'), 's' if retablies > 1 else '')
    if manuelles:
        message += ' (%d annulée%s à la main reste%s annulée%s)' % (
            manuelles, 's' if manuelles > 1 else '', 'nt' if manuelles > 1 else '',
            's' if manuelles > 1 else '')
    return {'changed': True, 'retablies': retablies, 'annulees_manuelles': manuelles,
            'message': message}


def _retablir(seances) -> int:
    """Rétablit les séances annulées PAR UN FÉRIÉ — et elles seules."""
    from apps.edt.models import SeanceReelle
    return (seances.filter(annulee=True, motif_annulation=SeanceReelle.MOTIF_FERIE)
            .update(annulee=False, motif_annulation=''))


def retablir_seances_ferie(lignes_ids) -> int:
    """Pour une semaine ENTIÈRE remise en cours : ses jours n'ont plus de férié."""
    from apps.edt.models import SeanceReelle
    return _retablir(SeanceReelle.objects.filter(semaine_id__in=list(lignes_ids)))


# ── Les fériés fixes ─────────────────────────────────────────────────────────

def date_valide(jour, mois) -> bool:
    """Le 29 février est valide : on teste sur une année bissextile."""
    try:
        dt.date(2024, int(mois), int(jour))
        return True
    except (TypeError, ValueError):
        return False


def appliquer_feries_fixes(lignes) -> dict:
    """Marque les jours de `lignes` qui tombent sur un férié fixe ACTIF.

    Idempotent : un jour déjà férié est laissé tel quel. Un jour bloqué (suivi
    généré) est écarté avec son motif, sans empêcher les autres.
    """
    from apps.parametres.models import JourFerieFixe, Semaine

    fixes = {(f.jour, f.mois): f.libelle
             for f in JourFerieFixe.objects.filter(actif=True)}
    marques, ecartes, annulees = [], [], 0
    if not fixes:
        return {'marques': marques, 'ecartes': ecartes, 'annulees': annulees}

    for ligne in lignes:
        libelle = fixes.get((ligne.date.day, ligne.date.month))
        if libelle is None or ligne.type_semaine != Semaine.TYPE_COURS \
                or ligne.numero_semaine is None:
            continue
        try:
            r = marquer_jour_ferie(ligne, libelle)
        except (Conflit, ValidationError) as exc:
            detail = exc.detail
            if isinstance(detail, (list, tuple)):
                detail = ' '.join(str(d) for d in detail)
            ecartes.append({'id': ligne.pk, 'date': ligne.date.isoformat(),
                            'libelle': libelle, 'motif': str(detail)})
            continue
        annulees += r['annulees']
        marques.append({'id': ligne.pk, 'date': ligne.date.isoformat(),
                        'libelle': libelle, 'annulees': r['annulees']})
    return {'marques': marques, 'ecartes': ecartes, 'annulees': annulees}


def feries_de_la_periode(annee_universitaire, type_semestre=None):
    """Les jours fériés ISOLÉS d'une période, dans l'ordre du calendrier."""
    from apps.parametres.models import Semaine
    qs = (Semaine.objects
          .filter(annee_universitaire=annee_universitaire,
                  type_semaine=Semaine.TYPE_FERIE, numero_semaine__isnull=False)
          .select_related('jour_fk').order_by('date'))
    if type_semestre:
        qs = qs.filter(type_semestre=type_semestre)
    return [serialiser_ferie(l) for l in qs]


def serialiser_ferie(ligne) -> dict:
    return {'id': ligne.pk, 'date': ligne.date.isoformat(),
            'jour': ligne.jour_fk.jour if ligne.jour_fk_id else '',
            'libelle': ligne.description, 'numero_semaine': ligne.numero_semaine,
            'type_semestre': ligne.type_semestre,
            'annee_universitaire': ligne.annee_universitaire}
