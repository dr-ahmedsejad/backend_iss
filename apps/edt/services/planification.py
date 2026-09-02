"""
Duplication de la grille type, et projection d'une semaine vers le socle.

Trois opérations, dans l'ordre où on les emploie :

  1. `semaines_du_lot`  — quelles semaines sont visées ;
  2. `dupliquer_grille` — le patron devient des séances datées ;
  3. `projeter_semaine` — une semaine précise alimente `emplois.Emplois`,
     juste avant que le Suivi ne génère ses lignes.

La projection est le SEUL point de contact avec le socle, et il est à sens
unique. `suivi`, `vacation` et `avancement` ne sont pas modifiés : ils
continuent de lire `Emplois` comme ils l'ont toujours fait.
"""
import logging

from django.db import transaction

logger = logging.getLogger('siga')


# ── Sélection des semaines ───────────────────────────────────────────────────

def semaines_du_lot(annee_universitaire, type_semestre, numeros=None,
                    depuis=None, nombre=None):
    """
    Les lignes `Semaine` visées — rappel : une ligne est un JOUR.

    Trois façons de désigner un lot, par ordre de priorité : une liste de
    numéros, ou « N semaines à partir de la semaine X », ou tout le semestre.

    Seules les semaines de type `cours` sont retenues : dupliquer une grille sur
    une semaine de vacances ou d'examens y poserait des séances que personne
    n'assure, et que le Suivi facturerait.
    """
    from apps.parametres.models import Semaine

    qs = Semaine.objects.filter(
        annee_universitaire=annee_universitaire,
        type_semestre=type_semestre,
        type_semaine=Semaine.TYPE_COURS,
    )
    if numeros:
        qs = qs.filter(numero_semaine__in=list(numeros))
    elif depuis is not None:
        # `set()` en Python plutôt que `.distinct()` : le modèle `Semaine` porte
        # un `ordering` par date, que Django ajoute au SELECT d'un
        # `values_list().distinct()`. Le DISTINCT portait donc sur (numéro,
        # date) et ne dédoublonnait rien — une ligne par JOUR. « 16 semaines à
        # partir de la 1ʳᵉ » prenait alors les 16 premières LIGNES, soit les
        # semaines 1 à 3 seulement, et la duplication s'arrêtait là.
        suivantes = sorted({
            n for n in qs.filter(numero_semaine__gte=depuis)
                         .values_list('numero_semaine', flat=True)
            if n is not None
        })
        if nombre:
            suivantes = suivantes[:max(1, int(nombre))]
        qs = qs.filter(numero_semaine__in=suivantes)
    return qs.select_related('jour_fk').order_by('date')


# ── Duplication ──────────────────────────────────────────────────────────────

@transaction.atomic
def dupliquer_grille(grille, semaines, *, ecraser=False) -> dict:
    """
    Pose les séances du patron sur les semaines demandées.

    Par défaut une case déjà occupée est **laissée telle quelle** : la
    duplication n'écrase jamais une édition manuelle ni une permutation déjà
    appliquée. `ecraser=True` ne rétablit le patron que sur les séances dont
    l'origine est justement la grille — le travail manuel survit dans tous les
    cas.
    """
    from apps.edt.models import SeanceReelle

    modeles = list(grille.seances.select_related(
        'jour_fk', 'creneau_fk', 'em', 'prof', 'salle', 'type_seance_fk'))
    if not modeles:
        return {'creees': 0, 'ignorees': 0, 'remplacees': 0}

    # Les modèles sont indexés par jour : chaque ligne `Semaine` porte SON jour,
    # il suffit de rapprocher les deux.
    par_jour = {}
    for m in modeles:
        par_jour.setdefault(m.jour_fk_id, []).append(m)

    creees = ignorees = remplacees = 0
    for ligne_semaine in semaines:
        for modele in par_jour.get(ligne_semaine.jour_fk_id, []):
            filtre = {
                'departement': grille.departement,
                'semaine': ligne_semaine,
                'creneau_fk': modele.creneau_fk,
            }
            existante = SeanceReelle.objects.filter(**filtre).first()
            if existante is not None:
                if not ecraser or existante.origine != SeanceReelle.ORIGINE_GRILLE:
                    ignorees += 1
                    continue
                existante.delete()
                remplacees += 1
            SeanceReelle.objects.create(
                **filtre,
                em=modele.em, prof=modele.prof, salle=modele.salle,
                type_seance_fk=modele.type_seance_fk,
                origine=SeanceReelle.ORIGINE_GRILLE, seance_type=modele,
            )
            creees += 1

    return {'creees': creees, 'ignorees': ignorees, 'remplacees': remplacees}


# ── Projection vers le socle ─────────────────────────────────────────────────

