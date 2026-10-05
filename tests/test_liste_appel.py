"""
Qui figure sur la fiche d'appel d'une séance.

L'écran et le PDF prenaient le GROUPE entier. Mesuré sur `iss` le 02/10/2026 :

  * sur l'élément `ST41` du groupe #44, dix noms pour trois étudiants
    réellement inscrits — les sept autres l'avaient validé une année
    précédente, et une absence notée pour eux est fausse ;
  * quatorze inscriptions de l'année portaient sur un élément que le groupe de
    l'étudiant n'enseigne pas. Ces DETTES ne figuraient sur aucune fiche : ces
    étudiants ne pouvaient jamais être pointés.

La source juste est `InscriptionElement`. Mais elle n'est pas toujours saisie :
filtrer strictement rendrait alors une fiche VIDE, pire que trop de noms. D'où
le repli sur le groupe, et le champ `source` qui le DIT.
"""
import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL = '/api/v1/absences/presences/liste-appel/'


# ── Décor ────────────────────────────────────────────────────────────────────

def etudiant(monde, groupe, matricule):
    from apps.absence.models import Etudiant
    return Etudiant.objects.create(matricule=matricule, nom='Nom%s' % matricule,
                                   departement=monde['depts'][groupe])


def inscrire(monde, etu, em, annee=None, filiere='f_sea'):
    """Inscription administrative + pédagogique + à l'élément."""
    from apps.inscriptions.models import (InscriptionAdministrative,
                                          InscriptionElement,
                                          InscriptionPedagogique)
    from apps.parametres.models import Year
    annee = annee or ANNEE
    an, _ = Year.objects.get_or_create(annee=annee)
    ia, _ = InscriptionAdministrative.objects.get_or_create(
        etudiant=etu, annee_univ=an,
        defaults={'filiere': monde[filiere], 'niveau': 1,
                  'institution': monde['inst'],
                  # Unique en base : sans valeur distincte, deux étudiants
                  # inscrits la même année s'y heurtent.
                  'numero_inscription': 'INS-%s-%s' % (annee, etu.matricule)})
    ip, _ = InscriptionPedagogique.objects.get_or_create(
        inscription_admin=ia, semestre=monde['s1'])
    return InscriptionElement.objects.create(inscription_ped=ip, em=monde['ems'][em])


def seance(monde, groupe, em, jour='Lundi'):
    """Une séance : c'est elle qui dit quel groupe ENSEIGNE l'élément."""
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][groupe], semaine=monde['semaines'][(1, jour)],
        creneau_fk=monde['creneaux']['08h00-09h30'], em=monde['ems'][em],
        prof=monde['profs']['Moustapha'], salle=monde['salles']['101'],
        type_seance_fk=monde['cm'])


def appel(monde, groupe, em):
    from apps.absence.liste_appel import liste_appel
    return liste_appel(monde['depts'][groupe].id, monde['ems'][em].id, ANNEE)


def matricules(liste):
    return sorted(e.matricule for e in liste)


# ── La règle ─────────────────────────────────────────────────────────────────

class TestInscrits:

    def test_seuls_les_inscrits_a_l_element_figurent(self, monde):
        """Le cas de production : trois inscrits sur dix présents au groupe."""
        suit = etudiant(monde, 'G1', '001')
        ne_suit_pas = etudiant(monde, 'G1', '002')          # a déjà validé, ailleurs
        inscrire(monde, suit, 'SEA11')

        r = appel(monde, 'G1', 'SEA11')
        assert matricules(r['etudiants']) == ['001']
        assert r['source'] == 'inscriptions'
        assert ne_suit_pas.matricule not in matricules(r['etudiants'])

    def test_une_inscription_a_un_AUTRE_element_ne_compte_pas(self, monde):
        e = etudiant(monde, 'G1', '003')
        inscrire(monde, e, 'SEA12')
        assert appel(monde, 'G1', 'SEA11')['source'] == 'groupe'

    def test_une_inscription_d_une_AUTRE_annee_ne_compte_pas(self, monde):
        from tests._edt_decor import ANNEE_SUIVANTE
        e = etudiant(monde, 'G1', '004')
        inscrire(monde, e, 'SEA11', annee=ANNEE_SUIVANTE)
        assert appel(monde, 'G1', 'SEA11')['source'] == 'groupe'


