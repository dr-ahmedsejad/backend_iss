"""
Lecture de la structure des groupes — la pièce propre à l'ISS.

À l'ESP, le sous-groupe est déclaré dans `Departement.groupe`. À l'ISS ce champ
est vide sur les vingt-six groupes planifiables : le sous-groupe n'est écrit
que dans le nom. Tout le portage de la règle « mêmes étudiants » repose sur ce
module, et une erreur d'analyse ici se traduirait soit par des refus
injustifiés en production, soit par des collisions laissées passer.

Les noms testés sont ceux de la base `iss`, relevés le 02/09/2026.
"""
import pytest

from apps.edt.groupes import est_transversal, sous_groupe, souche


class FauxDepartement:
    """Le strict nécessaire : ces fonctions ne lisent que trois champs."""

    class _Niveau:
        def __init__(self, libelle):
            self.niveau = libelle

    def __init__(self, nom, groupe='', niveau=None, is_container=False):
        self.nom          = nom
        self.groupe       = groupe
        self.niveau       = self._Niveau(niveau) if niveau else None
        self.is_container = is_container


# ── Le sous-groupe ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('nom, attendu', [
    # Les noms réellement présents en base.
    ('G1',            'G1'),
    ('G2',            'G2'),
    ('SEA L2 - G1',   'G1'),
    ('SEA L2 - G2',   'G2'),
    ('SDID L2 G1',    'G1'),
    ('SDID L2 G2',    'G2'),
    ('SDID L2',       ''),
    ('SEA L3',        ''),
    ('HE',            ''),
    ('ST',            ''),
    ('SDID',          ''),
    ('SEA',           ''),
    ('Statistique',   ''),
    ('STAGES',        ''),
    # Les pièges : ces noms finissent par un chiffre sans être des
    # sous-groupes. Les prendre pour tels aurait scindé des groupes qui
    # n'existent qu'en un exemplaire.
    ('STATL1',        ''),
    ('LPSEA L2',      ''),
    ('SEA L2',        ''),
    ('STAT L1',       ''),
    # Écritures plausibles qu'on veut malgré tout reconnaître.
    ('SEA L2  G3',    'G3'),
    ('SEA L2–G4',     'G4'),
    ('SEA L2_G5',     'G5'),
    ('sea l2 - g6',   'G6'),
    ('SEA L2 G 7',    'G7'),
    # G doit ouvrir le mot : collé à la fin d'un sigle, ce n'est pas un
    # sous-groupe.
    ('SEAG5',         ''),
])
def test_le_sous_groupe_se_lit_sur_le_nom(nom, attendu):
    assert sous_groupe(FauxDepartement(nom)) == attendu


def test_le_champ_declare_l_emporte_sur_le_nom():
    """Le jour où `Departement.groupe` sera rempli, il fera foi."""
    d = FauxDepartement('SEA L2 - G1', groupe='G9')
    assert sous_groupe(d) == 'G9'
    # Et le nom redevient la souche entière : le champ dit déjà le sous-groupe.
    assert souche(d) == 'sea l2 - g1'


def test_departement_absent():
    assert sous_groupe(None) == ''
    assert souche(None) == ''
    assert est_transversal(None) is False


# ── La souche ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('nom, attendu', [
    ('SEA L2 - G1', 'sea l2'),
    ('SEA L2 - G2', 'sea l2'),
    ('SDID L2 G1',  'sdid l2'),
    ('SDID L2',     'sdid l2'),
    ('G1',          ''),
    ('G2',          ''),
    ('HE',          'he'),
])
def test_la_souche_retire_le_sous_groupe(nom, attendu):
    assert souche(FauxDepartement(nom)) == attendu


def test_un_groupe_entier_et_ses_sous_groupes_partagent_la_souche():
    """C'est ce rapprochement qui fait toute l'utilité de la souche."""
    entier = FauxDepartement('SDID L2')
    part   = FauxDepartement('SDID L2 G1')
    assert souche(entier) == souche(part)


def test_deux_familles_differentes_ne_partagent_pas_la_souche():
    assert souche(FauxDepartement('SEA L2 - G1')) \
        != souche(FauxDepartement('SDID L2 G1'))


def test_g1_et_g2_nus_ont_la_meme_souche_vide():
    """Leur souche ne les distingue pas — c'est le sous-groupe qui le fait.

    D'où l'ordre des règles dans `_memes_etudiants` : la filière est consultée
    AVANT le nom, faute de quoi deux « G1 » de filières différentes seraient
    tenus pour le même public.
    """
    assert souche(FauxDepartement('G1')) == souche(FauxDepartement('G2')) == ''
    assert sous_groupe(FauxDepartement('G1')) \
        != sous_groupe(FauxDepartement('G2'))


# ── Le groupe transversal ───────────────────────────────────────────────────

def test_le_niveau_transversal_designe_un_enseignement_de_promotion():
    assert est_transversal(FauxDepartement('HE', niveau='Transversal')) is True
    assert est_transversal(FauxDepartement('ST', niveau='Transversal')) is True


def test_le_libelle_est_lu_sans_egard_a_la_casse_ni_aux_espaces():
    assert est_transversal(FauxDepartement('HE', niveau=' transversal ')) is True


@pytest.mark.parametrize('nom, niveau', [
    ('SEA L2 - G1', 'L2'),
    ('G1',          'L1'),
    ('SDID',        'L3'),
])
def test_un_groupe_ordinaire_n_est_pas_transversal(nom, niveau):
    assert est_transversal(FauxDepartement(nom, niveau=niveau)) is False


def test_l_absence_de_filiere_ne_suffit_pas_a_faire_un_transversal():
    """La différence avec l'ESP.

    À l'ESP, un groupe de promotion se reconnaissait à l'absence de filière. À
    l'ISS, treize groupes planifiables ont `filiere IS NULL` par simple
    héritage — « SEA L3 », « SDID L2 » — sans rien avoir de transversal. Les
    confondre aurait fait croiser tout le monde avec tout le monde.
    """
    assert est_transversal(FauxDepartement('SEA L3', niveau='L3')) is False


def test_un_conteneur_d_inscription_n_est_jamais_transversal():
    """Un conteneur ne se planifie pas : il ne dispute aucun créneau."""
    assert est_transversal(
        FauxDepartement('STAT L1', niveau='Transversal',
                        is_container=True)) is False


def test_sans_niveau_on_ne_conclut_rien():
    assert est_transversal(FauxDepartement('HE')) is False
