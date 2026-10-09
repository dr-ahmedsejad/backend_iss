"""
Groupes d'anglais : les créer, y affecter les étudiants, lire une affectation
venue d'Excel. Modèles : `GroupeAnglais`, `AffectationAnglais` (models.py).

L'étudiant garde son groupe habituel pour tout le reste. Pour l'anglais, il
est affecté à l'un des DEUX groupes d'anglais de son niveau, pour l'année.
Le besoin de départ est l'appel (demande du 09/10/2026) : une fiche par groupe
d'anglais. Cette première étape pose les groupes et l'affectation ; la
planification et la fiche d'appel les liront ensuite.

Le NIVEAU d'un étudiant est celui de son groupe habituel de l'année
(`Etudiant.departement`). Un groupe sans niveau n'entre dans aucun niveau :
ses étudiants n'apparaissent nulle part ici, et l'écran nomme ces groupes
plutôt que de les taire.

Un groupe d'anglais est un `Departement` ordinaire — sans filière, de son
niveau —, désigné comme tel par sa ligne `GroupeAnglais`. Rien n'est changé au
modèle `Departement` ni au groupe habituel des étudiants : supprimer les
groupes d'anglais d'un niveau le rend à l'état d'avant.
"""
import unicodedata

from django.db import IntegrityError, transaction
from django.db.models import Count

from .groupes import LIBELLE_NIVEAU_TRANSVERSAL

MAX_GROUPES = 2
MAX_LIGNES_IMPORT = 3000

# Statut de chaque ligne d'une affectation (écran ou Excel).
AFFECTE, CHANGE, INCHANGE, RETIRE = 'affecte', 'change', 'inchange', 'retire'
INCONNU, GROUPE_INCONNU, HORS_NIVEAU = 'inconnu', 'groupe_inconnu', 'hors_niveau'
DOUBLON, VIDE = 'doublon', 'vide'
ECRITS = (AFFECTE, CHANGE, RETIRE)


class RegleAnglais(ValueError):
    """Une règle des groupes d'anglais refuse l'opération ; le message le dit."""


def plier(texte):
    """Sans accents, sans casse, espaces réduits : « Avancé » = « avance »."""
    sans = ''.join(c for c in unicodedata.normalize('NFKD', str(texte or ''))
                   if not unicodedata.combining(c))
    return ' '.join(sans.casefold().split())


# ── Lecture ───────────────────────────────────────────────────────────────────

def groupes_habituels(annee):
    """Les groupes de l'année qui ne sont pas des groupes d'anglais."""
    from apps.departement.models import Departement
    return Departement.objects.filter(annee_universitaire=annee,
                                      groupe_anglais__isnull=True)


def niveaux(annee):
    """Les niveaux qui ont des groupes cette année (le transversal excepté)."""
    from apps.parametres.models import Niveau
    ids = (groupes_habituels(annee).filter(niveau__isnull=False)
           .exclude(niveau__niveau__iexact=LIBELLE_NIVEAU_TRANSVERSAL)
           .values_list('niveau_id', flat=True))
    return Niveau.objects.filter(pk__in=ids).order_by('niveau')


def etudiants_du_niveau(annee, niveau_id):
    """Les étudiants dont le groupe habituel de l'année est de ce niveau."""
    from apps.absence.models import Etudiant
    return Etudiant.objects.filter(
        departement__in=groupes_habituels(annee).filter(niveau_id=niveau_id))


def groupes(annee, niveau_id=None):
    from .models import GroupeAnglais
    qs = (GroupeAnglais.objects.filter(annee_universitaire=annee)
          .select_related('departement', 'niveau')
          .annotate(effectif=Count('affectations')))
    if niveau_id:
        qs = qs.filter(niveau_id=niveau_id)
    return qs.order_by('niveau__niveau', 'rang')


def groupes_sans_niveau(annee):
    """Groupes de planification sans niveau, mais avec des étudiants : ceux-là
    échappent aux groupes d'anglais, l'écran doit le dire."""
    return (groupes_habituels(annee).filter(niveau__isnull=True, is_container=False)
            .annotate(nb=Count('etudiants')).filter(nb__gt=0).order_by('nom'))


