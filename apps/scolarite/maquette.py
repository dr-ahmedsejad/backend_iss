"""
La MAQUETTE d'une filière : ses modules et leurs éléments, semestre par semestre.

Ce module assemble les DONNÉES ; le gabarit `templates/maquette_filiere_pdf.html`
ne fait que les disposer. Les règles vivent ici, là où les tests peuvent les
atteindre sans passer par wkhtmltopdf.

Les ÉLÉMENTS sont les `em.EM` rattachés au module par `module_lmd`, et non les
`modules.ElementModule` : mesuré sur la base `iss` le 16/09/2026,
`ElementModule` compte 0 ligne, `EM` en compte 207 dont 151 rattachées à un
module. Un modèle vide ne produit qu'une maquette vide.

Cinq règles, chacune payée par un défaut réel :

1. LA MAQUETTE EST LE PARCOURS DE L'ÉTUDIANT. Une filière fille commence par les
   semestres de sa mère : sans eux, SEA démarrerait en L2 et ne totaliserait
   jamais 180 crédits. Ces semestres sont marqués « Tronc commun ».

   On ne reprend de la mère QUE les niveaux situés avant ceux de la fille, et
   dans le parcours que la mère déclare (`niveau_debut`..`niveau_fin`). Ce n'est
   pas une précaution d'école : LPSTAT, tronc commun déclaré L1 seul, porte
   aussi neuf modules en S4, S5 et S6 — des copies de ceux de SEA. Reprendre
   « tous les semestres de la mère » aurait donné à SEA deux S4, deux S5 et
   deux S6.

   La maquette d'une mère ne contient pas ses filles : seuls les modules de la
   filière elle-même y entrent.

2. LES TOTAUX SONT RECALCULÉS depuis les éléments, jamais recopiés d'un champ.
   Un semestre qui ne fait pas sa norme le dit sous son tableau.

3. LA COLONNE TP n'apparaît que si un élément de la maquette a du TP.

4. UN MODULE INACTIF n'entre pas dans une maquette publiée.

5. Un module rattaché à la filière mais HORS de son parcours déclaré n'est pas
   rangé dans un semestre qui n'est pas le sien : il est écarté, et nommé dans
   `hors_parcours` pour que le document le dise au lieu de le taire.
"""
import re

from apps.scolarite.models import TYPE_DIPLOME_CHOICES

# Norme de crédits : portée par chaque `Semestre.credits` (30 partout à l'ISS).
#
# Norme de coefficient : AUCUN champ ne la porte dans ce dépôt, et aucun code ne
# la vérifie. On retient 20, parce que c'est ce que font les données : mesuré le
# 16/09/2026, les 20 semestres de la base qui ont des éléments totalisent tous
# 20 de coefficient. (Le chiffre de 22 venait d'un autre établissement.)
COEFFICIENT_SEMESTRE = 20

_LIBELLE_DIPLOME = dict(TYPE_DIPLOME_CHOICES)


def _chiffre(texte, defaut=None):
    """Le premier nombre d'un libellé : « S10 » → 10, « L2 » → 2."""
    m = re.search(r'\d+', texte or '')
    return int(m.group()) if m else defaut


def numero_semestre(semestre):
    """Le NUMÉRO d'un semestre, pour trier : sur la chaîne, « S10 » passerait
    avant « S2 »."""
    return _chiffre(semestre.code_semestre, 0)


def niveau_du_semestre(semestre):
    """Le niveau d'étude d'un semestre (1 pour L1).

    Lu sur `niveau_semestre` ; à défaut d'un chiffre dans son libellé, déduit du
    numéro — deux semestres par année.
    """
    niveau = _chiffre(getattr(semestre.niveau_semestre, 'niveau', ''))
    if niveau is None:
        niveau = (numero_semestre(semestre) + 1) // 2
    return niveau


def chaine_des_meres(filiere):
    """[aïeule, …, mère, filière] — du tronc commun le plus lointain à elle.

    Garde contre un cycle : une filière déjà vue arrête la remontée.
    """
    chaine, vues = [], set()
    courante = filiere
    while courante is not None and courante.pk not in vues:
        vues.add(courante.pk)
        chaine.append(courante)
        courante = courante.filiere_parent
    return list(reversed(chaine))


def _nombre(valeur):
    return valeur if valeur is not None else 0


