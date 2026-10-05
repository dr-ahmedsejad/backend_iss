"""
Les fiches de présence d'une semaine — ce que le PDF imprime et ce que l'écran
affiche, calculé UNE fois ici.

Une fiche par séance et par groupe, SAUF le CM : un cours magistral réunit
plusieurs groupes devant un seul enseignant. L'emploi du temps l'enregistre
pourtant groupe par groupe — une ligne `Suivie` par groupe, même jour, même
créneau, même élément, même enseignant (sur la base du 02/10/2026 : les 38 CM
de la semaine, tous à deux groupes, dans la même salle). L'enseignant recevait
donc deux fiches pour un seul cours. Ici, ces lignes ne font qu'UNE fiche, avec
la liste globale des groupes réunis, par matricule croissant.

Rien ici n'ÉCRIT.
"""
from apps.absence.libelles import libelle_groupe
from apps.absence.liste_appel import (SOURCE_GROUPE, colonnes_de_fiche, liste_appel,
                                      lignes_de_fiche)

JOURS_ORDRE = {'Lundi': 1, 'Mardi': 2, 'Mercredi': 3, 'Jeudi': 4, 'Vendredi': 5, 'Samedi': 6}
SURVEILLANCES = ('DS', 'ER', 'EF')


def _jour(s):    return s.jour_fk.jour if s.jour_fk_id and s.jour_fk else ''
def _creneau(s): return s.creneau_fk.creneau if s.creneau_fk_id and s.creneau_fk else ''
def _type(s):    return s.type_seance_fk.type_seance if s.type_seance_fk_id and s.type_seance_fk else ''


def est_cm(s):
    return _type(s).strip().upper() == 'CM'


def cle_cm(s):
    """Ce qui fait UN cours magistral : même jour, même créneau, même élément,
    même enseignant (la semaine est commune à toute la requête)."""
    return (s.jour_fk_id, s.creneau_fk_id, s.em_id, s.prof_id)


def _resume(e, avec_groupe=False):
    d = {'id': e.id, 'matricule': e.matricule, 'nom': e.nom, 'genre': e.genre}
    if avec_groupe:
        d['groupe'] = e.departement.nom if e.departement_id else ''
    return d


def _liste(dep_id, em_id, annee, cache):
    cle = (dep_id, em_id)
    if cle not in cache:
        r = liste_appel(dep_id, em_id, annee)
        cache[cle] = {
            'etudiants': [_resume(e) for e in r['etudiants']],
            'rattaches': [{**_resume(e), 'filiere': e.filiere_inscription} for e in r['rattaches']],
            'dettes':    [_resume(e, avec_groupe=True) for e in r['dettes']],
            'liste_non_verifiee': r['source'] == SOURCE_GROUPE,
        }
    return cache[cle]


def reunir(listes):
    """Les listes de plusieurs groupes en une : un étudiant n'y figure qu'une
    fois — comme membre d'un groupe réuni plutôt que comme dette, s'il est les
    deux. Non vérifiée si l'une l'est."""
    vus, etudiants, rattaches, dettes = set(), [], [], []
    for cle, cible in (('etudiants', etudiants), ('rattaches', rattaches), ('dettes', dettes)):
        for l in listes:
            for e in l[cle]:
                if e['matricule'] not in vus:
                    vus.add(e['matricule'])
                    cible.append(e)
    return {'etudiants': etudiants, 'rattaches': rattaches, 'dettes': dettes,
            'liste_non_verifiee': any(l['liste_non_verifiee'] for l in listes)}


