"""
Le PDF d'une semaine : ce que porte son en-tête.

wkhtmltopdf n'est pas installé sur la machine de test ; on intercepte l'appel à
`pdfkit` et on lit le HTML qu'il aurait reçu. C'est l'en-tête qu'on vérifie,
pas la mise en page.
"""
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