def est_du_niveau(etudiant, groupe, annee):
    """Le groupe habituel de l'étudiant est-il, cette année, du niveau du
    groupe d'anglais ?"""
    dep = etudiant.departement
    return (dep is not None and dep.annee_universitaire == annee
            and dep.niveau_id == groupe.niveau_id
            and not hasattr(dep, 'groupe_anglais'))


# ── Les groupes ───────────────────────────────────────────────────────────────

def _nom_valide(annee, nom, sauf=None):
    from .models import GroupeAnglais
    nom = ' '.join(str(nom or '').split())
    if not nom:
        raise RegleAnglais('Le nom du groupe est obligatoire.')
    if len(nom) > 200:
        raise RegleAnglais('Le nom du groupe fait 200 caractères au plus.')
    # Le nom sert à reconnaître le groupe dans un fichier Excel : deux groupes
    # d'anglais de la même année ne peuvent pas porter le même.
    autres = GroupeAnglais.objects.filter(annee_universitaire=annee).select_related('departement')
    if sauf is not None:
        autres = autres.exclude(pk=sauf.pk)
    if any(plier(g.departement.nom) == plier(nom) for g in autres):
        raise RegleAnglais(f'Un autre groupe d\'anglais de {annee} s\'appelle déjà « {nom} ».')
    return nom


def creer_groupe(annee, niveau_id, nom=''):
    """Crée le groupe d'anglais suivant du niveau (le 1, puis le 2)."""
    from apps.departement.models import Departement
    from apps.parametres.models import Niveau
    from .models import GroupeAnglais

    annee = (annee or '').strip()
    niveau = Niveau.objects.filter(pk=niveau_id).first()
    if not annee or niveau is None:
        raise RegleAnglais('Année et niveau sont obligatoires.')
    habituels = groupes_habituels(annee).filter(niveau=niveau)
    if not habituels.exists():
        raise RegleAnglais(f'Aucun groupe de {niveau.niveau} en {annee} : '
                           'il n\'y a personne à mettre en groupe d\'anglais.')
    pris = set(GroupeAnglais.objects.filter(niveau=niveau, annee_universitaire=annee)
               .values_list('rang', flat=True))
    libres = [r for r in range(1, MAX_GROUPES + 1) if r not in pris]
    if not libres:
        raise RegleAnglais(f'{niveau.niveau} a déjà ses {MAX_GROUPES} groupes d\'anglais en {annee}.')
    rang = libres[0]
    nom = _nom_valide(annee, nom or f'Anglais {niveau.niveau} — Groupe {rang}')
    try:
        with transaction.atomic():
            dep = Departement.objects.create(
                nom=nom, niveau=niveau, annee_universitaire=annee,
                institution_id=habituels.values_list('institution_id', flat=True).first(),
                description='Groupe d\'anglais (apps/edt/anglais.py).')
            return GroupeAnglais.objects.create(departement=dep, niveau=niveau,
                                                annee_universitaire=annee, rang=rang)
    except IntegrityError:
        # Deux créations simultanées : la contrainte de la base a tranché.
        raise RegleAnglais(f'{niveau.niveau} a déjà ses {MAX_GROUPES} groupes d\'anglais en {annee}.')


def renommer(groupe, nom):
    dep = groupe.departement
    dep.nom = _nom_valide(groupe.annee_universitaire, nom, sauf=groupe)
    dep.save(update_fields=['nom'])
    return groupe


def references(departement):
    """Ce qui, hors groupes d'anglais, pointe déjà vers ce groupe : séances,
    suivi, grilles… Libellés lisibles, vides si rien."""
    from .models import GroupeAnglais
    trouve = []
    for rel in departement._meta.get_fields():
        if not (rel.is_relation and rel.auto_created and not rel.concrete):
            continue
        if rel.related_model is GroupeAnglais:
            continue
        if rel.many_to_many:
            existe = getattr(departement, rel.get_accessor_name()).exists()
        else:
            existe = (rel.related_model._base_manager
                      .filter(**{rel.field.name: departement}).exists())
        if existe:
            trouve.append(str(rel.related_model._meta.verbose_name_plural))
    return trouve