def fiches_de_la_semaine(annee, semaine, departement_id=None):
    from apps.departement.models import Departement
    from apps.suivi.models import Suivie

    qs = (Suivie.objects
          .filter(annee_universitaire=annee, numero_semaine=int(semaine))
          .select_related('prof', 'em', 'salle', 'creneau_fk', 'departement',
                          'jour_fk', 'type_seance_fk')
          .order_by('jour_fk__jour', 'creneau_fk__creneau'))
    seances = sorted(qs, key=lambda x: (JOURS_ORDRE.get(_jour(x), 9), _creneau(x)))
    # Pas de fiche pour une séance sans type ou sans élément (sport, instruction
    # militaire, lignes vides).
    seances = [s for s in seances if _type(s) and s.em_id]

    if departement_id:
        dep = int(departement_id)
        # Le CM d'un groupe garde ses groupes frères : la fiche du cours est
        # la même, quel que soit le groupe par lequel on la demande.
        cm_du_groupe = {cle_cm(s) for s in seances if s.departement_id == dep and est_cm(s)}
        seances = [s for s in seances
                   if s.departement_id == dep or (est_cm(s) and cle_cm(s) in cm_du_groupe)]

    # Regroupement : un CM = toutes ses lignes ; sinon une séance par groupe.
    paquets, index = [], {}
    for s in seances:
        cle = (('CM',) + cle_cm(s)) if est_cm(s) else \
              (_jour(s), _creneau(s), _type(s), s.departement_id)
        if cle not in index:
            index[cle] = len(paquets)
            paquets.append([])
        if s.departement_id not in {x.departement_id for x in paquets[index[cle]]}:
            paquets[index[cle]].append(s)

    infos = {d.pk: d for d in Departement.objects.select_related('niveau', 'filiere')
             .filter(pk__in={s.departement_id for s in seances if s.departement_id})}

    def libelle(dep_id):
        d = infos.get(dep_id)
        if d is None:
            return '—'
        return libelle_groupe(d.nom, d.niveau.niveau if d.niveau_id else '')

    cache, fiches = {}, []
    for paquet in paquets:
        s = paquet[0]
        groupes = sorted((x.departement_id for x in paquet if x.departement_id),
                         key=lambda g: libelle(g))
        listes = [_liste(g, s.em_id, annee, cache) for g in groupes]
        liste = reunir(listes) if listes else {
            'etudiants': [], 'rattaches': [], 'dettes': [], 'liste_non_verifiee': True}
        filieres = []
        for g in groupes:
            d = infos.get(g)
            f = d.filiere.intitule_fr if d is not None and d.filiere_id else ''
            if f and f not in filieres:
                filieres.append(f)
        # Le titre d'un CM réuni porte le NIVEAU seul — « L3 » et non
        # « L3 G1 + L3 G2 » (demande du 05/10/2026). Des groupes de niveaux
        # différents, s'il s'en trouvait, restent nommés un par un.
        niveaux = {infos[g].niveau.niveau for g in groupes
                   if g in infos and infos[g].niveau_id}
        if len(groupes) > 1 and len(niveaux) == 1:
            titre = niveaux.pop()
        else:
            titre = ' + '.join(libelle(g) for g in groupes) or '—'
        type_label = _type(s)
        creneau = _creneau(s)
        lignes = lignes_de_fiche(liste['etudiants'], liste['rattaches'], liste['dettes'])
        fiches.append({
            'id':             s.pk,
            'groupes':        [libelle(g) for g in groupes],
            'cm_commun':      len(groupes) > 1,
            # « L3 » pour un CM réuni ; « L1 G1 » et non « G1 » sinon :
            # plusieurs groupes s'appellent G1 (apps/absence/libelles.py).
            'groupe_libelle': titre,
            'dep_nom':        ' + '.join(infos[g].nom for g in groupes if g in infos) or '—',
            'filiere':        ' / '.join(filieres),
            'date_seance':    s.date_suivie,
            'jour':           _jour(s) or '—',
            'creneau_label':  creneau,
            'creneau':        creneau.replace('-', ' à ') if creneau else '—',
            'type_seance':    type_label or '—',
            # DS / ER / EF : examen, fiche signée par le surveillant.
            'is_surveillance': type_label in SURVEILLANCES,
            'numero_semaine': s.numero_semaine,
            'em_code':        s.em.code_em if s.em_id else '',
            'em_intitule':    s.em.intitule if s.em_id else '—',
            'prof_nom':       s.prof.nom if s.prof_id else '—',
            'salle_nom':      s.salle.nom if s.salle_id else '—',
            **liste,
            # Ce que la fiche imprime : une seule liste, par matricule croissant.
            'lignes': lignes,
            # Longue liste (un CM réuni) : deux colonnes sur la page imprimée.
            'paires': colonnes_de_fiche(lignes),
        })
    return fiches
