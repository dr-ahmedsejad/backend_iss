"""
Séance partagée entre plusieurs groupes.

Un même cours peut réunir plusieurs groupes — quatre groupes et deux
enseignants, chacun en prenant deux ; ou plusieurs filières devant le même
cours transversal. Le partage se décide à la planification, selon les effectifs
enseignants.

**Une séance par groupe, reliées par une clé.** C'est la forme qu'attend le
socle : il reçoit une ligne `Emplois` par groupe, les refusionne au pointage —
sa clé de regroupement exclut le département — et compte les heures une seule
fois. Une relation plusieurs-à-plusieurs sur la séance ferait perdre la
contrainte d'unicité de la case sans rien gagner en aval.

Ce que la clé apporte, c'est que le lien **existe** : jusqu'ici il était deviné
après coup, en comparant `(prof, EM, type, salle)` d'une grille à l'autre. Une
salle changée pour un seul groupe suffisait à scinder le cours en deux, sans
que rien ne le signale.

Conséquence à respecter : la clé de fusion du socle inclut `salle` et `prof`.
Deux groupes réunis mais **saisis avec des salles différentes** produisent deux
lignes payées. La propagation ci-dessous garantit donc que les séances d'un même
cours restent identiques sur les quatre axes qui comptent.
"""
import uuid

from django.db import transaction

# Ce qui définit le cours, par opposition au groupe qui le reçoit. Ces quatre
# champs sont propagés à toutes les séances partagées : les laisser diverger
# ferait payer deux fois un enseignant qui n'a donné qu'un cours.
CHAMPS_PROPAGES = ('em_id', 'prof_id', 'salle_id', 'type_seance_fk_id',
                   'annulee', 'prof_initial_id', 'origine')


@transaction.atomic
def partager(seance, departements) -> dict:
    """
    Étend une séance à d'autres groupes.

    Chaque groupe reçoit SA séance, identique, sur la même case. Toutes portent
    la même clé. Un groupe déjà servi sur cette case est ignoré plutôt que
    remplacé — écraser la séance d'un collègue sans le dire serait pire que ne
    rien faire.
    """
    from apps.edt.models import SeanceReelle

    if seance.cle_partage is None:
        seance.cle_partage = uuid.uuid4()
        seance.save(update_fields=['cle_partage'])

    ajoutes, occupes = [], []
    for dept_id in departements:
        if int(dept_id) == seance.departement_id:
            continue
        occupant = SeanceReelle.objects.filter(
            departement_id=dept_id, semaine=seance.semaine,
            creneau_fk=seance.creneau_fk).first()
        if occupant is not None:
            if occupant.cle_partage == seance.cle_partage:
                continue                     # déjà dans le partage
            occupes.append(dept_id)
            continue
        SeanceReelle.objects.create(
            departement_id=dept_id, semaine=seance.semaine,
            creneau_fk=seance.creneau_fk, em=seance.em, prof=seance.prof,
            salle=seance.salle, type_seance_fk=seance.type_seance_fk,
            origine=seance.origine, seance_type=seance.seance_type,
            annulee=seance.annulee, prof_initial=seance.prof_initial,
            cle_partage=seance.cle_partage,
        )
        ajoutes.append(int(dept_id))

    return {'cle': str(seance.cle_partage), 'ajoutes': ajoutes,
            'cases_occupees': occupes}


@transaction.atomic
def retirer_du_partage(seance) -> dict:
    """
    Sort un groupe du cours partagé — sa séance disparaît.

    Si le partage tombe à une seule séance, la clé est retirée : un « partage »
    d'un seul groupe n'est plus un partage, et le laisser marqué induirait en
    erreur l'écran comme la personne qui le lit.
    """
    from apps.edt.models import SeanceReelle

    cle = seance.cle_partage
    seance.delete()
    if cle is None:
        return {'restantes': 0}

    restantes = list(SeanceReelle.objects.filter(cle_partage=cle))
    if len(restantes) == 1:
        restantes[0].cle_partage = None
        restantes[0].save(update_fields=['cle_partage'])
    return {'restantes': len(restantes)}


@transaction.atomic
def propager(seance) -> int:
    """
    Aligne les autres séances du cours sur celle-ci.

    Appelé après chaque modification d'une séance partagée. Sans cela, changer
    la salle d'un seul groupe suffirait à faire diverger le cours — et le socle,
    dont la clé de fusion inclut la salle et l'enseignant, produirait alors deux
    lignes payées pour un seul cours donné.
    """
    from apps.edt.models import SeanceReelle

    if not seance.cle_partage:
        return 0
    valeurs = {c: getattr(seance, c) for c in CHAMPS_PROPAGES}
    return (SeanceReelle.objects
            .filter(cle_partage=seance.cle_partage)
            .exclude(pk=seance.pk)
            .update(**valeurs))