@transaction.atomic
def projeter_semaine(annee_universitaire, type_semestre, numero_semaine,
                     departements=None) -> dict:
    """
    Réécrit `emplois.Emplois` avec les séances RÉELLES d'une semaine.

    C'est le raccord entre la planification et le Suivi. Ce dernier génère ses
    lignes depuis `Emplois` ; tant qu'`Emplois` restait une copie figée du
    patron, tout ce qui distingue une semaine — remplacement, séance annulée,
    séance ajoutée — lui échappait. Le remplaçant n'était pas payé, le remplacé
    l'était.

    Deux règles portent le correctif :

      * l'enseignant projeté est celui de la séance réelle, donc l'enseignant
        EFFECTIF après permutation ;
      * les séances annulées ne sont pas projetées — ni cours à pointer, ni
        heure à payer.

    Une séance partagée par plusieurs groupes produit **une ligne `Emplois` par
    groupe**, aux valeurs identiques : c'est exactement ce que le socle attend,
    puisqu'il les refusionne ensuite en un seul pointage (la clé de
    regroupement de `apps/suivi/views.py` exclut le département).
    """
    from apps.edt.models import SeanceReelle
    from apps.emplois.models import Emplois

    seances = (SeanceReelle.objects
               .filter(annulee=False,
                       semaine__annee_universitaire=annee_universitaire,
                       semaine__type_semestre=type_semestre,
                       semaine__numero_semaine=numero_semaine)
               .select_related('departement', 'departement__institution',
                               'semaine', 'semaine__jour_fk', 'creneau_fk',
                               'em', 'em__semestre',
                               'em__module_lmd', 'em__module_lmd__semestre',
                               'prof', 'salle', 'type_seance_fk'))
    if departements:
        seances = seances.filter(departement_id__in=list(departements))
    seances = list(seances)

    # Le remplacement est borné aux groupes concernés : un responsable qui
    # projette SA semaine ne doit pas effacer la grille d'un collègue.
    vises = ({s.departement_id for s in seances}
             if departements is None else set(departements))

    # COEXISTENCE (§7 bis du brief). L'ancien emploi du temps reste en service :
    # tant qu'un groupe est saisi sur `/dashboard/emplois/gerer`, ses lignes
    # `Emplois` sont écrites à la main et n'ont pas de séance derrière elles.
    #
    # Purger tout le périmètre les détruirait : il suffirait au directeur des
    # études de projeter UN groupe passé au nouveau moteur pour effacer la
    # saisie manuelle de tous les autres, qui sont dans le même périmètre. La
    # semaine paraîtrait vide, et la génération du suivi ne produirait rien
    # pour eux — sans un mot.
    #
    # On ne purge donc que les groupes AYANT ADOPTÉ le nouveau moteur : ceux
    # qui portent au moins une séance sur la période, quelle que soit la
    # semaine. Un groupe encore à l'ancienne n'est jamais touché ; un groupe
    # passé au nouveau moteur garde la protection d'origine — ses lignes d'une
    # semaine précédente sont bien retirées avant qu'on projette celle-ci.
    adoptants = set(
        SeanceReelle.objects
        .filter(semaine__annee_universitaire=annee_universitaire,
                semaine__type_semestre=type_semestre)
        .values_list('departement_id', flat=True)
    )
    vises &= adoptants

    supprimees = 0
    if vises:
        supprimees, _ = Emplois.objects.filter(
            annee_universitaire=annee_universitaire,
            type_semestre=type_semestre,
            departement_id__in=vises,
        ).delete()

    lignes = []
    for s in seances:
        # `Emplois.semestre` attend le semestre du référentiel. On le lit sur
        # l'ÉLÉMENT, et non sur son UE LMD comme à l'ESP : 56 des 207 éléments
        # de l'ISS n'ont pas de `module_lmd`, et seraient projetés sans
        # semestre — invisibles ensuite sur tout écran filtré par semestre.
        # L'UE reste consultée en second.
        # Une séance spéciale n'a pas d'élément, donc pas de semestre : c'est
        # admis, le champ est nullable.
        semestre = None
        if s.em_id:
            semestre = s.em.semestre
            if semestre is None and s.em.module_lmd_id:
                semestre = s.em.module_lmd.semestre
        lignes.append(Emplois(
            annee_universitaire=annee_universitaire,
            type_semestre=type_semestre,
            departement=s.departement,
            jour_fk_id=s.semaine.jour_fk_id,
            creneau_fk=s.creneau_fk,
            em=s.em,
            prof=s.prof,             # l'enseignant EFFECTIF
            salle=s.salle,
            semestre=semestre,
            type_seance_fk=s.type_seance_fk,
            institution=s.departement.institution,
        ))
    if lignes:
        Emplois.objects.bulk_create(lignes)

    logger.info('projeter_semaine %s %s S%s : %s seances -> %s lignes Emplois '
                '(%s supprimees)', annee_universitaire, type_semestre,
                numero_semaine, len(seances), len(lignes), supprimees)
    return {'seances': len(seances), 'projetees': len(lignes),
            'supprimees': supprimees, 'departements': sorted(vises)}
