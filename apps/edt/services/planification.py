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
from rest_framework.exceptions import ValidationError

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
#
# Deux sources, une seule mécanique. Le patron pose des cases abstraites ; une
# semaine réelle pose ses propres séances. Dans les deux cas on rapproche une
# source d'une ligne `Semaine` cible par son JOUR — `jour_fk_id`, jamais le nom
# du jour : comparer des noms casse à la première différence de casse, et ça
# casse en silence.


def _libelle_case(ligne_semaine, creneau) -> str:
    """Nomme une case entièrement : « S3 · Mardi 08h00-09h30 ».

    Le numéro de semaine EN FAIT PARTIE. Sans lui, seize semaines bloquées sur
    le même créneau donnaient seize libellés identiques : on lisait la même
    ligne seize fois sans savoir qu'il s'agissait de seize cases, et le
    navigateur refusait la clé en double.

    À l'ISS une case est unique par (groupe, semaine, créneau) — contrainte
    `uniq_edt_seance_reelle_case`. Il n'y a donc pas de sous-groupe à nommer en
    plus : deux séances ne peuvent pas partager un créneau.
    """
    jour = getattr(ligne_semaine.jour_fk, 'jour', '') if ligne_semaine.jour_fk_id else ''
    return 'S%s · %s %s' % (ligne_semaine.numero_semaine, jour, creneau.creneau)


def _motif_du_refus(existante, ecraser: bool) -> str:
    """Pourquoi cette case n'a pas été reprise — en clair, pas en jargon.

    « Un enseignant ou une salle déjà pris » laisse perplexe devant une case
    bloquée par une saisie manuelle. Chaque motif dit SA raison.
    """
    from apps.edt.models import SeanceReelle

    if not ecraser:
        return ("Case déjà occupée. Cochez « Rétablir » pour remplacer ce "
                "qu'une duplication y avait posé.")
    if existante.origine == SeanceReelle.ORIGINE_MANUELLE:
        return "Séance ajoutée à la main : une duplication ne l'écrase jamais."
    if existante.origine == SeanceReelle.ORIGINE_PERMUTATION:
        return "Remplacement d'enseignant saisi sur cette semaine : conservé."
    return 'Case déjà occupée.'


def _bilan(creees, remplacees, refus) -> dict:
    """Le compte rendu, dans l'ordre où il se lit.

    Ce qui a RÉUSSI d'abord : la duplication est déjà enregistrée quand ce bilan
    s'affiche, et des refus présentés en tête donnent à croire que tout a
    échoué.

    Les refus sont GROUPÉS par motif : seize semaines bloquées par la même
    raison donnaient seize lignes identiques. Le nombre dit l'ampleur, les
    exemples servent à lire — cinq suffisent, et l'ensemble reste sous vingt
    lignes.
    """
    par_motif = {}
    for motif, libelle in refus:
        par_motif.setdefault(motif, []).append(libelle)
    conflits = [
        {'motif': motif, 'nombre': len(cases), 'exemples': cases[:5]}
        for motif, cases in sorted(par_motif.items(), key=lambda kv: -len(kv[1]))
    ]
    return {
        'creees': creees,
        'remplacees': remplacees,
        'ignorees': len(refus),
        'conflits': conflits,
    }


def _poser(sources, semaines, departement, *, origine, ecraser):
    """Pose `sources` sur `semaines`, et rend (créées, remplacées, refus).

    `sources` : itérable de (jour_fk_id, champs, seance_type) — `champs` est ce
    qui sera copié, `seance_type` le lien vers la case de patron (nul pour une
    recopie de semaine).

    Une collision n'interrompt RIEN : sur seize semaines, tout annuler pour une
    case serait pire que la case elle-même. On écarte la case et on rend le
    motif à l'appelant.
    """
    from apps.edt.models import SeanceReelle

    par_jour = {}
    for jour_id, champs, modele in sources:
        par_jour.setdefault(jour_id, []).append((champs, modele))

    creees = remplacees = 0
    refus = []
    for ligne in semaines:
        for champs, modele in par_jour.get(ligne.jour_fk_id, []):
            filtre = {
                'departement': departement,
                'semaine': ligne,
                'creneau_fk': champs['creneau_fk'],
            }
            existante = SeanceReelle.objects.filter(**filtre).first()
            if existante is not None:
                reprenable = (ecraser
                              and existante.origine in SeanceReelle.ORIGINES_DUPLIQUEES)
                if not reprenable:
                    refus.append((_motif_du_refus(existante, ecraser),
                                  _libelle_case(ligne, champs['creneau_fk'])))
                    continue
                existante.delete()
                remplacees += 1
            # `filtre` et `champs` portent tous deux `creneau_fk` : on ne
            # déplie que `champs`, qui est la source de vérité de la case.
            SeanceReelle.objects.create(
                departement=departement, semaine=ligne, **champs,
                origine=origine, seance_type=modele)
            creees += 1
    return creees, remplacees, refus