class TestDettes:

    def test_un_inscrit_d_un_autre_groupe_est_rattache_a_la_fiche(self, monde):
        """Sans cela, il ne figure sur AUCUNE fiche et n'est jamais pointé."""
        local = etudiant(monde, 'G1', '010')
        ailleurs = etudiant(monde, 'SDID L2', '011')
        inscrire(monde, local, 'SEA11')
        inscrire(monde, ailleurs, 'SEA11')
        seance(monde, 'G1', 'SEA11')                 # G1 enseigne l'élément

        r = appel(monde, 'G1', 'SEA11')
        assert matricules(r['etudiants']) == ['010']
        assert matricules(r['dettes']) == ['011']

    def test_un_inscrit_dont_le_groupe_ENSEIGNE_l_element_n_est_pas_une_dette(self, monde):
        """Il a sa propre fiche : l'ajouter ici le ferait pointer deux fois."""
        local = etudiant(monde, 'G1', '020')
        voisin = etudiant(monde, 'G2', '021')
        inscrire(monde, local, 'SEA11')
        inscrire(monde, voisin, 'SEA11')
        seance(monde, 'G1', 'SEA11')
        seance(monde, 'G2', 'SEA11')                 # G2 l'enseigne aussi

        assert appel(monde, 'G1', 'SEA11')['dettes'] == []

    def test_sans_inscription_dans_le_groupe_aucune_dette_n_est_rattachee(self, monde):
        """La fiche est déjà une liste non vérifiée : y ajouter des noms venus
        d'ailleurs la rendrait incompréhensible."""
        etudiant(monde, 'G1', '030')
        ailleurs = etudiant(monde, 'SDID L2', '031')
        inscrire(monde, ailleurs, 'SEA11')

        r = appel(monde, 'G1', 'SEA11')
        assert r['source'] == 'groupe'
        assert r['dettes'] == []


class TestRepli:

    def test_sans_aucune_inscription_on_garde_le_groupe_entier(self, monde):
        """Une fiche vide serait pire que trop de noms."""
        etudiant(monde, 'G1', '040')
        etudiant(monde, 'G1', '041')
        r = appel(monde, 'G1', 'SEA11')
        assert matricules(r['etudiants']) == ['040', '041']
        assert r['source'] == 'groupe'

    def test_une_seance_sans_element_garde_le_groupe(self, monde):
        """Sport, instruction militaire : aucune inscription pédagogique à lire."""
        from apps.absence.liste_appel import liste_appel
        etudiant(monde, 'G1', '050')
        r = liste_appel(monde['depts']['G1'].id, None, ANNEE)
        assert matricules(r['etudiants']) == ['050']
        assert r['source'] == 'groupe'


# ── L'adresse, celle que l'écran consomme ────────────────────────────────────

class TestAdresse:

    def test_l_ecran_et_le_pdf_lisent_la_meme_regle(self, monde, gens):
        suit = etudiant(monde, 'G1', '060')
        etudiant(monde, 'G1', '061')
        ailleurs = etudiant(monde, 'SDID L2', '062')
        inscrire(monde, suit, 'SEA11')
        inscrire(monde, ailleurs, 'SEA11')
        seance(monde, 'G1', 'SEA11')

        r = api(gens['admin']).get(URL, {
            'departement': monde['depts']['G1'].id, 'em': monde['ems']['SEA11'].id,
            'annee_universitaire': ANNEE})
        assert r.status_code == 200
        assert [e['matricule'] for e in r.data['etudiants']] == ['060']
        assert [e['matricule'] for e in r.data['dettes']] == ['062']
        assert r.data['dettes'][0]['groupe'] == 'SDID L2'
        assert r.data['liste_non_verifiee'] is False

    def test_la_liste_non_verifiee_est_annoncee(self, monde, gens):
        etudiant(monde, 'G1', '070')
        r = api(gens['admin']).get(URL, {
            'departement': monde['depts']['G1'].id, 'em': monde['ems']['SEA11'].id,
            'annee_universitaire': ANNEE})
        assert r.data['liste_non_verifiee'] is True
        assert r.data['source'] == 'groupe'

    def test_les_parametres_obligatoires_sont_exiges(self, monde, gens):
        assert api(gens['admin']).get(URL, {'em': 1}).status_code == 400

    def test_sans_connexion_c_est_refuse(self, monde):
        from rest_framework.test import APIClient
        assert APIClient().get(URL, {'departement': 1, 'annee_universitaire': ANNEE}
                               ).status_code in (401, 403)


