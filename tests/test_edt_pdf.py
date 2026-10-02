"""
Le PDF d'une semaine : ce que porte son en-tête.

wkhtmltopdf n'est pas installé sur la machine de test ; on intercepte l'appel à
`pdfkit` et on lit le HTML qu'il aurait reçu. C'est l'en-tête qu'on vérifie,
pas la mise en page.
"""
from io import BytesIO
from unittest import mock

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL = '/api/v1/edt/seances/pdf/'


def poser(monde, dept, num=1, jour='Lundi', creneau='08h00-09h30',
          em='SEA11', prof='Moustapha', salle='101'):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept],
        semaine=monde['semaines'][(num, jour)],
        creneau_fk=monde['creneaux'][creneau],
        em=monde['ems'][em], prof=monde['profs'][prof],
        salle=monde['salles'][salle], type_seance_fk=monde['cm'],
        origine='grille')


def html_du_pdf(user, **params):
    """Appelle la vue et rend le HTML passé à pdfkit, sans wkhtmltopdf."""
    capture = {}

    def faux_from_string(html, *_a, **_k):
        capture['html'] = html
        return b'%PDF-1.4 faux'

    with mock.patch('apps.edt.views._wkhtmltopdf', return_value='wkhtmltopdf'), \
         mock.patch('pdfkit.configuration', return_value=None), \
         mock.patch('pdfkit.from_string', side_effect=faux_from_string):
        r = api(user).get(URL, {'annee_universitaire': ANNEE,
                                'type_semestre': 'I', 'numero_semaine': 1,
                                **params})
    assert r.status_code == 200, getattr(r, 'data', r.content)
    return capture['html']


class TestEnTete:

    def test_le_groupe_est_precede_de_l_intitule_de_sa_filiere(self, monde, gens):
        """« Filière : G1 » ne disait pas laquelle. On imprime l'intitulé, puis le groupe."""
        poser(monde, 'G1')
        html = html_du_pdf(gens['admin'], departement=monde['depts']['G1'].pk)
        assert 'Filière : Statistique et Économie Appliquée — G1' in html

    def test_une_filiere_a_groupe_unique_ne_porte_que_son_intitule(self, monde, gens):
        """SDID L2 est seul de sa filière à ce niveau : « Science des Données — SDID L2 »
        répéterait pour rien. Ni le groupe, ni l'année d'étude : la ligne du dessous
        porte déjà le semestre, qui dit le niveau."""
        poser(monde, 'SDID L2', em='SDID31')
        html = html_du_pdf(gens['admin'], departement=monde['depts']['SDID L2'].pk)
        assert 'Filière : Science des Données</h4>' in html
        assert 'Semestre S3' in html

    def test_deux_groupes_de_td_gardent_leur_nom(self, monde, gens):
        """G1 et G2 partagent filière et niveau : là, le nom distingue."""
        poser(monde, 'G2')
        html = html_du_pdf(gens['admin'], departement=monde['depts']['G2'].pk)
        assert 'Filière : Statistique et Économie Appliquée — G2' in html

    def test_un_transversal_ne_porte_que_son_nom(self, monde, gens):
        """HE n'a pas de filière : rien à mettre devant, et l'axe le dit."""
        poser(monde, 'HE', em='HE11')
        html = html_du_pdf(gens['admin'], departement=monde['depts']['HE'].pk)
        assert 'Enseignement transversal : HE' in html

    def test_le_commentaire_du_gabarit_ne_s_imprime_pas(self, monde, gens):
        """Un `{# #}` sur plusieurs lignes n'est pas un commentaire : il s'imprimait."""
        poser(monde, 'G1')
        html = html_du_pdf(gens['admin'], departement=monde['depts']['G1'].pk)
        assert 'axe_label' not in html
        assert 'FACULTATIF' not in html
        assert '{#' not in html


# ── Une semaine tient sur UNE page ───────────────────────────────────────────

def pdf_de(pages):
    """Un vrai PDF de `pages` pages blanches."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=842, height=595)
    tampon = BytesIO()
    w.write(tampon)
    return tampon.getvalue()


def rendre(pages_par_echelle):
    """Appelle `_rendre_sur_une_page` avec un wkhtmltopdf simulé dont le nombre
    de pages dépend de l'échelle demandée. Rend (octets, échelles essayées)."""
    from apps.edt.views import _rendre_sur_une_page
    essais = []

    def faux_from_string(html, _sortie, configuration=None, options=None):
        echelle = options.get('zoom')
        essais.append(echelle)
        rendu = pages_par_echelle[echelle]
        return rendu if isinstance(rendu, bytes) else pdf_de(rendu)

    with mock.patch('apps.edt.views._wkhtmltopdf', return_value='wkhtmltopdf'), \
         mock.patch('pdfkit.configuration', return_value=None), \
         mock.patch('pdfkit.from_string', side_effect=faux_from_string):
        octets = _rendre_sur_une_page('<html></html>', {'orientation': 'Landscape'})
    return octets, essais


