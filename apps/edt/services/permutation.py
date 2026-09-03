"""
Permuter deux enseignants — le geste d'IPGEI, sans son circuit.

IPGEI fait passer l'échange par une demande, l'accord de la contrepartie et la
validation de la direction, parce que plusieurs personnes y planifient. L'ISS
n'a qu'un planificateur, le directeur des études : le circuit n'aurait personne
à qui demander. L'échange s'applique donc sur-le-champ, dans une transaction.

Ce qui s'échange : l'enseignant, la salle et l'élément. Le créneau ne bouge pas
— c'est la définition même d'une permutation, et c'est ce qui la distingue d'un
déplacement de séance.

Ce qui reste : `prof_initial` retient qui devait assurer chaque séance, et il
n'est écrit qu'au PREMIER échange — un second n'efface pas la mémoire du
premier. La charge et le pointage suivent `prof`, donc l'enseignant effectif ;
c'est `prof_initial` qui fait payer le remplaçant et pas le titulaire.

Aucune table nouvelle : la trace vit sur la séance (`origine`, `prof_initial`,
`observations`). Rien à migrer, rien à faire pour revenir en arrière.
"""
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.edt.models import SeanceReelle
from apps.edt.services.partage import propager
from apps.parametres.models import Semaine


def _numeros_du_lot(semaine, nb: int) -> list:
    """
    Les numéros des `nb` semaines de COURS à partir de celle-ci, dans l'ordre du
    calendrier — pas dans l'ordre des numéros, qui peut ne pas être le même.
    """
    lignes = (Semaine.objects
              .filter(annee_universitaire=semaine.annee_universitaire,
                      type_semestre=semaine.type_semestre,
                      type_semaine='cours',
                      numero_semaine__isnull=False)
              .order_by('date')
              .values_list('numero_semaine', 'date'))
    ordre, vus = [], set()
    for numero, _ in lignes:
        if numero not in vus:
            vus.add(numero)
            ordre.append(numero)
    if semaine.numero_semaine not in ordre:
        return [semaine.numero_semaine]
    i = ordre.index(semaine.numero_semaine)
    return ordre[i:i + max(1, nb)]


def _equivalente(seance, numero):
    """La même séance — même groupe, même jour, même créneau — une autre semaine."""
    s = seance.semaine
    return (SeanceReelle.objects
            .filter(departement_id=seance.departement_id,
                    creneau_fk_id=seance.creneau_fk_id,
                    semaine__annee_universitaire=s.annee_universitaire,
                    semaine__type_semestre=s.type_semestre,
                    semaine__numero_semaine=numero,
                    semaine__jour_fk_id=s.jour_fk_id)
            .select_related('semaine')
            .first())


def _echanger(x, y, motif: str) -> None:
    x.prof_initial_id = x.prof_initial_id or x.prof_id
    y.prof_initial_id = y.prof_initial_id or y.prof_id

    x.prof_id,  y.prof_id  = y.prof_id,  x.prof_id
    x.salle_id, y.salle_id = y.salle_id, x.salle_id
    x.em_id,    y.em_id    = y.em_id,    x.em_id
    x.origine = y.origine = SeanceReelle.ORIGINE_PERMUTATION

    if motif:
        for s in (x, y):
            s.observations = f'{s.observations} — {motif}' if s.observations else motif

    x.save()
    y.save()
    # Une séance partagée par plusieurs groupes reste identique sur chacun :
    # sinon la clé de fusion du socle, qui inclut l'enseignant, les séparerait
    # et l'enseignant serait payé deux fois pour un seul cours.
    for s in (x, y):
        if s.cle_partage:
            propager(s)


@transaction.atomic
def permuter_enseignants(a, b, nb_semaines=1, motif: str = '') -> int:
    """
    Échange `a` et `b`, puis leurs équivalentes sur les `nb_semaines - 1`
    semaines de cours suivantes. Une semaine où l'une des deux manque — férié,
    séance supprimée — est sautée : un échange à moitié fait n'est pas un
    échange. Rend le nombre de séances touchées.
    """
    if a.pk == b.pk:
        raise ValidationError('Choisissez deux séances différentes.')
    if a.creneau_fk_id != b.creneau_fk_id:
        raise ValidationError('Les deux séances doivent être sur le même créneau : '
                              'une permutation échange les enseignants, pas les horaires.')
    # Même filière, même année d'étude : on échange les enseignants de G1 et
    # G2 d'une même promotion, pas un L1 de statistique avec un L3 d'une autre
    # filière — leurs éléments ne sont pas interchangeables. Un groupe sans
    # filière (HE, ST, transversal) n'a pas de promotion : rien à échanger.
    da, db = a.departement, b.departement
    if not da.filiere_id or not db.filiere_id:
        raise ValidationError('Une permutation se fait entre deux groupes d’une même '
                              'filière : ce groupe n’en a pas.')
    if da.filiere_id != db.filiere_id or da.niveau_id != db.niveau_id:
        raise ValidationError('Les deux séances doivent appartenir à des groupes de '
                              'même filière et de même année d’étude.')
    # Les éléments échangés doivent être du programme de cette filière. Un
    # élément d'une AUTRE filière, posé par erreur sur l'un des deux groupes,
    # ne doit pas se propager à l'autre par l'échange — c'est exactement
    # l'élément « perdu » : le groupe recevrait un cours qui n'est pas le
    # sien, et le suivi le générerait tel quel. Un élément sans filière (tronc
    # commun) passe.
    for s in (a, b):
        if s.em_id and s.em.filiere_id and s.em.filiere_id != da.filiere_id:
            raise ValidationError(
                f'L’élément {s.em.code_em} n’appartient pas à la filière de ces '
                f'groupes : il ne peut pas s’échanger. Corrigez d’abord la séance.')
    sa, sb = a.semaine, b.semaine
    if (sa.annee_universitaire, sa.type_semestre, sa.numero_semaine) != \
       (sb.annee_universitaire, sb.type_semestre, sb.numero_semaine):
        raise ValidationError('Les deux séances doivent être de la même semaine.')
    if a.annulee or b.annulee:
        raise ValidationError('Une séance annulée ne se permute pas : rétablissez-la d’abord.')

    try:
        nb = int(nb_semaines or 1)
    except (TypeError, ValueError):
        raise ValidationError('Le nombre de semaines doit être un entier.')

    impactees = 0
    for numero in _numeros_du_lot(sa, nb):
        x = a if numero == sa.numero_semaine else _equivalente(a, numero)
        y = b if numero == sb.numero_semaine else _equivalente(b, numero)
        if x is None or y is None or x.annulee or y.annulee:
            continue
        _echanger(x, y, motif)
        impactees += 2
    return impactees