def _element(em):
    cm, td, tp = _nombre(em.CM), _nombre(em.TD), _nombre(em.TP)
    return {
        'code':        em.code_em,
        'intitule':    em.intitule,
        'credits':     em.credits,
        'coefficient': em.coefficient,
        'cm': cm, 'td': td, 'tp': tp,
        'total': cm + td + tp,
    }


def _semestre(semestre, filiere, modules, tronc_commun):
    """Un bloc de semestre, totaux recalculés et écarts à la norme nommés."""
    blocs_modules = []
    totaux = dict(credits=0, coefficient=0, cm=0, td=0, tp=0, total=0)
    for module in modules:
        elements = [_element(em) for em in
                    sorted(module.ems_planification.all(),
                           key=lambda e: (e.code_em or '', e.pk))]
        for e in elements:
            totaux['credits']     += _nombre(e['credits'])
            totaux['coefficient'] += _nombre(e['coefficient'])
            for cle in ('cm', 'td', 'tp', 'total'):
                totaux[cle] += e[cle]
        blocs_modules.append({
            'code':     module.code,
            'intitule': module.intitule_fr,
            'elements': elements,
            # Un module sans élément occupe tout de même sa ligne.
            'rowspan':  max(len(elements), 1),
        })

    ecarts = []
    norme_credits = semestre.credits or 30
    if totaux['credits'] != norme_credits:
        ecarts.append('Ce semestre totalise %s crédits au lieu de %s.'
                      % (totaux['credits'], norme_credits))
    if totaux['coefficient'] != COEFFICIENT_SEMESTRE:
        ecarts.append('Ce semestre totalise %s de coefficient au lieu de %s.'
                      % (totaux['coefficient'], COEFFICIENT_SEMESTRE))

    numero = numero_semestre(semestre)
    niveau = niveau_du_semestre(semestre)
    diplome = _LIBELLE_DIPLOME.get(filiere.type_diplome, 'Licence')
    return {
        'code':             semestre.code_semestre,
        'numero':           numero,
        'niveau':           niveau,
        'libelle_niveau':   '%s %s' % (diplome, niveau),
        'libelle_semestre': 'Semestre %s' % numero,
        'tronc_commun':     tronc_commun,
        'modules':          blocs_modules,
        'totaux':           totaux,
        'ecarts':           ecarts,
    }


def assembler_maquette(filiere):
    """Les données de la maquette de `filiere`, prêtes à disposer.

    {
      'filiere':       {'code', 'intitule'},
      'avec_tp':       bool,
      'semestres':     [bloc de semestre, trié par numéro],
      'hors_parcours': [codes des modules de la filière hors de son parcours],
      'total_credits': int,
    }
    """
    from apps.modules.models import Module

    chaine = chaine_des_meres(filiere)
    semestres, hors_parcours = [], []

    for rang, porteuse in enumerate(chaine):
        est_la_filiere = porteuse.pk == filiere.pk
        haut = porteuse.niveau_fin
        if not est_la_filiere:
            # Une mère ne fournit que les niveaux qui PRÉCÈDENT sa fille.
            haut = min(haut, chaine[rang + 1].niveau_debut - 1)

        modules = (Module.objects
                   .filter(filiere=porteuse, actif=True)
                   .select_related('semestre', 'semestre__niveau_semestre')
                   .prefetch_related('ems_planification')
                   .order_by('code'))

        par_semestre = {}
        for module in modules:
            niveau = niveau_du_semestre(module.semestre)
            if not (porteuse.niveau_debut <= niveau <= haut):
                if est_la_filiere:
                    hors_parcours.append(module.code)
                continue
            par_semestre.setdefault(module.semestre.pk,
                                    (module.semestre, []))[1].append(module)

        for semestre, mods in par_semestre.values():
            semestres.append(_semestre(
                semestre, filiere, mods,
                tronc_commun=None if est_la_filiere else porteuse.code))

    semestres.sort(key=lambda s: s['numero'])
    avec_tp = any(e['tp'] > 0
                  for s in semestres for m in s['modules'] for e in m['elements'])
    return {
        'filiere':       {'code': filiere.code, 'intitule': filiere.intitule_fr},
        'avec_tp':       avec_tp,
        'semestres':     semestres,
        'hors_parcours': sorted(hors_parcours),
        'total_credits': sum(s['totaux']['credits'] for s in semestres),
    }
