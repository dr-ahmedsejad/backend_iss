"""Annonces : un message aux étudiants choisis par filière et par niveau.

Une case = une filière à un niveau de l'année active ; ses étudiants (ceux qui
ont un compte) reçoivent une notification « annonce » qui porte le texte entier.
"""
import pytest
from rest_framework.test import APIClient

from apps.annonces.models import Annonce
from apps.notifications.models import Notification
from tests.factories.auth import UserFactory
from tests.factories.em import DepartementAnnuelFactory
from tests.factories.parametres import NiveauFactory, YearFactory
from tests.factories.scolarite import EtudiantFactory, FiliereFactory

ANNEE = '2026-2027'
URL = '/api/v1/annonces/'


def api(u):
    c = APIClient()
    c.force_authenticate(user=u)
    return c


@pytest.fixture
def ecole(db):
    YearFactory(annee=ANNEE, est_active=True)
    l1, l2 = NiveauFactory(niveau='L1'), NiveauFactory(niveau='L2')
    sea, sdid = FiliereFactory(code='SEA'), FiliereFactory(code='SDID')

    def groupe(nom, f, n, **kw):
        return DepartementAnnuelFactory(nom=nom, annee_universitaire=ANNEE, filiere=f, niveau=n, **kw)

    g = {
        'SEA L1 G1': groupe('SEA L1 G1', sea, l1),
        'SEA L1 G2': groupe('SEA L1 G2', sea, l1),
        'SDID L1': groupe('SDID L1', sdid, l1),
        'SEA L2': groupe('SEA L2', sea, l2),
        'accueil': groupe('Accueil L1', sea, l1, is_container=True),
    }
    ancien = DepartementAnnuelFactory(nom='SEA L1 2025', annee_universitaire='2025-2026',
                                      filiere=sea, niveau=l1)

    def etu(dep, actif=True):
        u = UserFactory(role='etudiant', is_active=actif)
        EtudiantFactory(user=u, departement=dep)
        return u

    e = {
        'sea_l1_g1': etu(g['SEA L1 G1']), 'sea_l1_g2': etu(g['SEA L1 G2']),
        'sdid_l1': etu(g['SDID L1']), 'sea_l2': etu(g['SEA L2']),
        'accueil': etu(g['accueil']), 'ancien': etu(ancien), 'inactif': etu(g['SDID L1'], actif=False),
    }
    EtudiantFactory(user=None, departement=g['SEA L2'])   # sans compte
    return dict(l1=l1, l2=l2, sea=sea, sdid=sdid, g=g, e=e,
                admin=UserFactory(username='adm', role='admin', is_superuser=True))


def cle(f, n):
    return f'f{f.pk}-n{n.pk}'


def test_la_grille_croise_filieres_et_niveaux_de_l_annee(ecole):
    r = api(ecole['admin']).get(f'{URL}grille/')

    assert r.status_code == 200, r.data
    assert [l['label'] for l in r.data['lignes']] == ['SDID', 'SEA']
    assert [c['label'] for c in r.data['colonnes']] == ['L1', 'L2']
    cases = {c['cle']: c['nb'] for c in r.data['cases']}
    # SEA L1 = deux groupes réunis ; l'accueil et l'année passée n'y sont pas.
    assert cases == {cle(ecole['sea'], ecole['l1']): 2, cle(ecole['sdid'], ecole['l1']): 1,
                     cle(ecole['sea'], ecole['l2']): 1}


def test_tous_les_L1_recoivent_l_annonce_avec_le_texte_entier(ecole):
    texte = 'Réunion le [DATE].\n\nPrésence obligatoire.'
    r = api(ecole['admin']).post(URL, {
        'titre': 'Réunion des L1', 'texte': texte, 'resume': 'Tous les L1',
        'cibles': [cle(ecole['sea'], ecole['l1']), cle(ecole['sdid'], ecole['l1'])]}, format='json')

    assert r.status_code == 201, r.data
    assert r.data['nb_destinataires'] == 3
    e = ecole['e']
    recus = set(Notification.objects.values_list('destinataire_id', flat=True))
    assert recus == {e['sea_l1_g1'].pk, e['sea_l1_g2'].pk, e['sdid_l1'].pk}
    n = Notification.objects.get(destinataire=e['sdid_l1'])
    assert (n.type, n.titre, n.message) == ('annonce', 'Réunion des L1', texte)
    assert Annonce.objects.get().resume == 'Tous les L1'


def test_une_seule_case(ecole):
    api(ecole['admin']).post(URL, {'titre': 'T', 'texte': 'x',
                                   'cibles': [cle(ecole['sea'], ecole['l2'])]}, format='json')
    assert list(Notification.objects.values_list('destinataire_id', flat=True)) == [ecole['e']['sea_l2'].pk]


def test_cases_inconnues_ou_vides_refusees(ecole):
    c = api(ecole['admin'])
    assert c.post(URL, {'titre': 'T', 'texte': 'x', 'cibles': []}, format='json').status_code == 400
    assert c.post(URL, {'titre': 'T', 'texte': 'x', 'cibles': ['n1-f2', 'abc']},
                  format='json').status_code == 400
    assert not Notification.objects.exists() and not Annonce.objects.exists()


def test_titre_et_texte_obligatoires(ecole):
    r = api(ecole['admin']).post(URL, {'titre': ' ', 'texte': '', 'cibles': [cle(ecole['sea'], ecole['l2'])]},
                                 format='json')
    assert r.status_code == 400


def test_sans_le_droit_annonces_refuse(ecole):
    aa = UserFactory(username='assistant', role='AA')
    assert api(aa).get(f'{URL}grille/').status_code == 403
    assert api(aa).post(URL, {'titre': 'T', 'texte': 'x',
                              'cibles': [cle(ecole['sea'], ecole['l2'])]}, format='json').status_code == 403
    assert api(ecole['e']['sea_l2']).get(URL).status_code == 403


def test_l_historique(ecole):
    c = api(ecole['admin'])
    c.post(URL, {'titre': 'Première', 'texte': 'x', 'resume': 'SEA L2',
                 'cibles': [cle(ecole['sea'], ecole['l2'])]}, format='json')
    r = c.get(URL)
    assert [(a['titre'], a['resume'], a['nb_destinataires']) for a in r.data] == [('Première', 'SEA L2', 1)]
