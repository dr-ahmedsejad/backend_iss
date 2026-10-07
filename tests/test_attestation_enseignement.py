"""
L'attestation d'enseignement — ce qu'elle imprime.

Constaté le 07/10/2026 sur l'attestation d'un vacataire (2025-2026, 28,67 h) :
  * 15 lignes à « — — — 0,00 » : ses surveillances d'examen, saisies en
    vacations avec l'EM de l'épreuve ;
  * « a assuré des Cours Magistraux (CM)… » alors que sa seule colonne CM
    était de l'encadrement ;
  * « en filière Statistique et Statistiques, Economie et Applications » —
    la seconde ne venait que de ces lignes vides ;
  * « Ministre de la Défense », « N° ISS ….. / Nouakchott, le ….. » vides,
    « HAMAR - titulaire du NNI ».

Le calcul (apps/vacation) n'est pas modifié : tout se joue à l'affichage —
templates/ et core/templatetags/attestations.py.
"""
import re

from tests._edt_decor import gens, monde  # noqa: F401

import pytest
from django.template.loader import render_to_string

INFO = {'intitule': 'Informatique bureautique et TIC', 'id_semestre': 'S1', 'filiere': 'Statistique',
        'niveau': 'L1', 'heures_cm': 0.0, 'heures_td': 10.5, 'heures_tp': 18.5, 'heures_service': 0.0,
        'is_encadrement_perm': False, 'total_pondere': 19.33}
SANS_FILIERE = {'intitule': 'Informatique, bureautique et TIC', 'id_semestre': 'S1', 'filiere': '',
                'niveau': 'L1', 'heures_cm': 0.0, 'heures_td': 0.0, 'heures_tp': 2.0,
                'heures_service': 0.0, 'is_encadrement_perm': False, 'total_pondere': 1.33}
SURVEILLANCE = {'intitule': 'Econométrie I', 'id_semestre': 'S4',
                'filiere': 'Statistiques, Economie et Applications', 'niveau': 'L2',
                'heures_cm': 0.0, 'heures_td': 0.0, 'heures_tp': 0.0, 'heures_service': 0.0,
                'is_encadrement_perm': False, 'total_pondere': 0.0}
ENCADREMENT = {'intitule': 'Encadrement', 'id_semestre': '—', 'filiere': '', 'niveau': '',
               'heures_cm': 8.0, 'heures_td': 0.0, 'heures_tp': 0.0, 'heures_service': 0.0,
               'is_encadrement_perm': True, 'total_pondere': 8.0}
COURS = {**INFO, 'intitule': 'Algèbre', 'heures_cm': 12.0, 'heures_td': 0.0, 'heures_tp': 0.0,
         'total_pondere': 12.0}


def rendre(modules, **ctx):
    from types import SimpleNamespace
    from core.pdf_utils import get_institution_context
    prof = SimpleNamespace(nom='Cheikh Brahim Amar HAMAR', NNI=8732439066, type='vacataire', genre='M')
    contexte = {
        **get_institution_context(),
        'prof': prof, 'civilite': 'Monsieur', 'interesse': "l'intéressé", 'qualite': 'Vacataire',
        'afficher_nni': True, 'titre_document': "Attestation d'Enseignement",
        'numero_attestation': 'AT-20252026-0084-39A7', 'qr_data_uri': '',
        'modules': modules, 'grand_total_ponde': 28.67,
        'texte_types': 'des Cours Magistraux (CM), des Travaux Dirigés (TD) et des Travaux Pratiques (TP)',
        'texte_depts_description': 'G1, G2 et SEA L2 - G1',
        'texte_filieres_description': 'Statistique et Statistiques, Economie et Applications',
        'annee_universitaire': '2025-2026', 'date_debut_periode': '', 'date_fin_periode': '',
        'lieu': 'Nouakchott', 'date_generation': '07/10/2026', 'is_service_fait': False,
    }
    contexte.update(ctx)
    return render_to_string('vacation_attestation_pdf.html', contexte)