# ── Le gabarit du PDF n'imprime pas ses commentaires ─────────────────────────

class TestGabarit:
    """Un `{# #}` sur plusieurs lignes n'est PAS un commentaire pour Django : il
    s'imprime tel quel. Le texte expliquant les dettes est sorti dans le PDF
    le 02/10/2026 — et celui de la liste non vérifiée sur CHAQUE fiche. Le dépôt
    avait déjà connu ce défaut sur le PDF de l'emploi du temps."""

    def test_aucun_commentaire_ne_sort_dans_le_document(self, db):
        from django.template.loader import render_to_string
        fiche = {
            'dep_nom': 'G1', 'groupe_libelle': 'L1 G1', 'niveau': 'L1',
            'filiere': 'Statistique', 'date_seance': None, 'jour': 'Lundi',
            'creneau': '08h00 à 09h30', 'type_seance': 'CM',
            'is_surveillance': False, 'numero_semaine': 1, 'em_code': 'ST41',
            'em_intitule': 'Élément', 'prof_nom': 'Prof', 'salle_nom': '101',
            # Les deux branches qui portaient un commentaire multiligne :
            'etudiants': [{'matricule': '001', 'nom': 'Un', 'genre': 'M'}],
            'dettes': [{'matricule': '002', 'nom': 'Deux', 'genre': 'F', 'groupe': 'G2'}],
            'rattaches': [{'matricule': '003', 'nom': 'Trois', 'genre': 'F', 'filiere': 'SEA'}],
            'liste_non_verifiee': True,
        }
        from apps.absence.liste_appel import lignes_de_fiche
        fiche['lignes'] = lignes_de_fiche(fiche['etudiants'], fiche['rattaches'], fiche['dettes'])
        # Le même contexte d'institution que la vue (logo, noms) : sans lui, le
        # gabarit échoue avant d'avoir rendu la moindre fiche.
        from core.pdf_utils import get_institution_context
        html = render_to_string('absence/fiches_presence.html', {
            'fiches': [fiche], 'annee_universitaire': '2026-2027', 'numero_semaine': 1,
            **get_institution_context()})

        assert '{#' not in html and '#}' not in html
        assert 'DETTES' not in html
        assert 'camarade de promotion' not in html
        assert 'Inscriptions pédagogiques non saisies' not in html
        # Et ce qui DOIT s'imprimer s'imprime toujours.
        assert 'dette · G2' in html
        assert 'Trois' in html and 'rattaché·e · inscrit·e en SEA' in html
        assert 'Liste du groupe entier' in html
        assert 'L1 G1' in html


# ── Les rattachés : placés dans le groupe, inscrits ailleurs ──────────────────

def compter_inscriptions():
    from apps.inscriptions.models import (InscriptionAdministrative,
                                          InscriptionElement,
                                          InscriptionPedagogique)
    return (InscriptionAdministrative.objects.count(),
            InscriptionPedagogique.objects.count(),
            InscriptionElement.objects.count())


