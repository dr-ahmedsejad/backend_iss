"""
Les relevés de notes d'UN étudiant, tous ses semestres, en un seul PDF
(demande du 08/10/2026). Voir apps/documents/releves_etudiant.py.

Le rendu (wkhtmltopdf) et le calcul de la moyenne sont SIMULÉS : on vérifie
la sélection des semestres, l'ordre, la fusion et la numérotation officielle.
"""
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from pypdf import PdfReader, PdfWriter
from rest_framework.test import APIClient

from apps.absence.models import Etudiant
from apps.departement.models import Departement
from apps.documents import services
from apps.documents.models import DocumentOfficiel
from apps.inscriptions.models import InscriptionAdministrative, InscriptionPedagogique
from apps.parametres.models import Institution, Niveau, Semestre, Year
from apps.scolarite.models import Filiere

User = get_user_model()
URL = '/api/v1/documents/officiels/releves-etudiant/'


def _page(texte):
    w = PdfWriter()
    w.add_blank_page(width=72, height=72)
    w.add_metadata({'/Title': texte})
    b = BytesIO()
    w.write(b)
    return b.getvalue()


@pytest.fixture
def ctx(db):
    inst = Institution.objects.create(acronyme='TST', nom='T', est_principale=True)
    l1, l2 = Niveau.objects.create(niveau='L1'), Niveau.objects.create(niveau='L2')
    fil = Filiere.objects.create(code='STAT', intitule_fr='Statistique', institution=inst)
    dep = Departement.objects.create(nom='G1', institution=inst, filiere=fil, niveau=l1)
    s1 = Semestre.objects.create(code_semestre='S1', semestre='S1', niveau_semestre=l1, type_semestre='I')
    s2 = Semestre.objects.create(code_semestre='S2', semestre='S2', niveau_semestre=l1, type_semestre='P')
    s3 = Semestre.objects.create(code_semestre='S3', semestre='S3', niveau_semestre=l2, type_semestre='I')
    y1 = Year.objects.create(annee='2024-2025')
    y2 = Year.objects.create(annee='2025-2026', est_active=True)
    etu = Etudiant.objects.create(matricule='24607', nom='Etudiant', departement=dep, genre='M')
    a1 = InscriptionAdministrative.objects.create(etudiant=etu, annee_univ=y1, filiere=fil,
                                                  institution=inst, niveau=1, numero_inscription='I-1')
    a2 = InscriptionAdministrative.objects.create(etudiant=etu, annee_univ=y2, filiere=fil,
                                                  institution=inst, niveau=2, numero_inscription='I-2')
    # Créées dans le désordre : l'ordre du PDF ne doit pas en dépendre.
    for adm, sem in ((a2, s3), (a1, s2), (a1, s1)):
        InscriptionPedagogique.objects.create(inscription_admin=adm, semestre=sem)
    user = User.objects.create_user(username='sco', email='sco@t.l', password='x', role='admin',
                                    is_superuser=True)
    return {'etu': etu, 'user': user, 's1': s1, 's2': s2, 's3': s3}


@pytest.fixture(autouse=True)
def simules(monkeypatch):
    """S1 : 12,5 (validé) ; S2 : 9 (non validé) ; S3 : en cours, sans résultat."""
    moyennes = {'S1': 12.5, 'S2': 9.0, 'S3': None}
    notes = {'S1', 'S2'}

    def contexte(doc, etudiant, institution, data):
        code = Semestre.objects.get(pk=doc.semestre_id).code_semestre
        m = moyennes[code]
        return {'moyenne_semestre': m,
                'decision_semestre': None if m is None else ('Validé' if m >= 10 else 'Non validé')}

    monkeypatch.setattr(services, '_build_context_releve', contexte)
    from apps.documents import releves_etudiant
    # Des notes sur S1 et S2 ; aucune sur S3 (en cours).
    monkeypatch.setattr(releves_etudiant, 'a_des_notes',
                        lambda ip: ip.semestre.code_semestre in notes)
    monkeypatch.setattr(services, '_generer_pdf',
                        lambda doc, etu, data, is_duplicata=False: _page(f'releve {doc.semestre_id}'))
    return moyennes


class TestSemestres:

    def test_tous_ses_semestres_dans_l_ordre(self, ctx):
        from apps.documents.releves_etudiant import semestres_de
        lignes = semestres_de(ctx['etu'])
        assert [(l['annee_universitaire'], l['semestre_code'], l['a_des_resultats'])
                for l in lignes] == [('2024-2025', 'S1', True), ('2024-2025', 'S2', True),
                                     ('2025-2026', 'S3', False)]
        assert lignes[0]['decision'] == 'Validé' and lignes[1]['decision'] == 'Non validé'
        assert lignes[2]['moyenne'] is None