@transaction.atomic
def dupliquer_grille(grille, semaines, *, ecraser=False) -> dict:
    """
    Pose les séances du PATRON sur les semaines demandées.

    Par défaut une case déjà occupée est laissée telle quelle. `ecraser=True`
    ne reprend que ce qu'une duplication avait posé — patron ou recopie de
    semaine ; une saisie manuelle et une permutation survivent dans tous les
    cas.

    Un patron vide LÈVE UNE ERREUR au lieu de rendre zéro : « 0 séance créée sur
    0 semaine » est un message fugace qui ressemble à une panne, et c'est
    justement le cas le plus fréquent — six grilles sur onze sont vides ici.
    """
    from apps.edt.models import SeanceReelle

    modeles = list(grille.seances.select_related(
        'jour_fk', 'creneau_fk', 'em', 'prof', 'salle', 'type_seance_fk'))
    if not modeles:
        raise ValidationError(
            "Le patron de ce groupe est vide : il n'y a rien à dupliquer. "
            "Remplissez la grille type, ou dupliquez plutôt une semaine déjà "
            "construite.")

    sources = [
        (m.jour_fk_id,
         {'creneau_fk': m.creneau_fk, 'em': m.em, 'prof': m.prof,
          'salle': m.salle, 'type_seance_fk': m.type_seance_fk},
         m)
        for m in modeles
    ]
    creees, remplacees, refus = _poser(
        sources, semaines, grille.departement,
        origine=SeanceReelle.ORIGINE_GRILLE, ecraser=ecraser)
    return _bilan(creees, remplacees, refus)


@transaction.atomic
def dupliquer_semaine(departement, semaine_source, semaines, *, ecraser=False) -> dict:
    """
    Recopie une SEMAINE RÉELLE sur d'autres semaines du même semestre.

    C'est le geste qu'on fait vraiment : on bâtit la semaine 1 sur l'écran
    hebdomadaire, et l'on veut les suivantes identiques. Le patron, lui, reste
    souvent vide — six cases pour trente et une séances réelles ici.

    Ce qui est copié : l'élément, l'enseignant, la salle, le type. Ce qui ne
    l'est PAS :

      * `annulee` — une annulation dit « ce cours-là n'a pas eu lieu CETTE
        semaine ». La recopier annulerait quinze cours qui doivent avoir lieu ;
      * `prof_initial` et `observations` — la mémoire d'un remplacement ponctuel
        et son motif ne valent que pour leur semaine ;
      * `cle_partage` — un cours partagé se ré-étend groupe par groupe, avec
        « Étendre à d'autres groupes ». Copier la clé rattacherait la copie au
        cours d'une autre semaine.

    L'enseignant copié est l'enseignant EFFECTIF : si la semaine source portait
    un remplacement, c'est le remplaçant qui est recopié. C'est ce qu'on voit à
    l'écran, donc ce qu'on croit recopier.

    La copie porte l'origine `recopie`, jamais celle de sa source — voir le
    modèle. L'original, lui, garde la sienne.
    """
    from apps.edt.models import SeanceReelle

    originales = list(
        SeanceReelle.objects
        .filter(departement=departement,
                semaine__annee_universitaire=semaine_source.annee_universitaire,
                semaine__type_semestre=semaine_source.type_semestre,
                semaine__numero_semaine=semaine_source.numero_semaine)
        .select_related('semaine', 'semaine__jour_fk', 'creneau_fk',
                        'em', 'prof', 'salle', 'type_seance_fk'))
    if not originales:
        raise ValidationError(
            "La semaine %s de ce groupe ne contient aucune séance : il n'y a "
            "rien à dupliquer." % semaine_source.numero_semaine)

    # La source n'est JAMAIS sa propre cible : avec « Rétablir », elle
    # s'effacerait puis se recréerait — au mieux inutile, au pire destructeur si
    # la suppression passait et la création échouait.
    cibles = [s for s in semaines
              if s.numero_semaine != semaine_source.numero_semaine]
    if not cibles:
        raise ValidationError(
            "Aucune semaine cible : la seule semaine retenue est la semaine "
            "source elle-même.")

    sources = [
        (o.semaine.jour_fk_id,
         {'creneau_fk': o.creneau_fk, 'em': o.em, 'prof': o.prof,
          'salle': o.salle, 'type_seance_fk': o.type_seance_fk},
         None)
        for o in originales
    ]
    creees, remplacees, refus = _poser(
        sources, cibles, departement,
        origine=SeanceReelle.ORIGINE_RECOPIE, ecraser=ecraser)
    return _bilan(creees, remplacees, refus)


