"""
Le CM : une seule fiche pour les groupes réunis.

Un cours magistral réunit plusieurs groupes devant un seul enseignant, mais
l'emploi du temps l'enregistre groupe par groupe : une ligne `Suivie` par
groupe, même jour, même créneau, même élément, même enseignant. Sur la base du
02/10/2026, les 38 CM de la semaine étaient tous à deux groupes : l'enseignant
recevait deux fiches pour un seul cours. TD et TP restent par groupe.
"""
import re

import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401


def etudiant(monde, groupe, matricule):
    from apps.absence.models import Etudiant
    return Etudiant.objects.create(matricule=matricule, nom='Nom%s' % matricule,
                                   departement=monde['depts'][groupe])


def suivie(monde, groupe, em='SEA11', type_='cm', jour='Lundi',
           creneau='08h00-09h30', prof='Moustapha', salle='101'):
    from apps.suivi.models import Suivie
    return Suivie.objects.create(
        annee_universitaire=ANNEE, type_semestre='I', numero_semaine=1,
        institution=monde['inst'], departement=monde['depts'][groupe],
        em=monde['ems'][em], prof=monde['profs'][prof], salle=monde['salles'][salle],
        jour_fk=monde['jours'][jour], creneau_fk=monde['creneaux'][creneau],
        type_seance_fk=monde[type_])


@pytest.fixture
def classe(monde):
    """Deux groupes de L1, matricules entrelacés."""
    for m in ('24610', '24630'):
        etudiant(monde, 'G1', m)
    for m in ('24620', '24640'):
        etudiant(monde, 'G2', m)
    return monde


def fiches(monde, dep=None):
    from apps.absence.fiches import fiches_de_la_semaine
    return fiches_de_la_semaine(ANNEE, 1, monde['depts'][dep].id if dep else None)


def matricules(fiche):
    return [l['matricule'] for l in fiche['lignes']]


class TestCmReuni:

    def test_un_cm_a_deux_groupes_fait_une_seule_fiche(self, classe):
        suivie(classe, 'G1')
        suivie(classe, 'G2')
        [f] = fiches(classe)
        assert f['cm_commun'] is True
        assert f['groupes'] == ['L1 G1', 'L1 G2']
        assert f['groupe_libelle'] == 'L1'
        # La liste globale, par matricule croissant, groupes entrelacés.
        assert matricules(f) == ['24610', '24620', '24630', '24640']

    def test_demande_par_un_seul_groupe_la_fiche_du_cm_reste_globale(self, classe):
        suivie(classe, 'G1')
        suivie(classe, 'G2')
        [f] = fiches(classe, dep='G1')
        assert f['groupes'] == ['L1 G1', 'L1 G2']
        assert matricules(f) == ['24610', '24620', '24630', '24640']

    def test_un_cm_charge_passe_en_deux_colonnes(self, classe):
        for i in range(20):
            etudiant(classe, 'G1', '25%03d' % i)
            etudiant(classe, 'G2', '26%03d' % i)
        suivie(classe, 'G1')
        suivie(classe, 'G2')
        [f] = fiches(classe)
        assert len(f['lignes']) == 44 and len(f['paires']) == 22

    def test_un_cm_d_un_seul_groupe_reste_tel_quel(self, classe):
        suivie(classe, 'G1')
        [f] = fiches(classe)
        assert f['cm_commun'] is False
        assert f['groupe_libelle'] == 'L1 G1'
        assert matricules(f) == ['24610', '24630']


class TestCeQuiNeSeReunitPas:

    def test_le_td_reste_par_groupe(self, classe):
        suivie(classe, 'G1', type_='td')
        suivie(classe, 'G2', type_='td')
        f1, f2 = fiches(classe)
        assert (f1['groupe_libelle'], matricules(f1)) == ('L1 G1', ['24610', '24630'])
        assert (f2['groupe_libelle'], matricules(f2)) == ('L1 G2', ['24620', '24640'])

    def test_deux_enseignants_au_meme_creneau_font_deux_cours(self, classe):
        suivie(classe, 'G1', prof='Moustapha')
        suivie(classe, 'G2', prof='Abderahmane', salle='102')
        assert [f['groupe_libelle'] for f in fiches(classe)] == ['L1 G1', 'L1 G2']

    def test_deux_elements_au_meme_creneau_font_deux_cours(self, classe):
        suivie(classe, 'G1', em='SEA11')
        suivie(classe, 'G2', em='SEA12', prof='Abderahmane', salle='102')
        assert len(fiches(classe)) == 2

    def test_le_td_d_un_groupe_ne_ramene_pas_l_autre(self, classe):
        suivie(classe, 'G1', type_='td')
        suivie(classe, 'G2', type_='td')
        [f] = fiches(classe, dep='G1')
        assert f['groupe_libelle'] == 'L1 G1'


class TestReunir:

    def test_un_etudiant_ne_figure_qu_une_fois(self):
        """Dette dans la liste d'un groupe, membre de l'autre : une seule ligne,
        comme membre."""
        from apps.absence.fiches import reunir
        g1 = {'etudiants': [{'matricule': '1'}], 'rattaches': [],
              'dettes': [{'matricule': '2', 'groupe': 'G2'}], 'liste_non_verifiee': False}
        g2 = {'etudiants': [{'matricule': '2'}], 'rattaches': [], 'dettes': [],
              'liste_non_verifiee': True}
        r = reunir([g1, g2])
        assert [e['matricule'] for e in r['etudiants']] == ['1', '2']
        assert r['dettes'] == []
        assert r['liste_non_verifiee'] is True


class TestEcranEtPdf:

    def test_l_ecran_lit_les_memes_fiches(self, classe, gens):
        suivie(classe, 'G1')
        suivie(classe, 'G2')
        r = api(gens['admin']).get('/api/v1/absences/presences/fiches/',
                                   {'annee_universitaire': ANNEE, 'numero_semaine': 1})
        assert r.status_code == 200, r.status_code
        [f] = r.json()
        assert f['groupe_libelle'] == 'L1'
        assert [l['matricule'] for l in f['lignes']] == ['24610', '24620', '24630', '24640']

    def test_le_pdf_titre_les_groupes_reunis(self, classe):
        from django.template.loader import render_to_string
        from core.pdf_utils import get_institution_context
        suivie(classe, 'G1')
        suivie(classe, 'G2')
        html = render_to_string('absence/fiches_presence.html', {
            'fiches': fiches(classe), 'annee_universitaire': ANNEE, 'numero_semaine': 1,
            **get_institution_context()})
        assert html.count('Fiche de Présence') == 1
        # Le niveau seul, et l'enseignant nommé comme tel.
        titre = ' '.join(re.search(r'class="fiche-title">(.*?)</div>', html, re.S).group(1).split())
        assert titre == 'Fiche de Présence — Statistique et Économie Appliquée — L1', titre
        assert 'Enseignant :' in html and 'Professeur' not in html
        assert "Signature de l'enseignant" in html
        assert re.findall(r'class="mat-code">(\w+)<', html) == ['24610', '24620', '24630', '24640']
