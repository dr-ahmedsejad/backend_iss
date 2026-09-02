"""
Archivage de l'emploi du temps au moment où il est transmis au suivi.

`SeanceReelle` est modifiée sur place : rien n'y garde la version d'avant. Or
c'est sur une version précise que les heures ont été pointées, et parfois
payées. Ce module fige cette version-là, groupe par groupe, à l'instant où la
projection remet l'emploi du temps au socle.

L'archive est autoportante : aucun lien vers le référentiel, uniquement des
libellés figés. Renommer une salle ou supprimer un enseignant ne doit pas
récrire le passé.
"""
import logging

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..groupes import sous_groupe
from ..models import EmploiArchive, SeanceReelle

logger = logging.getLogger(__name__)


def _instantane(seance, version, genere_le):
    """Une séance, recopiée en valeurs — plus aucune clé étrangère à suivre."""
    semaine = seance.semaine
    return EmploiArchive(
        annee_universitaire=semaine.annee_universitaire,
        type_semestre=semaine.type_semestre,
        numero_semaine=semaine.numero_semaine,
        departement_id=seance.departement_id,
        version=version,
        genere_le=genere_le,

        jour_ref=semaine.jour_fk_id,
        creneau_ref=seance.creneau_fk_id,
        creneau_ordre=seance.creneau_fk.ordre if seance.creneau_fk_id else 0,
        em_ref=seance.em_id,
        type_seance_ref=seance.type_seance_fk_id,
        prof_ref=seance.prof_id,
        salle_ref=seance.salle_id,

        jour_libelle=semaine.jour_fk.jour if semaine.jour_fk_id else '',
        creneau_libelle=seance.creneau_fk.creneau if seance.creneau_fk_id else '',
        departement_nom=seance.departement.nom if seance.departement_id else '',
        # `Departement.groupe` est vide à l'ISS : le sous-groupe se lit sur le
        # nom (voir `apps/edt/groupes.py`). L'archive fige ce qui a été lu, et
        # non un champ qui, ici, ne dit jamais rien.
        departement_groupe=(sous_groupe(seance.departement)
                            if seance.departement_id else ''),
        em_code=seance.em.code_em if seance.em_id else '',
        em_intitule=seance.em.intitule if seance.em_id else '',
        type_seance_libelle=(seance.type_seance_fk.type_seance
                             if seance.type_seance_fk_id else ''),
        type_seance_special=bool(seance.type_seance_fk_id
                                 and seance.type_seance_fk.is_special),
        prof_nom=seance.prof.nom if seance.prof_id else '',
        prof_initial_nom=seance.prof_initial.nom if seance.prof_initial_id else '',
        salle_nom=seance.salle.nom if seance.salle_id else '',

        date_seance=semaine.date,
        origine=seance.origine,
        annulee=seance.annulee,
    )


@transaction.atomic
def archiver_semaine(annee: str, type_semestre: str, numero_semaine: int,
                     departements=None) -> int:
    """
    Fige l'emploi du temps de la semaine, une version par groupe.

    Appelé après la projection vers `emplois.Emplois` — c'est-à-dire au moment
    où cette version-là devient celle sur laquelle le suivi sera généré, puis
    la charge et les vacations calculées.

    Une re-transmission produit une NOUVELLE version : l'ancienne n'est jamais
    écrasée. Un groupe sans séance cette semaine-là n'est pas archivé — une
    grille vide n'apprend rien et polluerait le sélecteur de versions.

    Retourne le nombre de séances figées.
    """
    seances = list(
        SeanceReelle.objects
        .filter(semaine__annee_universitaire=annee,
                semaine__type_semestre=type_semestre,
                semaine__numero_semaine=numero_semaine)
        .select_related('departement', 'semaine', 'semaine__jour_fk',
                        'creneau_fk', 'em', 'prof', 'prof_initial',
                        'salle', 'type_seance_fk')
    )
    if departements:
        garder = {int(d) for d in departements}
        seances = [s for s in seances if s.departement_id in garder]
    if not seances:
        return 0

    # Une version par groupe : deux responsables transmettent leur semaine à
    # des moments différents, et chacun doit retrouver la sienne.
    deja = {
        ligne['departement_id']: ligne['v']
        for ligne in (EmploiArchive.objects
                      .filter(annee_universitaire=annee,
                              type_semestre=type_semestre,
                              numero_semaine=numero_semaine)
                      .values('departement_id')
                      .annotate(v=Max('version')))
    }

    genere_le = timezone.now()
    lot = [_instantane(s, deja.get(s.departement_id, 0) + 1, genere_le)
           for s in seances]
    EmploiArchive.objects.bulk_create(lot)

    logger.info('Archivage EDT : %s séance(s) figée(s) pour la semaine %s (%s %s)',
                len(lot), numero_semaine, annee, type_semestre)
    return len(lot)
