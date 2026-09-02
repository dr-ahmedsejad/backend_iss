"""
L'emploi du temps et le suivi disent-ils la même chose ?

Le suivi est tiré de l'emploi du temps à un instant donné. L'emploi du temps,
lui, continue de vivre : on corrige une salle, on change un enseignant, on
annule une séance. Rien n'empêche cela — c'est même normal — mais à partir du
moment où le suivi a été généré, les deux peuvent décrire des semaines
différentes. Les heures pointées, la charge et les vacations ne correspondent
alors plus à ce que l'on imprime, et rien ne dit lequel des deux fait foi.

Ce module donne à chaque semaine l'un de trois états :

    prévisionnel   aucun suivi : l'emploi du temps n'engage que l'avenir
    aligné         suivi généré, et rien n'a bougé depuis
    divergent      l'emploi du temps a changé APRÈS la génération

Deux partis pris, repris de l'IPGEI qui les a éprouvés :

**On compare le contenu, jamais des horodatages.** Confronter une date de
modification à une date de génération ne dit que ceci : quelque chose a été
touché. Un enregistrement sans changement, ou une valeur modifiée puis remise,
suffisait à annoncer une divergence qui n'existait pas. Le faux positif coûte
cher ici : il pousse à régénérer un suivi qui allait bien.

**La salle ne fait pas partie de l'empreinte.** Déplacer un cours de la 101 à
la 102 ne change ni les heures dues, ni le programme avancé, ni ce qui sera
payé. Ce n'est pas une divergence, et le signaler comme telle apprendrait à
ignorer le signal.
"""
from django.db.models import Q

from apps.emplois.models import Emplois  # noqa: F401  (voisinage documentaire)

from ..models import SeanceReelle

ETAT_PREVISIONNEL = 'previsionnel'
ETAT_ALIGNE       = 'aligne'
ETAT_DIVERGENT    = 'divergent'

LIBELLES = {
    ETAT_PREVISIONNEL: 'Prévisionnel — suivi non généré',
    ETAT_ALIGNE:       'Suivi généré et à jour',
    ETAT_DIVERGENT:    'Emploi du temps modifié après la génération du suivi',
}

# Ce qui fait qu'une séance est « la même » des deux côtés. La salle en est
# absente, à dessein (voir l'en-tête). Le groupe en fait partie : déplacer un
# cours d'un groupe à l'autre change bien ce qui est dû à chacun.
#
# Une séance sans élément — sport, instruction militaire — est écartée des deux
# côtés : le suivi ne la porte pas, l'y chercher créerait une divergence
# permanente et fausse.


def _borner(qs, departements):
    """Restreint au périmètre de l'appelant — ses groupes délégués.

    `departements is None` signifie « aucune borne » (superutilisateur). Une
    liste VIDE, en revanche, veut dire « rien » : un compte sans périmètre ne
    doit pas hériter de la vue d'ensemble.

    L'ESP bornait sur deux axes, le second étant le pôle. Il ne se porte pas :
    l'ISS n'a pas de pôles, et son périmètre n'a qu'un axe.
    """
    if departements is None:
        return qs
    depts = list(departements)
    if not depts:
        return qs.none()
    return qs.filter(departement_id__in=depts)


def _empreinte_edt(annee, type_semestre, numeros, departements=None) -> dict:
    """Empreinte de l'emploi du temps, par numéro de semaine.

    Une seule passe pour toutes les semaines demandées : sur un semestre de
    seize semaines, le calcul un-par-un coûtait trente-deux allers-retours.
    """
    qs = (SeanceReelle.objects
          .filter(semaine__annee_universitaire=annee,
                  semaine__type_semestre=type_semestre,
                  semaine__numero_semaine__in=list(numeros),
                  annulee=False, em__isnull=False))
    qs = _borner(qs, departements)

    par_semaine = {n: set() for n in numeros}
    for (numero, dept, jour, creneau, em, prof, type_seance) in qs.values_list(
            'semaine__numero_semaine', 'departement_id', 'semaine__jour_fk_id',
            'creneau_fk_id', 'em_id', 'prof_id', 'type_seance_fk__type_seance'):
        par_semaine.setdefault(numero, set()).add(
            (dept, jour, creneau, em, prof, type_seance))
    return par_semaine


def _empreinte_suivi(annee, type_semestre, numeros, departements=None) -> dict:
    """La même empreinte, lue depuis les lignes de suivi déjà générées."""
    from apps.suivi.models import Suivie

    qs = (Suivie.objects
          .filter(annee_universitaire=annee, type_semestre=type_semestre,
                  numero_semaine__in=list(numeros), em__isnull=False))
    qs = _borner(qs, departements)

    par_semaine = {n: set() for n in numeros}
    for (numero, dept, jour, creneau, em, prof, type_seance) in qs.values_list(
            'numero_semaine', 'departement_id', 'jour_fk_id', 'creneau_fk_id',
            'em_id', 'prof_id', 'type_seance_fk__type_seance'):
        par_semaine.setdefault(numero, set()).add(
            (dept, jour, creneau, em, prof, type_seance))
    return par_semaine


def etats_en_lot(annee, type_semestre, numeros, departements=None) -> dict:
    """État de chaque semaine, en DEUX requêtes quel qu'en soit le nombre.

    Le périmètre borne la comparaison aux groupes de l'appelant. Sans cette
    borne, une semaine parfaitement à jour chez lui apparaîtrait divergente
    parce qu'un collègue a modifié la sienne — un signal qu'il ne peut ni
    comprendre ni corriger.
    """
    numeros = [int(n) for n in numeros]
    if not numeros:
        return {}

    edt   = _empreinte_edt(annee, type_semestre, numeros, departements)
    suivi = _empreinte_suivi(annee, type_semestre, numeros, departements)

    etats = {}
    for n in numeros:
        empreinte_suivi = suivi.get(n) or set()
        if not empreinte_suivi:
            etats[n] = ETAT_PREVISIONNEL
        elif (edt.get(n) or set()) != empreinte_suivi:
            etats[n] = ETAT_DIVERGENT
        else:
            etats[n] = ETAT_ALIGNE
    return etats


def etat_semaine(annee, type_semestre, numero, departements=None) -> str:
    """État d'une seule semaine."""
    return etats_en_lot(annee, type_semestre, [numero],
                        departements)[int(numero)]


def detail_divergence(annee, type_semestre, numero, departements=None) -> dict:
    """Ce qui a changé, pour le dire plutôt que de le laisser deviner.

    « Divergent » sans plus est un reproche sans mode d'emploi. On rend donc
    les deux écarts : ce que l'emploi du temps porte et que le suivi ignore,
    et l'inverse.
    """
    numero = int(numero)
    edt   = _empreinte_edt(annee, type_semestre, [numero],
                           departements)[numero]
    suivi = _empreinte_suivi(annee, type_semestre, [numero],
                             departements)[numero]
    return {
        'ajoutees':   len(edt - suivi),      # dans l'EDT, absentes du suivi
        'disparues':  len(suivi - edt),      # dans le suivi, absentes de l'EDT
    }
