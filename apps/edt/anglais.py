"""
Groupes d'anglais : les créer, y affecter les étudiants, lire une affectation
venue d'Excel. Modèles : `GroupeAnglais`, `AffectationAnglais` (models.py).

L'étudiant garde son groupe habituel pour tout le reste. Pour l'anglais, il
est affecté à l'un des DEUX groupes d'anglais de son niveau, pour l'année.
Le besoin de départ est l'appel (demande du 09/10/2026) : une fiche par groupe
d'anglais. Étape 1 : les groupes et l'affectation ; étape 2 : la
planification (règles en fin de module, appliquées par serializers.py) ; la
fiche d'appel les lira ensuite.

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
            groupe = GroupeAnglais.objects.create(departement=dep, niveau=niveau,
                                                  annee_universitaire=annee, rang=rang)
            _deleguer(dep, habituels)
            return groupe
    except IntegrityError:
        # Deux créations simultanées : la contrainte de la base a tranché.
        raise RegleAnglais(f'{niveau.niveau} a déjà ses {MAX_GROUPES} groupes d\'anglais en {annee}.')


def _deleguer(departement, habituels):
    """Qui planifie un groupe du niveau planifie aussi ses groupes d'anglais.

    La délégation EDT est par groupe (`managed_departements`) : sans elle, le
    directeur des études ne verrait pas le groupe d'anglais qu'on vient de
    créer pour lui. Les autres délégations se règlent, comme toujours, dans
    « Paramètres → Permissions EDT »."""
    from django.contrib.auth import get_user_model
    for u in (get_user_model().objects
              .filter(managed_departements__in=habituels).distinct()):
        u.managed_departements.add(departement)


def renommer(groupe, nom):
    dep = groupe.departement
    dep.nom = _nom_valide(groupe.annee_universitaire, nom, sauf=groupe)
    dep.save(update_fields=['nom'])
    return groupe


def references(departement):
    """Ce qui, hors groupes d'anglais, pointe déjà vers ce groupe : séances,
    suivi, grilles… Libellés lisibles, vides si rien."""
    from django.contrib.auth import get_user_model
    from .models import GroupeAnglais
    trouve = []
    for rel in departement._meta.get_fields():
        if not (rel.is_relation and rel.auto_created and not rel.concrete):
            continue
        # Ses propres lignes, et la délégation EDT posée à la création
        # (`_deleguer`) : ni l'une ni l'autre n'est un usage.
        if rel.related_model in (GroupeAnglais, get_user_model()):
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


# ── La planification (étape 2) ────────────────────────────────────────────────
#
# Une séance d'anglais se reconnaît à son élément : l'intitulé commence par
# « Anglais » (les dix-huit EM d'anglais de l'ISS s'appellent tous ainsi, une
# fiche par filière). Elle se pose sur un groupe d'anglais, dont le créneau
# n'est pas fixe : chaque placement, dans la grille comme dans la semaine,
# repasse par les règles ci-dessous (apps/edt/serializers.py).

MOT_ANGLAIS = 'anglais'


def est_intitule_anglais(intitule):
    return plier(intitule).startswith(MOT_ANGLAIS)


def groupe_anglais_de(departement):
    """La ligne GroupeAnglais du groupe, ou None pour un groupe habituel."""
    from django.core.exceptions import ObjectDoesNotExist
    if departement is None or departement.pk is None:
        return None
    try:
        return departement.groupe_anglais
    except ObjectDoesNotExist:
        return None


def croisement(a, b, memes_etudiants):
    """Les groupes `a` et `b` ont-ils des étudiants en commun, quand l'un des
    deux est un groupe d'anglais ? None si aucun ne l'est — la règle
    ordinaire s'applique alors.

    `memes_etudiants` est la règle ordinaire entre deux groupes habituels.

    * Deux groupes d'anglais DIFFÉRENTS ne partagent personne : un étudiant
      n'a qu'une affectation par année. Intermediate et Advanced peuvent donc
      avoir cours en même temps.
    * Un groupe d'anglais et un groupe habituel se croisent dès qu'un étudiant
      affecté vient de ce groupe — ou d'un groupe qui le recoupe (le groupe
      entier et ses sous-groupes, un transversal…).
    * Tant que personne n'est affecté, on suppose le niveau entier : laisser
      passer serait découvrir la collision le jour où l'affectation arrive.
    """
    ga, gb = groupe_anglais_de(a), groupe_anglais_de(b)
    if ga is None and gb is None:
        return None
    if ga is not None and gb is not None:
        return ga.pk == gb.pk
    g, autre = (ga, b) if ga is not None else (gb, a)
    if autre.annee_universitaire != g.annee_universitaire:
        return False
    from apps.departement.models import Departement
    from .groupes import est_transversal
    habituels = list(Departement.objects
                     .filter(etudiants__affectations_anglais__groupe=g)
                     .distinct())
    if not habituels:
        return autre.niveau_id == g.niveau_id or est_transversal(autre)
    return any(memes_etudiants(h, autre) for h in habituels)


def _em(em_id):
    from apps.em.models import EM
    return (EM.objects.select_related('semestre', 'module_lmd__semestre')
            .filter(pk=em_id).first()) if em_id else None


def _niveau_de_l_em(em):
    if em.semestre_id and em.semestre.niveau_semestre_id:
        return em.semestre.niveau_semestre_id
    if em.module_lmd_id and em.module_lmd.semestre_id:
        return em.module_lmd.semestre.niveau_semestre_id
    return None


def motif_refus_em(departement, em_id, annee, nouvel_em):
    """Pourquoi cet élément ne peut pas aller sur ce groupe — ou None.

    * Un groupe d'anglais ne reçoit que l'anglais de SON niveau : il n'existe
      que pour lui, et ses étudiants ont leurs autres cours ailleurs.
    * Un groupe HABITUEL d'un niveau qui a ses groupes d'anglais cette année
      ne reçoit plus d'anglais : ses étudiants figureraient sur deux fiches
      d'appel. Seulement quand l'élément est posé ou changé (`nouvel_em`) —
      une séance d'anglais déjà là reste modifiable, le temps de la déplacer.
    """
    from .models import GroupeAnglais
    if departement is None:
        return None
    g = groupe_anglais_de(departement)
    em = _em(em_id)
    if g is not None:
        if em is None or not est_intitule_anglais(em.intitule):
            return (f'« {departement.nom} » est un groupe d\'anglais : il ne reçoit '
                    'que des séances d\'anglais. Choisissez un élément « Anglais ».')
        niveau = _niveau_de_l_em(em)
        if niveau is not None and niveau != g.niveau_id:
            return (f'{em.code_em} n\'est pas de l\'anglais de {g.niveau.niveau} : '
                    f'« {departement.nom} » réunit les étudiants de {g.niveau.niveau}.')
        return None
    if not (nouvel_em and em is not None and est_intitule_anglais(em.intitule)):
        return None
    if not departement.niveau_id:
        return None
    noms = list(GroupeAnglais.objects
                .filter(niveau_id=departement.niveau_id, annee_universitaire=annee)
                .order_by('rang').values_list('departement__nom', flat=True))
    if not noms:
        return None
    return (f'L\'anglais de {departement.niveau.niveau} se planifie sur ses groupes '
            f'd\'anglais ({", ".join(noms)}) : les étudiants de « {departement.nom} » '
            'y sont affectés. Posé ici, il les mettrait sur deux fiches d\'appel.')
