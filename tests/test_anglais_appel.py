"""
Groupes d'anglais — étape 3 : l'appel (09/10/2026).

Un groupe d'anglais n'a aucun étudiant rattaché : ses membres sont ses
AFFECTÉS. La fiche de présence, la liste d'appel (écran et application de
l'enseignant) et la saisie des absences les lisent. Voir apps/edt/anglais.py.

Décor (L2) : 25010 et 25030 en « SEA L2 - G1 », 25020 en « SDID L2 »,
25040 en « SEA L2 - G2 ». Intermediate : 25010, 25020 ; Advanced : 25030 ;
25040 n'est affecté nulle part.
"""
from decimal import Decimal

import pytest

from apps.absence.liste_appel import SOURCE_AFFECTATION, liste_appel
from apps.edt import anglais
from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401
from tests.test_fiches_cm import etudiant, suivie


@pytest.fixture
def ang(monde):
    from apps.em.models import EM
    monde['ems']['ANG'] = EM.objects.create(
        code_em='HE0141', intitule='Anglais', filiere=monde['f_sea'], semestre=monde['s3'],
        institution=monde['inst'], seuil_eliminatoire=Decimal('6.00'))
    e = {m: etudiant(monde, g, m) for m, g in (('25010', 'SEA L2 G1'), ('25030', 'SEA L2 G1'),
                                              ('25020', 'SDID L2'), ('25040', 'SEA L2 G2'))}
    a = anglais.creer_groupe(ANNEE, monde['l2'].pk, 'Anglais L2 — Intermediate')
    b = anglais.creer_groupe(ANNEE, monde['l2'].pk, 'Anglais L2 — Advanced')
    anglais.affecter(ANNEE, [(e['25010'].pk, a.pk), (e['25020'].pk, a.pk), (e['25030'].pk, b.pk)])
    monde['depts']['ANG A'], monde['depts']['ANG B'] = a.departement, b.departement
    return {'A': a.departement, 'B': b.departement, 'etu': e}


def _fiches(monde, dep=None):
    from apps.absence.fiches import fiches_de_la_semaine
    return fiches_de_la_semaine(ANNEE, 1, monde['depts'][dep].id if dep else None)


class TestLaFiche:

    def test_la_fiche_d_un_groupe_d_anglais_liste_ses_affectes(self, monde, ang):
        suivie(monde, 'ANG A', em='ANG', type_='td')
        [f] = _fiches(monde)
        assert [l['matricule'] for l in f['lignes']] == ['25010', '25020']
        assert f['groupe_libelle'] == 'Anglais L2 — Intermediate'
        assert f['liste_non_verifiee'] is False
        # Ni rattaché ni dette : des étudiants de plusieurs filières, tous à
        # leur place.
        assert {l['statut'] for l in f['lignes']} == {''}

    def test_chaque_groupe_d_anglais_a_sa_fiche(self, monde, ang):
        suivie(monde, 'ANG A', em='ANG', type_='td')
        suivie(monde, 'ANG B', em='ANG', type_='td', creneau='09h45-11h15',
               prof='Abderahmane', salle='102')
        par_titre = {f['groupe_libelle']: [l['matricule'] for l in f['lignes']]
                     for f in _fiches(monde)}
        assert par_titre == {'Anglais L2 — Intermediate': ['25010', '25020'],
                             'Anglais L2 — Advanced': ['25030']}

    def test_demandee_pour_le_groupe(self, monde, ang):
        suivie(monde, 'ANG A', em='ANG', type_='td')
        suivie(monde, 'SEA L2 G1', em='SEA31', type_='td', creneau='09h45-11h15',
               prof='Abderahmane', salle='102')
        [f] = _fiches(monde, dep='ANG A')
        assert f['groupe_libelle'] == 'Anglais L2 — Intermediate'

    def test_le_groupe_habituel_ne_change_pas(self, monde, ang):
        suivie(monde, 'SEA L2 G1', em='SEA31', type_='td')
        [f] = _fiches(monde)
        assert [l['matricule'] for l in f['lignes']] == ['25010', '25030']


class TestLaListeDAppel:

    def test_source_affectation(self, monde, ang):
        r = liste_appel(ang['A'].pk, monde['ems']['ANG'].pk, ANNEE)
        assert r['source'] == SOURCE_AFFECTATION
        assert [e.matricule for e in r['etudiants']] == ['25010', '25020']
        assert r['rattaches'] == [] and r['dettes'] == []

    def test_personne_d_affecte_une_fiche_vide(self, monde, ang):
        from apps.edt.models import AffectationAnglais
        AffectationAnglais.objects.all().delete()
        assert liste_appel(ang['A'].pk, monde['ems']['ANG'].pk, ANNEE)['etudiants'] == []

    def test_par_l_adresse(self, monde, gens, ang):
        r = api(gens['admin']).get('/api/v1/absences/presences/liste-appel/', {
            'departement': ang['A'].pk, 'em': monde['ems']['ANG'].pk,
            'annee_universitaire': ANNEE})
        assert r.status_code == 200, r.data
        assert [e['matricule'] for e in r.data['etudiants']] == ['25010', '25020']
        assert r.data['liste_non_verifiee'] is False


class TestLaSaisieDesAbsences:

    def test_les_etudiants_d_un_groupe_d_anglais(self, monde, gens, ang):
        c = api(gens['admin'])
        r = c.get('/api/v1/absences/etudiants/', {'departement': ang['A'].pk, 'page_size': 500})
        assert r.status_code == 200, r.data
        assert sorted(e['matricule'] for e in r.data['results']) == ['25010', '25020']

    def test_ceux_d_un_groupe_habituel_ne_changent_pas(self, monde, gens, ang):
        r = api(gens['admin']).get('/api/v1/absences/etudiants/', {
            'departement': monde['depts']['SEA L2 G1'].pk, 'page_size': 500})
        assert sorted(e['matricule'] for e in r.data['results']) == ['25010', '25030']

    def test_une_absence_en_anglais_s_enregistre(self, monde, gens, ang):
        from apps.absence.models import Presence
        s = suivie(monde, 'ANG A', em='ANG', type_='td')
        r = api(gens['admin']).post('/api/v1/absences/presences/bulk/', {
            'suivi_id': s.pk,
            'presences': [{'etudiant_id': ang['etu']['25020'].pk, 'statut': 1}],
        }, format='json')
        assert r.status_code == 200, r.data
        p = Presence.objects.get(suivi=s)
        assert (p.etudiant.matricule, p.statut) == ('25020', 1)
        # Elle compte pour l'étudiant, dans SON groupe habituel.
        assert p.etudiant.departement == monde['depts']['SDID L2']