class TestGeneration:

    def test_un_pdf_un_releve_par_semestre_note(self, ctx):
        from apps.documents.releves_etudiant import generer_releves_etudiant
        pdf, lignes = generer_releves_etudiant(ctx['etu'], ctx['user'])
        lecteur = PdfReader(BytesIO(pdf))
        assert len(lecteur.pages) == 2                      # S1 et S2 ; S3 sans résultat
        docs = DocumentOfficiel.objects.filter(etudiant=ctx['etu'], type_document='releve_semestre')
        assert sorted(docs.values_list('semestre_id', flat=True)) == sorted([ctx['s1'].pk, ctx['s2'].pk])
        assert [l.get('imprime', False) for l in lignes] == [True, True, False]

    def test_reimprimer_ne_renumerote_pas(self, ctx):
        from apps.documents.releves_etudiant import generer_releves_etudiant
        generer_releves_etudiant(ctx['etu'], ctx['user'])
        numeros = set(DocumentOfficiel.objects.values_list('numero_serie', flat=True))
        generer_releves_etudiant(ctx['etu'], ctx['user'])
        assert set(DocumentOfficiel.objects.values_list('numero_serie', flat=True)) == numeros

    def test_une_moyenne_sans_note_n_est_pas_un_resultat(self, ctx, simules):
        """Le calcul du relevé rend 0,00 pour un semestre en cours : sans note
        saisie, le semestre n'est pas imprimé — il passerait pour un échec."""
        from apps.documents.releves_etudiant import semestres_de
        simules['S3'] = 0.0
        s3 = semestres_de(ctx['etu'])[2]
        assert (s3['a_des_resultats'], s3['moyenne'], s3['decision']) == (False, None, None)

    def test_aucun_semestre_note(self, ctx, simules):
        from apps.documents.releves_etudiant import generer_releves_etudiant
        simules.update({'S1': None, 'S2': None})
        with pytest.raises(ValueError):
            generer_releves_etudiant(ctx['etu'], ctx['user'])


class TestAdresses:

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_apercu(self, ctx):
        r = self._client(ctx['user']).get(URL, {'etudiant': ctx['etu'].pk})
        assert r.status_code == 200, r.data
        assert r.data['etudiant']['matricule'] == '24607'
        assert [l['semestre_code'] for l in r.data['semestres']] == ['S1', 'S2', 'S3']
        assert not DocumentOfficiel.objects.exists()        # l'aperçu n'émet rien

    def test_le_pdf(self, ctx):
        r = self._client(ctx['user']).post(URL, {'etudiant': ctx['etu'].pk}, format='json')
        assert r.status_code == 200
        assert r['Content-Type'] == 'application/pdf'
        assert (r['X-Generated'], r['X-Total']) == ('2', '3')
        assert 'releves_24607.pdf' in r['Content-Disposition']

    def test_etudiant_inconnu(self, ctx):
        assert self._client(ctx['user']).get(URL, {'etudiant': 999999}).status_code == 400

    def test_sans_droit(self, ctx):
        ens = User.objects.create_user(username='ens', email='ens@t.l', password='x', role='enseignant')
        assert self._client(ens).post(URL, {'etudiant': ctx['etu'].pk}, format='json').status_code == 403


class TestPlusieursEtudiants:

    @pytest.fixture
    def second(self, ctx):
        """Un second étudiant, sans aucun semestre noté."""
        dep = Departement.objects.first()
        return Etudiant.objects.create(matricule='24608', nom='Second', departement=dep, genre='F')

    def test_un_pdf_pour_plusieurs_dans_l_ordre(self, ctx, second):
        from apps.documents.releves_etudiant import generer_releves_etudiants
        pdf, bilan = generer_releves_etudiants([second, ctx['etu']], ctx['user'])
        assert [(b['matricule'], b['releves']) for b in bilan] == [('24608', 0), ('24607', 2)]
        assert len(PdfReader(BytesIO(pdf)).pages) == 2

    def test_par_l_adresse(self, ctx, second):
        c = APIClient()
        c.force_authenticate(ctx['user'])
        r = c.post(URL, {'etudiants': [ctx['etu'].pk, second.pk]}, format='json')
        assert r.status_code == 200
        assert r['X-Generated'] == '2'
        assert r['X-Etudiants-Sans-Releve'] == '24608'
        assert 'releves_2_etudiants.pdf' in r['Content-Disposition']

    def test_un_inconnu_dans_la_liste(self, ctx):
        c = APIClient()
        c.force_authenticate(ctx['user'])
        r = c.post(URL, {'etudiants': [ctx['etu'].pk, 999999]}, format='json')
        assert r.status_code == 400

    def test_trop_d_etudiants(self, ctx, monkeypatch):
        from apps.documents import releves_etudiant
        monkeypatch.setattr(releves_etudiant, 'MAX_ETUDIANTS', 1)
        with pytest.raises(ValueError):
            releves_etudiant.generer_releves_etudiants([ctx['etu'], ctx['etu']], ctx['user'])
