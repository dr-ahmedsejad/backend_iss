"""Les cases à cocher de l'écran « Annonces », et qui elles désignent.

Une case = une FILIÈRE à un NIVEAU de l'année en cours (SEA L1, SDID L2…) :
les groupes (`Departement`) de l'année qui portent cette filière et ce
niveau, tous leurs groupes compris. Les groupes d'accueil (`is_container`)
n'en font pas partie : on n'y planifie rien et un étudiant n'y reste pas.

Identifiant d'une case : « f<filière>-n<niveau> ». Il se décode côté serveur à
l'envoi — l'écran ne choisit jamais lui-même les destinataires.
"""
import re

from django.db.models import Count, Q

CLE = re.compile(r'^f(\d+)-n(\d+)$')


def annee_active():
    from apps.departement.models import Departement
    from apps.parametres.models import Year
    a = Year.objects.filter(est_active=True).values_list('annee', flat=True).first()
    if a:
        return a
    return (Departement.objects.exclude(annee_universitaire='')
            .order_by('-annee_universitaire').values_list('annee_universitaire', flat=True).first())


def _groupes(annee):
    from apps.departement.models import Departement
    return (Departement.objects
            .filter(annee_universitaire=annee, is_container=False,
                    filiere__isnull=False, niveau__isnull=False))


def _etudiants():
    from apps.absence.models import Etudiant
    return Etudiant.objects.filter(user__isnull=False, user__is_active=True)


def grille(annee=None):
    """Lignes (filières), colonnes (niveaux) et cases existantes, avec leur
    nombre d'étudiants ayant un compte."""
    annee = annee or annee_active()
    groupes = _groupes(annee).select_related('filiere', 'niveau')
    filieres, niveaux, cases = {}, {}, {}
    for g in groupes:
        filieres[g.filiere_id] = g.filiere
        niveaux[g.niveau_id] = g.niveau
        cases.setdefault((g.filiere_id, g.niveau_id), 0)
    comptes = (_etudiants()
               .filter(departement__in=groupes)
               .values('departement__filiere', 'departement__niveau')
               .annotate(n=Count('id')))
    for c in comptes:
        cle = (c['departement__filiere'], c['departement__niveau'])
        if cle in cases:
            cases[cle] = c['n']
    return {
        'annee': annee,
        'lignes': [{'id': f'f{f.pk}', 'label': f.code, 'sub': f.intitule_fr or ''}
                   for f in sorted(filieres.values(), key=lambda f: f.code or '')],
        'colonnes': [{'id': f'n{n.pk}', 'label': n.niveau, 'sub': ''}
                     for n in sorted(niveaux.values(), key=lambda n: n.niveau or '')],
        'cases': [{'cle': f'f{f}-n{n}', 'ligne': f'f{f}', 'colonne': f'n{n}', 'nb': nb}
                  for (f, n), nb in cases.items()],
    }


def destinataires(cles, annee=None):
    """Les comptes des étudiants désignés par ces cases (identifiants inconnus
    ignorés). Retourne (ids des utilisateurs, cases retenues)."""
    annee = annee or annee_active()
    q, retenues = Q(), []
    for cle in cles or []:
        m = CLE.match(str(cle))
        if not m:
            continue
        q |= Q(departement__filiere_id=int(m[1]), departement__niveau_id=int(m[2]))
        retenues.append(cle)
    if not retenues:
        return [], []
    ids = (_etudiants()
           .filter(q, departement__in=_groupes(annee))
           .values_list('user_id', flat=True).distinct())
    return list(ids), retenues
