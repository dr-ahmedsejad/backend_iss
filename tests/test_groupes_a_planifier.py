"""
Quels groupes méritent une place dans les écrans de planification.

Un groupe sans étudiant n'a pas d'emploi du temps à construire : le proposer
allonge la liste et fait remplir le vide. `?avec_etudiants=1` les écarte.

Le filtre est un OPT-IN : sans le paramètre, la liste reste entière. Les autres
écrans — admissions, statistiques, paramètres, et surtout le suivi et les
vacations — ne doivent rien voir changer.

L'EXCEPTION qui justifie la moitié de ce fichier : un groupe sans étudiant mais
qui porte DÉJÀ des séances reste visible. Mesuré le 30/09/2026, le groupe #45
(G2, STAT L1) a zéro étudiant et dix-huit séances : le masquer le rendrait
inatteignable depuis la grille alors que ses séances continuent d'alimenter
`Emplois`, le suivi et les vacations.
"""
import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL = '/api/v1/departements/all/'


def etudiant(monde, dept, matricule):
    from apps.absence.models import Etudiant
    return Etudiant.objects.create(
        matricule=matricule, nom='Nom%s' % matricule,
        departement=monde['depts'][dept])


def seance(monde, dept, jour='Lundi'):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept], semaine=monde['semaines'][(1, jour)],
        creneau_fk=monde['creneaux']['08h00-09h30'], em=monde['ems']['SEA11'],
        prof=monde['profs']['Moustapha'], salle=monde['salles']['101'],
        type_seance_fk=monde['cm'])


def case_de_patron(monde, dept):
    """Un patron NON VIDE : c'est ce qu'on ne doit pas rendre inatteignable."""
    from tests._edt_decor import grille
    from apps.edt.models import SeanceType
    g = grille(monde, dept)
    SeanceType.objects.create(
        grille=g, jour_fk=monde['jours']['Lundi'],
        creneau_fk=monde['creneaux']['08h00-09h30'], em=monde['ems']['SEA11'],
        prof=monde['profs']['Moustapha'], salle=monde['salles']['101'],
        type_seance_fk=monde['cm'])
    return g


def noms(reponse):
    corps = reponse.data
    lignes = corps['results'] if isinstance(corps, dict) else corps
    return sorted(l['nom'] for l in lignes)


def planifiables(user, **extra):
    params = {'avec_etudiants': 1, 'annee_universitaire': ANNEE}
    params.update(extra)
    return noms(api(user).get(URL, params))


class TestFiltre:

    def test_un_groupe_sans_etudiant_n_est_pas_propose(self, monde, gens):
        etudiant(monde, 'G1', '24001')
        resultat = planifiables(gens['admin'])
        assert 'G1' in resultat
        assert 'G2' not in resultat

    def test_un_groupe_garde_sa_place_des_le_premier_etudiant(self, monde, gens):
        etudiant(monde, 'G2', '24002')
        assert 'G2' in planifiables(gens['admin'])

    def test_un_groupe_vide_qui_porte_deja_des_seances_reste_visible(self, monde, gens):
        """Le cas mesuré en production : zéro étudiant, dix-huit séances. Masqué,
        il deviendrait inatteignable alors que ses séances alimentent le suivi."""
        seance(monde, 'G2')
        assert 'G2' in planifiables(gens['admin'])

    def test_un_groupe_vide_dont_le_patron_est_rempli_reste_visible(self, monde, gens):
        case_de_patron(monde, 'G2')
        assert 'G2' in planifiables(gens['admin'])

    def test_un_patron_VIDE_ne_suffit_pas_a_le_garder(self, monde, gens):
        """Une grille créée puis laissée vide ne prouve rien : six grilles sur
        onze sont dans ce cas."""
        from tests._edt_decor import grille
        grille(monde, 'G2')
        assert 'G2' not in planifiables(gens['admin'])

    def test_un_groupe_tres_rempli_n_apparait_qu_une_fois(self, monde, gens):
        """Le filtre joint trois tables d'un coup — étudiants, séances, cases de
        patron. Un groupe qui en porte plusieurs de chaque ne doit pas sortir
        en double.

        Ce test ne surveille PAS le `distinct=True` des `Count` : Django
        regroupe par clé primaire, et le retirer ne duplique aucune ligne — il
        gonfle seulement les valeurs comptées, qu'on ne compare ici qu'à zéro.
        Vérifié par sabotage : aucun test ne tombe quand on l'enlève."""
        etudiant(monde, 'G1', '24003')
        etudiant(monde, 'G1', '24004')
        seance(monde, 'G1')
        seance(monde, 'G1', jour='Mardi')
        case_de_patron(monde, 'G1')
        resultat = planifiables(gens['admin'])
        assert resultat.count('G1') == 1


class TestOptIn:

    def test_sans_le_parametre_la_liste_reste_entiere(self, monde, gens):
        """Le suivi, les vacations, les admissions et les paramètres lisent le
        même endpoint : ils ne doivent rien voir changer."""
        etudiant(monde, 'G1', '24005')
        complet = noms(api(gens['admin']).get(URL, {'annee_universitaire': ANNEE}))
        assert 'G2' in complet and 'HE' in complet and 'ST' in complet
        assert len(complet) > len(planifiables(gens['admin']))

    def test_le_filtre_se_combine_au_perimetre(self, monde, gens):
        """`edt_scope` borne aux groupes délégués ; `avec_etudiants` retire les
        vides. Les deux ensemble ne gardent que l'intersection."""
        etudiant(monde, 'G1', '24006')
        etudiant(monde, 'SEA L2 G1', '24007')     # hors périmètre du DE
        resultat = noms(api(gens['de']).get(URL, {
            'avec_etudiants': 1, 'edt_scope': 1, 'annee_universitaire': ANNEE}))
        assert resultat == ['G1']
