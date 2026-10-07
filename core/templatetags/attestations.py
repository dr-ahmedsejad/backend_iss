"""
Ce que l'attestation d'enseignement IMPRIME (templates/vacation_attestation_pdf.html).

Le calcul vit dans `apps/vacation/views.py`, qui n'est jamais modifié : on
compose autour, au moment de l'affichage. Les heures, le total et le QR de
vérification restent ceux du calcul — rien n'est recompté ici.

Constaté le 07/10/2026 sur l'attestation d'un vacataire (2025-2026) :
  * 15 lignes à « — — — 0,00 » : ses SURVEILLANCES d'examen, saisies en
    vacations avec l'EM de l'épreuve. Le calcul crée la ligne de l'EM mais n'y
    compte que CM, TD et TP. L'attestation laissait croire qu'il avait enseigné
    ces quinze matières ;
  * « a assuré des Cours Magistraux (CM)… » alors qu'il n'en avait fait aucun :
    l'encadrement est compté en colonne CM ;
  * « en filière A et B », les filières de ces lignes vides comprises.
"""
from django import template
from django.utils.html import format_html

register = template.Library()


def _h(ligne, cle):
    try:
        return float(ligne.get(cle) or 0)
    except (TypeError, ValueError):
        return 0.0


def _enumerer(elements):
    """« A », « A et B », « A, B et C »."""
    elements = list(elements)
    if len(elements) <= 1:
        return ''.join(elements)
    return ', '.join(elements[:-1]) + ' et ' + elements[-1]


@register.filter
def avec_heures(modules):
    """Les lignes qui portent des heures : une ligne à 0 h n'atteste rien."""
    return [m for m in (modules or []) if _h(m, 'total_pondere') > 0]


@register.filter
def texte_seances(modules, texte_calcule=''):
    """« des Travaux Dirigés (TD), des Travaux Pratiques (TP) et de
    l'encadrement » — d'après les lignes réellement imprimées. L'encadrement,
    compté en colonne CM par le calcul, est nommé pour ce qu'il est."""
    lignes = avec_heures(modules)
    enc = [m for m in lignes if m.get('is_encadrement_perm')]
    autres = [m for m in lignes if not m.get('is_encadrement_perm')]
    parties = []
    if any(_h(m, 'heures_cm') > 0 for m in autres):
        parties.append('des Cours Magistraux (CM)')
    if any(_h(m, 'heures_td') > 0 for m in lignes):
        parties.append('des Travaux Dirigés (TD)')
    if any(_h(m, 'heures_tp') > 0 for m in lignes):
        parties.append('des Travaux Pratiques (TP)')
    if any(_h(m, 'heures_cm') > 0 for m in enc):
        parties.append("de l'encadrement")
    if any(_h(m, 'heures_service') > 0 for m in lignes):
        parties.append("des activités de service (surveillance d'examens, missions)")
    return _enumerer(parties) or texte_calcule


@register.filter
def phrase_filieres(modules):
    """« dans la filière A » / « dans les filières A et B » — les seules
    filières des lignes imprimées. Vide si aucune n'est connue : le gabarit
    retombe alors sur les départements, comme avant."""
    filieres = []
    for m in avec_heures(modules):
        for f in str(m.get('filiere') or '').split(' / '):
            f = f.strip()
            if f and f != '—' and f not in filieres:
                filieres.append(f)
    if not filieres:
        return ''
    filieres.sort()
    return format_html('dans {} <span class="bold">{}</span>',
                       'la filière' if len(filieres) == 1 else 'les filières',
                       _enumerer(filieres))
