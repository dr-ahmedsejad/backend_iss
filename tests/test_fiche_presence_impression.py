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


def fiche(n):
    return {
        'dep_nom': 'G1', 'groupe_libelle': 'L1 G1', 'filiere': 'Statistique',
        'date_seance': None, 'creneau': '08h00 à 09h30', 'type_seance': 'CM',
        'is_surveillance': False, 'em_intitule': 'Élément', 'prof_nom': 'Prof',
        'etudiants': [{'matricule': '%03d' % i, 'nom': 'Etu %d' % i} for i in range(n - 1)],
        'rattaches': [],
        'dettes': [{'matricule': '999', 'nom': 'Dette', 'groupe': 'G2'}],
        'liste_non_verifiee': False,
    }


def rendre(*fiches):
    from core.pdf_utils import get_institution_context
    return render_to_string('absence/fiches_presence.html', {
        'fiches': list(fiches), 'annee_universitaire': '2026-2027', 'numero_semaine': 1,
        **get_institution_context()})


class TestPlace:
    """Sans réduction automatique, c'est le gabarit qui fait tenir un groupe
    chargé sur une page : lignes plus basses au-delà de 24, puis de 30.
    Mesuré : 36 lignes tiennent sur une page A4 (le plus grand groupe réel en
    compte 27)."""

    @pytest.mark.parametrize('n, classe', [
        (20, 'etu-table"'), (24, 'etu-table"'),
        (25, 'etu-table serree"'), (30, 'etu-table serree"'),
        (31, 'etu-table tres-serree"'),
    ])
    def test_la_densite_suit_le_nombre_de_lignes(self, db, n, classe):
        assert 'class="%s' % classe in rendre(fiche(n))

    def test_chaque_fiche_a_sa_propre_densite(self, db):
        html = rendre(fiche(20), fiche(33))
        assert html.count('class="etu-table"') == 1
        assert html.count('class="etu-table tres-serree"') == 1


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
