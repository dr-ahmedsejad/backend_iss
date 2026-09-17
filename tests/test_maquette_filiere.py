"""
La maquette d'une filière — contre la fonction qui assemble les données, pas
contre le PDF : wkhtmltopdf n'a rien à prouver ici, les règles si.

Le décor reproduit la structure mesurée sur `iss` : une mère tronc commun L1
(LPSTAT) qui porte AUSSI des modules hors de son parcours, et une fille L2-L3
(SEA).
"""
from decimal import Decimal

import pytest

from tests.factories.parametres import NiveauFactory, SemestreFactory
from tests.factories.scolarite import FiliereFactory


def _semestre(numero):
    niveau = NiveauFactory(niveau='L%d' % ((numero + 1) // 2))
    return SemestreFactory(code_semestre='S%d' % numero,
                           semestre='Semestre %d' % numero,
                           type_semestre='I' if numero % 2 else 'P',
                           niveau_semestre=niveau, credits=30)


def _module(filiere, numero, code, elements, actif=True):
    """Un module et ses éléments : [(code, credits, coeff, cm, td, tp), …]."""
    from apps.em.models import EM
    from apps.modules.models import Module
    m = Module.objects.create(
        code=code, intitule_fr='Module ' + code, semestre=_semestre(numero),
        filiere=filiere, credits=sum(e[1] for e in elements),
        coefficient=Decimal(sum(e[2] for e in elements)), actif=actif)
    for c, cr, co, cm, td, tp in elements:
        EM.objects.create(code_em=c, intitule='Élément ' + c, filiere=filiere,
                          module_lmd=m, credits=cr, coefficient=co,
                          CM=cm, TD=td, TP=tp)
    return m


def _semestre_complet(filiere, numero, prefixe, tp=0):
    """Deux modules qui font la norme : 30 crédits, 20 de coefficient."""
    _module(filiere, numero, '%s-A' % prefixe,
            [('%sA1' % prefixe, 10, 6, 20, 10, tp), ('%sA2' % prefixe, 5, 4, 10, 10, 0)])
    _module(filiere, numero, '%s-B' % prefixe,
            [('%sB1' % prefixe, 15, 10, 30, 15, 0)])


@pytest.fixture
def familles(db):
    mere = FiliereFactory(code='TC', intitule_fr='Tronc commun',
                          niveau_debut=1, niveau_fin=1, nb_semestres=2)
    fille = FiliereFactory(code='FIL', intitule_fr='Fille',
                           niveau_debut=2, niveau_fin=3, nb_semestres=4,
                           filiere_parent=mere)
    for n in (1, 2):
        _semestre_complet(mere, n, 'TC%d' % n)
    for n in (3, 4, 5, 6):
        _semestre_complet(fille, n, 'FIL%d' % n, tp=12 if n == 3 else 0)
    return mere, fille


def maquette(filiere):
    from apps.scolarite.maquette import assembler_maquette
    return assembler_maquette(filiere)


def codes(m):
    return [s['code'] for s in m['semestres']]


# ── 1. Le parcours de l'étudiant ────────────────────────────────────────────

class TestParcours:

    def test_une_fille_commence_par_les_semestres_de_sa_mere_marques(self, familles):
        _, fille = familles
        m = maquette(fille)
        assert codes(m) == ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']
        assert [s['tronc_commun'] for s in m['semestres']] == \
               ['TC', 'TC', None, None, None, None]
        # C'est ce qui fait une licence : sans la mère, 120.
        assert m['total_credits'] == 180

    def test_la_maquette_d_une_mere_ne_contient_pas_ses_filles(self, familles):
        mere, _ = familles
        m = maquette(mere)
        assert codes(m) == ['S1', 'S2']
        assert all(s['tronc_commun'] is None for s in m['semestres'])
        modules = {mod['code'] for s in m['semestres'] for mod in s['modules']}
        assert not any(c.startswith('FIL') for c in modules)

    def test_la_mere_ne_fournit_que_les_niveaux_qui_precedent_sa_fille(self, familles):
        """Le cas mesuré sur `iss` : LPSTAT, tronc commun L1, porte aussi des
        copies des modules de SEA en S4-S6. Les reprendre donnerait à la fille
        deux S4."""
        mere, fille = familles
        _semestre_complet(mere, 4, 'COPIE')
        m = maquette(fille)
        assert codes(m) == ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']
        s4 = next(s for s in m['semestres'] if s['code'] == 'S4')
        assert s4['tronc_commun'] is None
        assert not any(mod['code'].startswith('COPIE') for mod in s4['modules'])

    def test_une_mere_au_parcours_large_s_arrete_ou_commence_sa_fille(self, familles):
        """Le test précédent ne suffit pas : ses copies sont hors du parcours
        DÉCLARÉ de la mère (L1), qui les écarte à lui seul. Ici la mère se
        déclare L1-L3 — une licence complète qui a aussi une fille. C'est la
        coupe au début de la fille, et elle seule, qui évite deux S3."""
        mere, fille = familles
        mere.niveau_fin = 3
        mere.save()
        _semestre_complet(mere, 3, 'MERE3')
        m = maquette(fille)
        assert codes(m) == ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']
        s3 = next(s for s in m['semestres'] if s['code'] == 'S3')
        assert s3['tronc_commun'] is None
        assert {mod['code'] for mod in s3['modules']} == {'FIL3-A', 'FIL3-B'}

    def test_un_module_hors_parcours_est_ecarte_et_nomme(self, familles):
        mere, _ = familles
        _semestre_complet(mere, 5, 'HORS')
        m = maquette(mere)
        assert codes(m) == ['S1', 'S2']
        assert m['hors_parcours'] == ['HORS-A', 'HORS-B']

    def test_un_cycle_de_meres_ne_boucle_pas(self, familles):
        mere, fille = familles
        mere.filiere_parent = fille
        mere.save()
        from apps.scolarite.maquette import chaine_des_meres
        assert [f.code for f in chaine_des_meres(fille)] == ['TC', 'FIL']

    def test_les_semestres_sont_tries_sur_le_numero_pas_sur_la_chaine(self, db):
        f = FiliereFactory(code='LONG', niveau_debut=1, niveau_fin=5)
        for n in (10, 2, 1):
            _semestre_complet(f, n, 'L%d' % n)
        assert codes(maquette(f)) == ['S1', 'S2', 'S10']


# ── 2. Les totaux ───────────────────────────────────────────────────────────

class TestTotaux:

    def test_les_totaux_sont_recalcules_depuis_les_elements(self, familles):
        """Le coefficient DÉCLARÉ du module est faux exprès : le total ne doit
        pas le recopier."""
        from apps.modules.models import Module
        mere, _ = familles
        Module.objects.filter(code='TC1-A').update(credits=99, coefficient=Decimal('99'))
        s1 = maquette(mere)['semestres'][0]
        assert s1['totaux']['credits'] == 30
        assert s1['totaux']['coefficient'] == 20
        assert s1['totaux']['cm'] == 60 and s1['totaux']['td'] == 35
        assert s1['totaux']['total'] == 95
        assert s1['ecarts'] == []

    def test_un_semestre_hors_norme_le_dit(self, familles):
        from apps.em.models import EM
        mere, _ = familles
        EM.objects.filter(code_em='TC1B1').update(credits=13, coefficient=9)
        s1 = maquette(mere)['semestres'][0]
        assert s1['ecarts'] == [
            'Ce semestre totalise 28 crédits au lieu de 30.',
            'Ce semestre totalise 19 de coefficient au lieu de 20.',
        ]


# ── 3. La colonne TP ────────────────────────────────────────────────────────

class TestColonneTP:

    def test_la_colonne_tp_apparait_si_un_element_en_a(self, familles):
        _, fille = familles
        assert maquette(fille)['avec_tp'] is True

    def test_pas_de_colonne_tp_sans_tp(self, familles):
        mere, _ = familles
        assert maquette(mere)['avec_tp'] is False


# ── 4-5. Inactifs et filière vide ───────────────────────────────────────────

class TestContenu:

    def test_un_module_inactif_n_apparait_pas(self, familles):
        mere, _ = familles
        _module(mere, 1, 'ETEINT', [('ET1', 3, 2, 10, 0, 0)], actif=False)
        modules = {mod['code'] for s in maquette(mere)['semestres'] for mod in s['modules']}
        assert 'ETEINT' not in modules
        assert 'TC1-A' in modules

    def test_une_filiere_vide_n_a_aucun_semestre(self, db):
        vide = FiliereFactory(code='VIDE')
        m = maquette(vide)
        assert m['semestres'] == [] and m['total_credits'] == 0

    def test_le_libelle_de_niveau_suit_le_type_de_diplome(self, familles):
        mere, _ = familles
        s1 = maquette(mere)['semestres'][0]
        assert (s1['libelle_niveau'], s1['libelle_semestre']) == ('Licence 1', 'Semestre 1')


# ── L'adresse ───────────────────────────────────────────────────────────────

class TestAdresse:

    URL = '/api/v1/scolarite/filieres/%s/maquette/'

    def test_sans_connexion_la_maquette_est_refusee(self, familles):
        """`list` et `retrieve` sont publics ; la maquette ne doit pas en
        hériter."""
        from rest_framework.test import APIClient
        mere, _ = familles
        assert APIClient().get(self.URL % mere.pk).status_code == 401

    def test_la_liste_reste_publique(self, familles):
        from rest_framework.test import APIClient
        assert APIClient().get('/api/v1/scolarite/filieres/').status_code == 200

    def test_une_filiere_sans_module_repond_400_avec_son_motif(self, db):
        from rest_framework.test import APIClient
        from tests.factories.auth import UserFactory
        vide = FiliereFactory(code='VIDE')
        c = APIClient()
        c.force_authenticate(UserFactory(username='u_adm', role='admin', is_superuser=True))
        r = c.get(self.URL % vide.pk)
        assert r.status_code == 400
        assert r.data['detail'] == ("La filière VIDE n'a encore aucun module : il "
                                    "n'y a pas de maquette à télécharger.")