def texte(html):
    return ' '.join(re.sub(r'<[^>]+>', ' ', html).split())


@pytest.fixture
def cas_du_07_10(db):
    return rendre([INFO, SANS_FILIERE, SURVEILLANCE, ENCADREMENT])


class TestLignes:

    def test_une_ligne_sans_heures_n_est_pas_imprimee(self, cas_du_07_10):
        assert 'Econométrie I' not in cas_du_07_10
        assert 'Informatique bureautique et TIC' in cas_du_07_10
        assert 'Informatique, bureautique et TIC' in cas_du_07_10
        assert 'Encadrement' in cas_du_07_10

    def test_le_total_reste_celui_du_calcul(self, cas_du_07_10):
        assert '28,67 h' in texte(cas_du_07_10) or '28.67 h' in texte(cas_du_07_10)

    def test_que_des_lignes_vides_donne_le_message(self, db):
        assert 'Aucun module enregistré' in rendre([SURVEILLANCE])


class TestPhrase:

    def test_l_encadrement_n_est_plus_un_cours_magistral(self, cas_du_07_10):
        t = texte(cas_du_07_10)
        assert 'Cours Magistraux' not in t
        assert ("a assuré des Travaux Dirigés (TD), des Travaux Pratiques (TP) "
                "et de l'encadrement") in t.replace('&#x27;', "'").replace('&#39;', "'")

    def test_un_vrai_cours_magistral_reste_nomme(self, db):
        assert 'des Cours Magistraux (CM)' in texte(rendre([COURS, ENCADREMENT]))

    def test_seules_les_filieres_du_tableau(self, cas_du_07_10):
        t = texte(cas_du_07_10)
        assert 'dans la filière Statistique .' in t
        assert 'Statistiques, Economie et Applications' not in t

    def test_plusieurs_filieres_au_pluriel(self, db):
        autre = {**COURS, 'filiere': 'Statistiques, Economie et Applications'}
        assert ('dans les filières Statistique et Statistiques, Economie et Applications'
                in texte(rendre([INFO, autre])))

    def test_une_virgule_et_non_un_tiret(self, cas_du_07_10):
        t = texte(cas_du_07_10)
        assert 'HAMAR , titulaire du NNI 8732439066' in t
        assert 'HAMAR -' not in t


class TestEnTete:

    def test_ministere_et_non_ministre(self, cas_du_07_10):
        assert 'Ministère de la Défense' in cas_du_07_10
        assert 'Ministre de la Défense' not in cas_du_07_10

    def test_numero_et_date_a_la_place_des_pointilles(self, cas_du_07_10):
        t = texte(cas_du_07_10)
        assert 'N° ISS AT-20252026-0084-39A7' in t
        assert 'Nouakchott, le 07/10/2026' in t
        # Le numéro n'est plus répété sous le titre.
        assert t.count('AT-20252026-0084-39A7') == 2          # français + arabe

    def test_sans_numero_les_pointilles_restent(self, db):
        html = rendre([INFO], numero_attestation='', date_generation='')
        assert html.count('class="dots"') == 4


class TestServiceFait:
    """L'attestation de service fait (personnel) garde son texte et son tableau."""

    def test_inchangee(self, db):
        html = rendre([], is_service_fait=True, texte_types='des activités de surveillance',
                      services_list=[{'type_activite': 'Surveillance', 'nb_seances': 3,
                                      'total_heures': 6.0}], grand_total_heures=6.0,
                      texte_filieres_description='Statistique')
        t = texte(html)
        assert 'a effectué des activités de surveillance' in t
        assert 'en filière Statistique' in t


# ── Les surveillances d'examens (décision du 07/10/2026) ──────────────────────
# UNE ligne, à part, HORS du volume équivalent CM. apps/vacation n'est pas
# modifié : apps/documents/attestation_surveillances.py compose autour.

