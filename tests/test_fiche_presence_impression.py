"""
La fiche de présence s'imprime en noir et blanc, puis se photocopie.

Constaté le 05/10/2026 : une imprimante N&B rend un gris par une trame de
points, une photocopieuse l'efface. La grille gris clair (#bbb) sortait en
pointillés puis disparaissait — l'enseignant ne voyait plus sur quelle ligne
porter le « A » — et les fonds gris (#fafafa, #ececec) semaient des points sur
les noms. En plus, wkhtmltopdf réduisait la page à 80 % : les noms sortaient à
7 points, les mentions à 5,4.
"""
import io
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string

GABARIT = Path(settings.BASE_DIR) / 'templates' / 'absence' / 'fiches_presence.html'


class TestNoirSurBlanc:

    def test_aucune_couleur_autre_que_le_noir(self):
        """Ni dans la feuille de style, ni en ligne. Le blanc n'a pas à être
        écrit : c'est le papier."""
        source = GABARIT.read_text(encoding='utf-8')
        couleurs = {c.lower() for c in re.findall(r'#[0-9a-fA-F]{3,6}\b', source)}
        assert couleurs <= {'#000', '#000000'}, couleurs
        assert not re.search(r'\brgba?\(|\bgr[ae]y\b|silver|opacity', source, re.I)

    def test_aucun_fond(self):
        source = GABARIT.read_text(encoding='utf-8')
        assert 'background' not in source

    def test_la_grille_est_en_traits_pleins(self):
        source = GABARIT.read_text(encoding='utf-8')
        traits = re.findall(r'border(?:-(?:top|bottom|left|right))?\s*:\s*([^;]+);', source)
        for t in traits:
            if t.strip() == 'none':
                continue
            assert 'solid' in t and '#000' in t, t
            epaisseur = float(re.match(r'([\d.]+)pt', t.strip()).group(1))
            assert epaisseur >= 0.75, t


class TestPolice:
    """Calibri, demandée le 05/10/2026. Elle n'existe pas sur le serveur Linux :
    sans Carlito, wkhtmltopdf y prendrait une police de repli quelconque."""

    def test_calibri_puis_carlito_partout(self):
        """Partout, sauf le nom ARABE de l'en-tête : Carlito n'a pas l'arabe,
        il reste en Arial, comme avant."""
        source = GABARIT.read_text(encoding='utf-8')
        regles = re.findall(r'([^{}]+)\{[^{}]*font-family\s*:\s*([^;]+);', source)
        assert regles
        for selecteur, police in regles:
            if selecteur.strip().endswith('.entete-wrapper td:last-child'):
                assert police.strip().startswith('Arial'), police
            else:
                assert police.strip().startswith('Calibri, Carlito'), (selecteur, police)

    def test_l_image_du_serveur_installe_carlito(self):
        dockerfile = (Path(settings.BASE_DIR) / 'Dockerfile').read_text(encoding='utf-8')
        assert 'fonts-crosextra-carlito' in dockerfile


def fiche(n):
    from apps.absence.liste_appel import lignes_de_fiche
    f = {
        'dep_nom': 'G1', 'groupe_libelle': 'L1 G1', 'filiere': 'Statistique',
        'date_seance': None, 'creneau': '08h00 à 09h30', 'type_seance': 'CM',
        'is_surveillance': False, 'em_intitule': 'Élément', 'prof_nom': 'Prof',
        'etudiants': [{'matricule': '%03d' % i, 'nom': 'Etu %d' % i} for i in range(n - 1)],
        'rattaches': [],
        'dettes': [{'matricule': '999', 'nom': 'Dette', 'groupe': 'G2'}],
        'liste_non_verifiee': False,
    }
    f['lignes'] = lignes_de_fiche(f['etudiants'], f['rattaches'], f['dettes'])
    from apps.absence.liste_appel import colonnes_de_fiche
    f['paires'] = colonnes_de_fiche(f['lignes'])
    return f


def rendre(*fiches):
    from core.pdf_utils import get_institution_context
    return render_to_string('absence/fiches_presence.html', {
        'fiches': list(fiches), 'annee_universitaire': '2026-2027', 'numero_semaine': 1,
        **get_institution_context()})


class TestPlace:
    """Sans réduction automatique, c'est le gabarit qui fait tenir un groupe
    chargé sur une page : lignes plus basses au-delà de 24, puis de 30, puis
    deux colonnes au-delà de 32. Mesuré en Calibri 11 pt : 35 lignes tiennent
    sur une colonne."""

    @pytest.mark.parametrize('n, classe', [
        (20, 'etu-table"'), (24, 'etu-table"'),
        (25, 'etu-table serree"'), (30, 'etu-table serree"'),
        (31, 'etu-table tres-serree"'),
    ])
    def test_la_densite_suit_le_nombre_de_lignes(self, db, n, classe):
        assert 'class="%s' % classe in rendre(fiche(n))

    def test_chaque_fiche_a_sa_propre_densite(self, db):
        html = rendre(fiche(20), fiche(31))
        assert html.count('class="etu-table"') == 1
        assert html.count('class="etu-table tres-serree"') == 1