def supprimer(groupe):
    """Supprime un groupe d'anglais encore vierge — affectations comprises.
    Un groupe qui a déjà des séances, un suivi… ne se supprime pas : ce serait
    effacer de l'historique."""
    dep = groupe.departement
    restes = references(dep)
    if restes:
        raise RegleAnglais(f'« {dep.nom} » est déjà utilisé ({", ".join(restes)}) : '
                           'on ne le supprime pas.')
    with transaction.atomic():
        dep.delete()            # la ligne GroupeAnglais et les affectations suivent


# ── L'affectation ─────────────────────────────────────────────────────────────

def _statut(etudiant, groupe, actuelle, annee):
    """Ce que deviendrait l'affectation de l'étudiant (groupe None = retirer)."""
    if etudiant is None:
        return INCONNU
    if groupe is None:
        return RETIRE if actuelle else INCHANGE
    if not est_du_niveau(etudiant, groupe, annee):
        return HORS_NIVEAU
    if actuelle and actuelle.groupe_id == groupe.pk:
        return INCHANGE
    return CHANGE if actuelle else AFFECTE


def _ecrire(annee, a_ecrire):
    """a_ecrire : (statut, etudiant, groupe, actuelle) ; seuls les statuts
    écrits agissent."""
    from .models import AffectationAnglais
    with transaction.atomic():
        for statut, etudiant, groupe, actuelle in a_ecrire:
            if statut == RETIRE:
                actuelle.delete()
            elif statut == CHANGE:
                actuelle.groupe = groupe
                actuelle.save(update_fields=['groupe', 'modifiee_le'])
            elif statut == AFFECTE:
                AffectationAnglais.objects.create(etudiant=etudiant, groupe=groupe,
                                                  annee_universitaire=annee)


def _etudiant_court(e):
    return {'id': e.pk, 'matricule': e.matricule, 'nom': e.nom,
            'groupe_habituel': e.departement.nom if e.departement_id else ''}


def _groupe_court(g):
    return {'id': g.pk, 'nom': g.departement.nom, 'rang': g.rang} if g else None


def _bilan(lignes):
    bilan = {}
    for l in lignes:
        bilan[l['statut']] = bilan.get(l['statut'], 0) + 1
    return bilan


def affecter(annee, demandes, apercu=False):
    """demandes : [(etudiant_id, groupe_id ou None), …] — None retire.

    Rend {'lignes': [...], 'bilan': {statut: n}}. Rien n'est écrit en aperçu,
    ni pour une ligne refusée."""
    from apps.absence.models import Etudiant
    from .models import AffectationAnglais, GroupeAnglais

    les_groupes = {g.pk: g for g in GroupeAnglais.objects
                   .filter(annee_universitaire=annee).select_related('departement')}
    ids = [e for e, _ in demandes]
    etudiants = Etudiant.objects.select_related('departement').in_bulk(ids)
    actuelles = {a.etudiant_id: a for a in AffectationAnglais.objects
                 .filter(annee_universitaire=annee, etudiant_id__in=ids)}
    vus = {}
    for e, _ in demandes:
        vus[e] = vus.get(e, 0) + 1

    lignes, a_ecrire = [], []
    for etu_id, grp_id in demandes:
        e, actuelle = etudiants.get(etu_id), actuelles.get(etu_id)
        g = les_groupes.get(grp_id) if grp_id else None
        if vus[etu_id] > 1:
            statut = DOUBLON
        elif grp_id and g is None:
            statut = GROUPE_INCONNU
        else:
            statut = _statut(e, g, actuelle, annee)
        lignes.append({'etudiant': _etudiant_court(e) if e else {'id': etu_id},
                       'groupe': _groupe_court(g), 'statut': statut})
        a_ecrire.append((statut, e, g, actuelle))
    if not apercu:
        _ecrire(annee, a_ecrire)
    return {'lignes': lignes, 'bilan': _bilan(lignes)}


# ── L'import Excel ────────────────────────────────────────────────────────────

def _texte(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)                                  # 24607.0 → « 24607 »
    return str(v).strip()


