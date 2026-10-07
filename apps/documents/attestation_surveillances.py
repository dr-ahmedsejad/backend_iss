"""
Les surveillances d'examens sur l'attestation d'enseignement.

Le calcul de l'attestation (`apps/vacation/views.py`, jamais modifié) ne compte
que CM, TD, TP et l'encadrement. Les surveillances d'un vacataire n'y
figuraient donc pas : saisies avec l'EM de l'épreuve, elles donnaient une ligne
vide à 0,00 h (retirée à l'affichage) ; sans EM, elles étaient écartées.

Décision du 07/10/2026 : UNE ligne « Surveillance d'examens » — nombre et
heures réelles —, à part, HORS du volume équivalent CM. Le total attesté et le
QR de vérification restent ceux du calcul.

On compose autour : `VacationViewSet` est hérité, sa méthode de contexte
appelée telle quelle, et l'adresse `vacations/pdf-attestation/` captée en amont
dans `siga/urls.py`.
"""
from django.db.models import Q

from apps.vacation.views import VacationViewSet

# Une vacation de surveillance porte le type « Surveillance », ou celui de
# l'épreuve surveillée (DS, EF, ER).
TYPES_SURVEILLANCE = ('Surveillance', 'DS', 'EF', 'ER')


def surveillances_de(prof, annee, filiere_id=None, date_debut=None, date_fin=None):
    """{'nombre', 'heures'} des surveillances de l'enseignant, ou None."""
    from apps.vacation.models import Vacation

    types = Q()
    for t in TYPES_SURVEILLANCE:
        types |= Q(type__type_seance__iexact=t)
    qs = Vacation.objects.filter(types, prof=prof, annee_univ=annee)
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)
    if filiere_id:
        # Même chaîne que l'attestation filtrée par filière.
        qs = qs.filter(Q(em__filiere_id=filiere_id)
                       | Q(em__module_lmd__filiere_id=filiere_id)
                       | Q(em__departement__filiere_id=filiere_id))
    lignes = list(qs.distinct().values_list('duree', flat=True))
    heures = sum(float(d or 0) for d in lignes)
    if not lignes or heures <= 0:
        return None
    return {'nombre': len(lignes), 'heures': heures}


class AttestationAvecSurveillancesViewSet(VacationViewSet):
    """`VacationViewSet`, dont l'attestation d'ENSEIGNEMENT porte en plus ses
    surveillances. L'attestation de service fait (personnel) les a déjà."""

    def _build_attestation_context(self, request, prof, annee, filiere_id=None,
                                   date_debut=None, date_fin=None):
        resultat = super()._build_attestation_context(
            request, prof, annee, filiere_id=filiere_id,
            date_debut=date_debut, date_fin=date_fin)
        contexte = resultat.get('context_pdf')
        if contexte and not contexte.get('is_service_fait'):
            contexte['surveillances'] = surveillances_de(
                prof, annee, filiere_id, date_debut, date_fin)
        return resultat
