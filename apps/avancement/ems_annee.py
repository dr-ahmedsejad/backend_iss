"""
Les EM d'une année universitaire.

Un EM n'appartient plus à un groupe d'une année : depuis la refonte, il est
rattaché à sa FILIÈRE et réutilisé d'année en année (apps/em/models.py — son
`departement` est « VESTIGIAL »). Le lien groupe ↔ EM se DÉRIVE : filière du
groupe + niveau du semestre de l'EM (`_scope_groupe_q`, apps/em/views.py).

L'avancement filtrait encore `departement__annee_universitaire=annee`. Mesuré
le 06/10/2026 sur le VPS : 0 EM pour 2026-2027 — tous pointent vers des
groupes de 2022 à 2026 — alors que 18 séances étaient marquées « Fait ».
« Avancement EMs » et « Avancement par semestre » restaient donc vides.

Ici : les EM des groupes de l'année (filière + niveau), plus tout EM qui a une
séance au suivi de l'année — qu'un EM réellement enseigné ne puisse jamais
manquer, même rattaché autrement (enseignement transversal, module LMD…).
"""
from django.db.models import Q

from apps.departement.models import Departement
from apps.em.models import EM
from apps.em.views import _scope_groupe_q
from apps.suivi.models import Suivie


def ems_de_l_annee(annee, type_semestre=None):
    """QuerySet des EM concernés par l'année (sans doublon, sans jointure)."""
    groupes = set(Departement.objects
                  .filter(annee_universitaire=annee, filiere__isnull=False,
                          niveau__isnull=False)
                  .values_list('filiere_id', 'niveau_id'))
    q = Q(pk__in=Suivie.objects.filter(annee_universitaire=annee, em__isnull=False)
          .values('em_id'))
    for filiere_id, niveau_id in groupes:
        q |= _scope_groupe_q(filiere_id, niveau_id)
    qs = EM.objects.filter(q)
    if type_semestre:
        qs = qs.filter(semestre__type_semestre=type_semestre)
    # Les filiations OU (filière / module LMD) dupliquent des lignes : on repart
    # des seuls identifiants, pour que les sommes (CM, TD…) restent justes.
    return EM.objects.filter(pk__in=list(qs.values_list('pk', flat=True).distinct()))


# ── La filière d'un EM (filtre de l'avancement par EM) ────────────────────────
#
# Même chaîne que partout ailleurs (EMSerializer, attestation, vacations) : la
# filière de l'EM, sinon celle de son module LMD, sinon celle de son groupe
# vestigial. 56 EM n'ont pas de filière propre à l'ISS.

def q_filiere(filiere_id):
    """Les EM de cette filière, selon la chaîne ci-dessus."""
    return (Q(filiere_id=filiere_id)
            | Q(filiere__isnull=True, module_lmd__filiere_id=filiere_id)
            | Q(filiere__isnull=True, module_lmd__filiere__isnull=True,
                departement__filiere_id=filiere_id))


def filiere_de(em):
    if em.filiere_id:
        return em.filiere
    if em.module_lmd_id and em.module_lmd.filiere_id:
        return em.module_lmd.filiere
    if em.departement_id and em.departement.filiere_id:
        return em.departement.filiere
    return None


def filieres_de_l_annee(annee, type_semestre=None):
    """Les filières qui ont des EM dans l'année — la liste du filtre."""
    vues = {}
    for em in (ems_de_l_annee(annee, type_semestre)
               .select_related('filiere', 'module_lmd__filiere', 'departement__filiere')):
        f = filiere_de(em)
        if f is not None:
            vues[f.pk] = f
    return sorted(vues.values(), key=lambda f: f.code or '')