class TestUnePage:
    """Mesuré sur le VPS le 02/10/2026 : SEA L2 G2 tenait sur une page, SEA L3
    G1 non — intitulés et noms plus longs, et son samedi VIDE partait seul en
    page 2. La longueur des textes ne se prévoit pas : on rend, on compte."""

    def test_une_semaine_qui_tient_sort_comme_avant(self):
        """Un seul rendu, et sans `zoom` : rien ne change pour elle."""
        from pypdf import PdfReader
        octets, essais = rendre({None: 1})
        assert essais == [None]
        assert len(PdfReader(BytesIO(octets)).pages) == 1

    def test_une_semaine_qui_deborde_est_reduite_juste_assez(self):
        """Le cas SEA L3 G1 : 2 pages à taille réelle, 1 à 90 %. On s'arrête
        à la PREMIÈRE échelle qui tient — la plus lisible."""
        octets, essais = rendre({None: 2, '0.9': 1, '0.8': 1, '0.7': 1})
        assert essais == [None, '0.9']
        assert octets == pdf_de(1)

    def test_on_descend_tant_qu_il_le_faut(self):
        """Deux cours dans chaque case : il faut aller jusqu'à 70 %."""
        _, essais = rendre({None: 2, '0.9': 2, '0.8': 2, '0.7': 1})
        assert essais == [None, '0.9', '0.8', '0.7']

    def test_on_ne_descend_pas_sous_l_illisible(self):
        """Si même 70 % déborde, on rend 70 % — deux pages lisibles valent mieux
        qu'une illisible — et on n'essaie rien de plus petit."""
        from pypdf import PdfReader
        octets, essais = rendre({None: 3, '0.9': 3, '0.8': 3, '0.7': 2})
        assert essais == [None, '0.9', '0.8', '0.7']
        assert len(PdfReader(BytesIO(octets)).pages) == 2

    def test_un_pdf_illisible_sort_tel_quel(self):
        """Compter est un confort : ne pas savoir compter ne doit jamais
        empêcher le document de sortir, ni le faire rendre quatre fois."""
        octets, essais = rendre({None: b'%PDF-1.4 faux'})
        assert essais == [None]
        assert octets == b'%PDF-1.4 faux'

    def test_l_adresse_rend_la_version_qui_tient(self, monde, gens):
        """De bout en bout : c'est bien la version réduite qui est téléchargée."""
        poser(monde, 'G1')
        essais = []

        def faux_from_string(html, _sortie, configuration=None, options=None):
            essais.append(options.get('zoom'))
            return pdf_de(2 if options.get('zoom') is None else 1)

        with mock.patch('apps.edt.views._wkhtmltopdf', return_value='wkhtmltopdf'), \
             mock.patch('pdfkit.configuration', return_value=None), \
             mock.patch('pdfkit.from_string', side_effect=faux_from_string):
            r = api(gens['admin']).get(URL, {'annee_universitaire': ANNEE,
                                             'type_semestre': 'I', 'numero_semaine': 1,
                                             'departement': monde['depts']['G1'].pk})
        assert r.status_code == 200
        assert essais == [None, '0.9']
        assert r.content == pdf_de(1)


# ── Le PDF d'une salle dit devant QUI on enseigne ────────────────────────────

class TestGroupesSurLaSalle:
    """La vue calculait les groupes d'une case de salle ; le gabarit ne les
    imprimait pas. On savait qui occupait la salle, pas devant quels étudiants."""

    def test_la_salle_imprime_l_enseignant_puis_les_groupes(self, monde, gens):
        poser(monde, 'G2')
        poser(monde, 'G1')                       # le même cours, partagé
        html = html_du_pdf(gens['admin'], salle=monde['salles']['101'].pk)
        assert '<div class="groupes">' in html
        bloc = html.split('<div class="prof-name">', 1)[1]
        enseignant, groupes = bloc.split('<div class="groupes">', 1)
        assert 'Moustapha' in enseignant
        # Triés, et sur une seule ligne : un cours, une case.
        assert groupes.split('</div>', 1)[0].strip() == 'G1, G2'

    def test_le_pdf_d_un_groupe_n_a_pas_de_ligne_de_plus(self, monde, gens):
        """Le groupe est dans le titre : le répéter dans chaque case est du bruit."""
        poser(monde, 'G1')
        html = html_du_pdf(gens['admin'], departement=monde['depts']['G1'].pk)
        assert '<div class="groupes">' not in html

    def test_le_pdf_d_un_enseignant_n_imprime_pas_les_groupes_deux_fois(self, monde, gens):
        """Chez lui, les groupes occupent DÉJÀ la ligne du nom."""
        poser(monde, 'G1')
        html = html_du_pdf(gens['admin'], prof=monde['profs']['Moustapha'].pk)
        assert '<div class="groupes">' not in html
        assert 'G1' in html.split('<div class="prof-name">', 1)[1].split('</div>', 1)[0]