@pytest.fixture
def surveillant(monde):
    import datetime as dt
    from tests.factories.parametres import SeanceFactory
    from apps.vacation.models import Vacation
    surv, ef, td = (SeanceFactory(type_seance='Surveillance'), SeanceFactory(type_seance='EF'),
                    SeanceFactory(type_seance='TD'))
    prof = monde['profs']['Moustapha']

    def vac(type_, duree, em=None, jour=1):
        return Vacation.objects.create(prof=prof, type=type_, duree=duree, em=em,
                                       date=dt.date(2026, 1, jour), annee_univ='2025-2026',
                                       institution=monde['inst'])
    vac(surv, 2.0, em=monde['ems']['SEA11'])            # avec l'EM de l'épreuve
    vac(surv, 1.5)                                      # sans EM
    vac(ef, 1.5, em=monde['ems']['SDID31'], jour=20)    # typée par l'épreuve
    vac(td, 3.0, em=monde['ems']['SEA11'])              # enseignement : pas une surveillance
    return prof


class TestSurveillances:

    def test_toutes_ses_surveillances_et_seulement_elles(self, monde, surveillant):
        from apps.documents.attestation_surveillances import surveillances_de
        assert surveillances_de(surveillant, '2025-2026') == {'nombre': 3, 'heures': 5.0}

    def test_bornees_a_la_periode_demandee(self, monde, surveillant):
        import datetime as dt
        from apps.documents.attestation_surveillances import surveillances_de
        assert surveillances_de(surveillant, '2025-2026', date_fin=dt.date(2026, 1, 10)) == \
            {'nombre': 2, 'heures': 3.5}

    def test_bornees_a_la_filiere_demandee(self, monde, surveillant):
        from apps.documents.attestation_surveillances import surveillances_de
        assert surveillances_de(surveillant, '2025-2026', filiere_id=monde['f_sdid'].pk) == \
            {'nombre': 1, 'heures': 1.5}

    def test_aucune_surveillance_pas_de_ligne(self, monde):
        from apps.documents.attestation_surveillances import surveillances_de
        assert surveillances_de(monde['profs']['Abderahmane'], '2025-2026') is None

    def test_la_ligne_est_imprimee_hors_total(self, db):
        t = texte(rendre([INFO], surveillances={'nombre': 30, 'heures': 83.5}))
        assert "Surveillance d'examens 30 83,5 h" in t or "Surveillance d'examens 30 83.5 h" in t
        assert 'ne sont pas comptées dans le volume horaire équivalent CM' in t
        assert '28,67 h' in t or '28.67 h' in t

    def test_sans_surveillance_pas_de_tableau(self, cas_du_07_10):
        assert "Surveillance d'examens" not in cas_du_07_10

    def test_l_adresse_de_l_attestation_passe_par_la_vue_composee(self):
        from django.urls import resolve
        from apps.documents.attestation_surveillances import AttestationAvecSurveillancesViewSet
        assert resolve('/api/v1/vacations/pdf-attestation/').func.cls is \
            AttestationAvecSurveillancesViewSet

    def test_par_la_vraie_adresse(self, monde, surveillant, gens):
        """De bout en bout : la vue d'origine calcule, la vue composée ajoute."""
        from unittest import mock
        from tests._edt_decor import api
        recu = {}

        def capter(template, contexte, nom, orientation='Portrait'):
            recu.update(contexte)
            from django.http import HttpResponse
            return HttpResponse(b'%PDF', content_type='application/pdf')

        with mock.patch('apps.vacation.views._render_pdf', capter):
            r = api(gens['admin']).get('/api/v1/vacations/pdf-attestation/',
                                       {'annee_univ': '2025-2026', 'prof_id': surveillant.pk})
        assert r.status_code == 200, getattr(r, 'data', r.content[:200])
        assert recu['surveillances'] == {'nombre': 3, 'heures': 5.0}
        # Le total attesté reste celui du calcul : 3 h de TD × 2/3.
        assert round(recu['grand_total_ponde'], 2) == 2.0