class TestDeuxColonnes:
    """Au-delà de 32 — un CM réunit 40 à 47 étudiants — une colonne ne tenait
    sur une page qu'avec des lignes trop basses. Deux colonnes, lues de haut
    en bas : la gauche, puis la droite. Mesuré : 64 lignes tiennent sur une page."""

    def test_sous_le_seuil_une_seule_colonne(self):
        from apps.absence.liste_appel import colonnes_de_fiche
        assert colonnes_de_fiche([{'matricule': str(i)} for i in range(32)]) is None

    def test_au_dela_la_gauche_puis_la_droite(self):
        from apps.absence.liste_appel import colonnes_de_fiche
        paires = colonnes_de_fiche([{'matricule': str(i)} for i in range(37)])
        assert len(paires) == 19
        assert [g['matricule'] for g, _ in paires] == [str(i) for i in range(19)]
        assert [d['matricule'] for _, d in paires[:-1]] == [str(i) for i in range(19, 37)]
        assert paires[-1][1] is None

    def test_le_pdf_imprime_deux_colonnes_dans_l_ordre(self, db):
        html = rendre(fiche(42))
        assert 'class="etu-table deux-colonnes"' in html
        lus = re.findall(r'class="mat-code">(\w+)<', html)
        # Rangée par rangée : gauche, droite. Reconstitué colonne par colonne,
        # c'est l'ordre croissant.
        assert lus[0::2] + lus[1::2] == [l['matricule'] for l in fiche(42)['lignes']]

    def test_une_fiche_courte_reste_sur_une_colonne(self, db):
        assert 'deux-colonnes"' not in rendre(fiche(32))
        assert 'deux-colonnes"' in rendre(fiche(33))


class TestOrdreDesMatricules:
    """Le 05/10/2026, la fiche de SEA L3 G1 lisait « …255045, 24603, 24616… » :
    rattachés et dettes étaient rejetés en fin de liste. On fait l'appel dans
    l'ordre des matricules : une seule liste, croissante."""

    def test_en_nombre_et_non_en_lettres(self):
        from apps.absence.liste_appel import ordre_matricule
        assert sorted(['10000', '9999', '255004', '24603'], key=ordre_matricule) ==             ['9999', '10000', '24603', '255004']

    def test_un_matricule_non_numerique_passe_apres(self):
        from apps.absence.liste_appel import ordre_matricule
        assert sorted(['B12', '24603', 'A7', ''], key=ordre_matricule) == ['24603', '', 'A7', 'B12']

    def test_rattaches_et_dettes_prennent_leur_place(self):
        from apps.absence.liste_appel import lignes_de_fiche
        lignes = lignes_de_fiche(
            [{'matricule': '24622', 'nom': 'A'}, {'matricule': '255004', 'nom': 'B'}],
            [{'matricule': '24603', 'nom': 'R', 'filiere': 'SEA'}],
            [{'matricule': '24616', 'nom': 'D', 'groupe': 'G2'}])
        assert [(l['matricule'], l['statut']) for l in lignes] == [
            ('24603', 'rattache'), ('24616', 'dette'), ('24622', ''), ('255004', '')]

    def test_le_pdf_imprime_dans_cet_ordre_avec_les_mentions(self, db):
        from apps.absence.liste_appel import lignes_de_fiche
        f = fiche(3)
        f['etudiants'] = [{'matricule': '24622', 'nom': 'Alpha'}, {'matricule': '255004', 'nom': 'Beta'}]
        f['rattaches'] = [{'matricule': '24603', 'nom': 'Rho', 'filiere': 'SEA'}]
        f['dettes'] = [{'matricule': '24616', 'nom': 'Delta', 'groupe': 'L2 G2'}]
        f['lignes'] = lignes_de_fiche(f['etudiants'], f['rattaches'], f['dettes'])
        html = rendre(f)
        assert re.findall(r'class="mat-code">(\w+)<', html) == ['24603', '24616', '24622', '255004']
        corps = html[html.index('<tbody>'):]
        # Le rattaché prend sa place SANS mention (demande du 05/10/2026).
        assert corps.index('Rho') < corps.index('Delta')
        assert 'rattaché' not in corps
        assert corps.index('Delta') < corps.index('dette · L2 G2') < corps.index('Alpha')


class TestSansReduction:

    def test_les_fiches_sont_produites_sans_reduction_automatique(self, db, monkeypatch):
        from apps.absence import views
        recu = {}

        def faux_pdf(html, sortie, configuration=None, options=None):
            recu.update(options)
            return b'%PDF-1.4'

        monkeypatch.setattr(views.pdfkit, 'configuration', lambda **kw: None)
        monkeypatch.setattr(views.pdfkit, 'from_string', faux_pdf)
        from rest_framework.test import APIClient
        from tests.factories.auth import UserFactory
        c = APIClient()
        c.force_authenticate(UserFactory(username='adm_fiche', role='admin', is_superuser=True))
        r = c.get('/api/v1/absences/presences/fiches-pdf/',
                  {'annee_universitaire': '2026-2027', 'numero_semaine': 1})
        assert r.status_code == 200, r.status_code
        assert 'disable-smart-shrinking' in recu

    def test_les_autres_pdf_d_absences_ne_changent_pas(self, monkeypatch):
        from apps.absence import views
        recu = {}
        monkeypatch.setattr(views.pdfkit, 'configuration', lambda **kw: None)
        monkeypatch.setattr(views.pdfkit, 'from_string',
                            lambda html, sortie, configuration=None, options=None:
                            recu.update(options) or b'')
        views.PresenceViewSet._make_pdf('<html></html>')
        assert 'disable-smart-shrinking' not in recu