class TestRattaches:
    """Le cas du 02/10/2026 : trois étudiants inscrits en SEA placés dans le
    groupe LPSEA L3 G2 pour la planification, sans toucher leur inscription.
    Ici, le groupe « SDID L2 » (filière SDID) joue ce rôle."""

    def test_un_inscrit_d_une_autre_filiere_figure_comme_rattache(self, monde):
        suit = etudiant(monde, 'SDID L2', '100')
        place = etudiant(monde, 'SDID L2', '101')          # inscrit en SEA
        inscrire(monde, suit, 'SDID31', filiere='f_sdid')
        inscrire(monde, place, 'SEA31', filiere='f_sea')

        r = appel(monde, 'SDID L2', 'SDID31')
        assert r['source'] == 'inscriptions'
        assert matricules(r['etudiants']) == ['100']
        assert matricules(r['rattaches']) == ['101']
        assert r['rattaches'][0].filiere_inscription == 'SEA'

    def test_un_membre_de_la_filiere_non_inscrit_reste_exclu(self, monde):
        """Le cas ST41 : inscrit dans la filière du groupe mais pas à
        l'élément, il l'a validé. Il ne revient pas par la porte des rattachés."""
        suit = etudiant(monde, 'SDID L2', '110')
        a_valide = etudiant(monde, 'SDID L2', '111')
        inscrire(monde, suit, 'SDID31', filiere='f_sdid')
        inscrire(monde, a_valide, 'SEA31', filiere='f_sdid')   # autre élément, MÊME filière

        r = appel(monde, 'SDID L2', 'SDID31')
        assert matricules(r['etudiants']) == ['110']
        assert r['rattaches'] == []

    def test_sans_inscription_de_l_annee_personne_n_est_rattache(self, monde):
        from tests._edt_decor import ANNEE_SUIVANTE
        suit = etudiant(monde, 'SDID L2', '120')
        inscrire(monde, suit, 'SDID31', filiere='f_sdid')
        etudiant(monde, 'SDID L2', '121')                      # aucune inscription
        ailleurs_l_an_prochain = etudiant(monde, 'SDID L2', '122')
        inscrire(monde, ailleurs_l_an_prochain, 'SEA31', annee=ANNEE_SUIVANTE)

        assert appel(monde, 'SDID L2', 'SDID31')['rattaches'] == []

    def test_un_rattache_inscrit_a_l_element_n_est_liste_qu_une_fois(self, monde):
        place = etudiant(monde, 'SDID L2', '130')
        inscrire(monde, place, 'SDID31', filiere='f_sea')     # inscrit ailleurs, MAIS à l'élément
        r = appel(monde, 'SDID L2', 'SDID31')
        assert matricules(r['etudiants']) == ['130']
        assert r['rattaches'] == []

    def test_un_groupe_sans_filiere_n_a_pas_de_rattache(self, monde):
        """HE n'a pas de filière à comparer."""
        suit = etudiant(monde, 'HE', '140')
        autre = etudiant(monde, 'HE', '141')
        inscrire(monde, suit, 'HE11')
        inscrire(monde, autre, 'SEA31', filiere='f_sdid')
        assert appel(monde, 'HE', 'HE11')['rattaches'] == []

    def test_liste_du_groupe_entier_le_rattache_y_est_deja(self, monde):
        """Sans inscription saisie pour l'élément, tout le groupe est listé :
        le rattaché y figure, une seule fois, sans catégorie à part."""
        place = etudiant(monde, 'SDID L2', '150')
        inscrire(monde, place, 'SEA31', filiere='f_sea')
        r = appel(monde, 'SDID L2', 'SDID31')
        assert r['source'] == 'groupe'
        assert matricules(r['etudiants']) == ['150']
        assert r['rattaches'] == []

    def test_la_regle_n_ecrit_aucune_inscription(self, monde):
        """La demande : les voir sur la fiche SANS toucher leur inscription."""
        suit = etudiant(monde, 'SDID L2', '160')
        place = etudiant(monde, 'SDID L2', '161')
        inscrire(monde, suit, 'SDID31', filiere='f_sdid')
        inscrire(monde, place, 'SEA31', filiere='f_sea')
        avant = compter_inscriptions()
        appel(monde, 'SDID L2', 'SDID31')
        assert compter_inscriptions() == avant

    def test_l_adresse_rend_les_rattaches_avec_leur_filiere(self, monde, gens):
        suit = etudiant(monde, 'SDID L2', '170')
        place = etudiant(monde, 'SDID L2', '171')
        inscrire(monde, suit, 'SDID31', filiere='f_sdid')
        inscrire(monde, place, 'SEA31', filiere='f_sea')
        r = api(gens['admin']).get(URL, {
            'departement': monde['depts']['SDID L2'].id, 'em': monde['ems']['SDID31'].id,
            'annee_universitaire': ANNEE})
        assert r.status_code == 200
        assert [e['matricule'] for e in r.data['etudiants']] == ['170']
        assert [(e['matricule'], e['filiere']) for e in r.data['rattaches']] == [('171', 'SEA')]