@transaction.atomic
def reprendre_semaine(grille, semaine_source, *, ecraser=False) -> dict:
    """
    Promeut une SEMAINE RÉELLE en patron — le sens inverse de la duplication.

    Personne ne compose un patron à vide : on bâtit une semaine sur l'écran
    hebdomadaire, où l'on voit ce qu'on fait, et le patron reste vide. Six
    grilles sur onze le sont ici, pendant que les semaines portent trente et une
    séances.

    La raison d'être du patron est pourtant réelle, et c'est la seule : il
    n'appartient à AUCUN semestre. Il tient au groupe et au TYPE de semestre.
    Rempli une fois, il resservira l'année suivante — là où une semaine réelle
    meurt avec son année.

    DEUX ÉCARTS entre la semaine et le patron obtenu, et chacun se justifie :

    1. Une séance ANNULÉE n'entre pas. On reprend un emploi du temps, pas
       l'histoire de ses accidents : un cours annulé le mercredi 8 octobre ne dit
       rien du mercredi ordinaire.

    2. Une PERMUTATION revient à son TITULAIRE. Les deux réflexes sont mauvais.
       La recopier telle quelle graverait l'exception dans le modèle — le
       remplaçant deviendrait titulaire pour toutes les années à venir. Mais
       l'écarter est pire : le patron y garderait un trou, et la case manquerait
       à chaque duplication future. On la reprend donc, en rendant la case à
       `prof_initial`, que le modèle conserve précisément pour cela.

       Sans `prof_initial` — une permutation posée avant que ce champ ne soit
       rempli — on reprend l'enseignant EFFECTIF : on ne peut reprendre que ce
       qu'on voit, et prétendre avoir normalisé serait mentir.

    Le compte rendu DIT ces deux écarts. Sans cela le patron ne reproduit pas la
    semaine qu'on avait sous les yeux, et la différence se découvre bien plus
    tard, sans explication.

    `ecraser` ne commande pas la même chose que dans la duplication, et c'est
    normal : ici la cible est le patron. Sans lui, une case déjà composée à la
    main dans le patron reste telle quelle — on complète un modèle sans défaire
    ce qu'on y a réglé. Avec, le patron suit la semaine.
    """
    from apps.edt.models import SeanceReelle, SeanceType

    # Le patron tient au TYPE de semestre : une semaine paire n'entre pas dans
    # un patron impair. Deux semestres de même parité partagent leurs semaines à
    # l'ISS — ce contrôle n'est donc pas suffisant à lui seul, mais il attrape le
    # cas courant, et c'est le seul axe que la donnée permette de vérifier.
    if semaine_source.type_semestre != grille.type_semestre:
        raise ValidationError(
            "La semaine %s est d'un semestre %s ; ce patron est %s. Un patron "
            "tient au type de semestre : il ne peut pas reprendre une semaine "
            "de l'autre parité." % (
                semaine_source.numero_semaine,
                'pair' if semaine_source.type_semestre == 'P' else 'impair',
                'pair' if grille.type_semestre == 'P' else 'impair'))

    toutes = list(
        SeanceReelle.objects
        .filter(departement=grille.departement,
                semaine__annee_universitaire=semaine_source.annee_universitaire,
                semaine__type_semestre=semaine_source.type_semestre,
                semaine__numero_semaine=semaine_source.numero_semaine)
        .select_related('semaine', 'semaine__jour_fk', 'creneau_fk',
                        'em', 'prof', 'prof_initial', 'salle', 'type_seance_fk'))

    retenues = [s for s in toutes if not s.annulee]
    annulees = len(toutes) - len(retenues)

    if not retenues:
        if annulees:
            raise ValidationError(
                "La semaine %s de %s ne contient que des séances annulées : il "
                "n'y a rien à reprendre dans un patron." % (
                    semaine_source.numero_semaine, grille.departement.nom))
        raise ValidationError(
            "La semaine %s de %s ne contient aucune séance : il n'y a rien à "
            "reprendre." % (semaine_source.numero_semaine, grille.departement.nom))

    creees = remplacees = 0
    ramenees = 0
    refus = []
    for s in retenues:
        jour_id = s.semaine.jour_fk_id
        if jour_id is None:
            continue

        # La permutation rend la case à son titulaire, quand il est connu.
        prof = s.prof
        if s.origine == SeanceReelle.ORIGINE_PERMUTATION and s.prof_initial_id:
            prof = s.prof_initial
            ramenees += 1

        champs = {'em': s.em, 'prof': prof, 'salle': s.salle,
                  'type_seance_fk': s.type_seance_fk}
        # La case du patron est identifiée par (grille, jour, créneau) — il n'y
        # a pas de sous-groupe à l'ISS, la contrainte `uniq_edt_seance_type_case`
        # l'atteste. Deux séances ne peuvent donc pas se disputer une case.
        cle = {'grille': grille, 'jour_fk_id': jour_id, 'creneau_fk': s.creneau_fk}

        existante = SeanceType.objects.filter(**cle).first()
        if existante is not None:
            if not ecraser:
                refus.append((
                    "Case déjà composée dans le patron. Cochez « Le patron suit "
                    "la semaine » pour la remplacer.",
                    '%s %s' % (getattr(s.semaine.jour_fk, 'jour', ''),
                               s.creneau_fk.creneau)))
                continue
            for champ, valeur in champs.items():
                setattr(existante, champ, valeur)
            existante.save(update_fields=list(champs))
            remplacees += 1
            continue
        SeanceType.objects.create(**cle, **champs)
        creees += 1

    bilan = _bilan(creees, remplacees, refus)
    bilan['permutations_ramenees'] = ramenees
    bilan['annulees_ecartees'] = annulees
    bilan['semaine_source'] = semaine_source.numero_semaine
    return bilan


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