def lire_classeur(fichier):
    """[(n° de ligne, matricule, groupe), …] de la première feuille.

    Une ligne d'en-tête est reconnue à « matricule » ; la colonne du groupe est
    celle qui parle de groupe, de niveau ou d'anglais — à défaut, la suivante.
    Sans en-tête : matricule en A, groupe en B."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(fichier, read_only=True, data_only=True)
    except Exception:
        raise RegleAnglais('Fichier illisible : un classeur Excel (.xlsx) est attendu.')
    lignes = list(wb.worksheets[0].iter_rows(values_only=True))
    wb.close()

    col_mat, col_grp, debut = 0, 1, 0
    for i, ligne in enumerate(lignes[:5]):
        entetes = [plier(_texte(c)) for c in ligne]
        if any('matricule' in h for h in entetes):
            col_mat = next(j for j, h in enumerate(entetes) if 'matricule' in h)
            col_grp = next((j for j, h in enumerate(entetes)
                            if j != col_mat and any(m in h for m in ('groupe', 'niveau', 'anglais'))),
                           col_mat + 1)
            debut = i + 1
            break

    rangs = []
    for i, ligne in enumerate(lignes[debut:], start=debut + 1):
        cellules = list(ligne) + [None, None]
        mat, grp = _texte(cellules[col_mat]), _texte(cellules[col_grp])
        if mat or grp:
            rangs.append((i, mat, grp))
    if len(rangs) > MAX_LIGNES_IMPORT:
        raise RegleAnglais(f'{len(rangs)} lignes : {MAX_LIGNES_IMPORT} au plus par fichier.')
    return rangs


def trouver_groupe(valeur, candidats):
    """Le groupe d'anglais que désigne `valeur` parmi ceux du niveau : son
    numéro (1 ou 2), son nom, ou un morceau de son nom qui n'en désigne
    qu'un (« Advanced » pour « Anglais L3 — Advanced »)."""
    v = plier(valeur)
    if not v:
        return None
    for g in candidats:
        if v == str(g.rang) or v == plier(g.departement.nom):
            return g
    approchants = [g for g in candidats if v in plier(g.departement.nom)]
    return approchants[0] if len(approchants) == 1 else None


def importer(annee, rangs, apercu=False):
    """Applique les lignes lues par `lire_classeur` ; même forme de réponse
    que `affecter`, avec le n° de ligne et ce qui était écrit."""
    from django.db.models.functions import Lower
    from apps.absence.models import Etudiant
    from .models import AffectationAnglais, GroupeAnglais

    par_niveau = {}
    for g in GroupeAnglais.objects.filter(annee_universitaire=annee).select_related('departement'):
        par_niveau.setdefault(g.niveau_id, []).append(g)
    cles = [m.lower() for _, m, _ in rangs if m]
    etudiants = {e.cle: e for e in Etudiant.objects.annotate(cle=Lower('matricule'))
                 .filter(cle__in=cles).select_related('departement')}
    actuelles = {a.etudiant_id: a for a in AffectationAnglais.objects
                 .filter(annee_universitaire=annee,
                         etudiant_id__in=[e.pk for e in etudiants.values()])}
    vus = {}
    for c in cles:
        vus[c] = vus.get(c, 0) + 1

    lignes, a_ecrire = [], []
    for numero, mat, valeur in rangs:
        e = etudiants.get(mat.lower()) if mat else None
        g = None
        if not mat or not valeur:
            statut = VIDE
        elif vus[mat.lower()] > 1:
            statut = DOUBLON
        elif e is None:
            statut = INCONNU
        else:
            dep = e.departement
            du_niveau = (dep is not None and dep.annee_universitaire == annee
                         and dep.niveau_id in par_niveau)
            if not du_niveau:
                statut = HORS_NIVEAU
            else:
                g = trouver_groupe(valeur, par_niveau[dep.niveau_id])
                statut = (GROUPE_INCONNU if g is None
                          else _statut(e, g, actuelles.get(e.pk), annee))
        lignes.append({'ligne': numero, 'matricule': mat, 'valeur': valeur,
                       'etudiant': _etudiant_court(e) if e else None,
                       'groupe': _groupe_court(g), 'statut': statut})
        a_ecrire.append((statut, e, g, actuelles.get(e.pk) if e else None))
    if not apercu:
        _ecrire(annee, a_ecrire)
    return {'lignes': lignes, 'bilan': _bilan(lignes)}
