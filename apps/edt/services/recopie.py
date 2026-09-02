"""
Recopie d'un patron vers d'autres groupes.

Répartir une promotion entre G1, G2, G3 et G4, c'est poser quatre fois le
même squelette : mêmes enseignements, mêmes créneaux, et seuls l'enseignant
et la salle changent d'un groupe à l'autre. Le retaper quatre fois est un
travail de copiste, avec les fautes que cela suppose.

Ce module recopie, il ne fusionne pas : chaque groupe reçoit ses propres
cases, qu'on ajuste ensuite. Une case déjà occupée chez la cible n'est
JAMAIS écrasée — on la signale et on passe. Écraser en silence ferait perdre
un travail dont on ne saurait même pas qu'il existait.
"""
from django.db import transaction

from ..models import GrilleType, SeanceType

# Ce qui définit une case, et donc ce qui se recopie. Le jour et le créneau en
# font partie : recopier ailleurs qu'à la même heure ne serait plus une copie.
CHAMPS_RECOPIES = (
    'jour_fk_id', 'creneau_fk_id', 'type_seance_fk_id',
    'em_id', 'prof_id', 'salle_id',
)


def grille_jumelle(source: GrilleType, departement_id: int):
    """La grille de ce groupe pour la même période, créée si elle manque.

    Obliger à créer la grille cible d'abord ne protégerait de rien : elle est
    vide, et on vient précisément la remplir.
    """
    grille, creee = GrilleType.objects.get_or_create(
        departement_id=departement_id,
        type_semestre=source.type_semestre,
        annee_universitaire=source.annee_universitaire,
        defaults={'libelle': source.libelle, 'actif': True},
    )
    return grille, creee


@transaction.atomic
def recopier(source: GrilleType, departements, seances=None) -> dict:
    """
    Recopie les cases de `source` vers les groupes indiqués.

    `seances` : les identifiants à recopier ; None recopie toute la grille.

    Retourne le détail : ce qui a été créé, les cases déjà prises chez la
    cible, et les grilles créées au passage.
    """
    cases = source.seances.all()
    if seances:
        cases = cases.filter(pk__in=list(seances))
    cases = list(cases)
    if not cases:
        return {'crees': 0, 'occupees': [], 'grilles_creees': 0, 'groupes': []}

    crees, occupees, grilles_creees, groupes = 0, [], 0, []

    for dept_id in departements:
        cible, creee = grille_jumelle(source, int(dept_id))
        grilles_creees += int(creee)
        groupes.append({'departement': cible.departement_id,
                        'nom': cible.departement.nom,
                        'grille': cible.pk})

        # Ce qui occupe déjà la cible, par case. Une seule requête plutôt
        # qu'une par séance.
        prises = {(s.jour_fk_id, s.creneau_fk_id)
                  for s in cible.seances.all()}

        for case in cases:
            reperes = (case.jour_fk_id, case.creneau_fk_id)
            if reperes in prises:
                occupees.append({
                    'departement': cible.departement_id,
                    'nom':         cible.departement.nom,
                    'jour':        case.jour_fk.jour,
                    'creneau':     case.creneau_fk.creneau,
                })
                continue
            SeanceType.objects.create(
                grille=cible,
                **{c: getattr(case, c) for c in CHAMPS_RECOPIES},
            )
            prises.add(reperes)
            crees += 1

    return {'crees': crees, 'occupees': occupees,
            'grilles_creees': grilles_creees, 'groupes': groupes}
