"""
Groupes d'anglais — ce que voit l'étudiant (09/10/2026).

Son emploi du temps (portail et application) et l'annonce « emploi validé »
lisent son groupe ; pour l'anglais, ce groupe est son groupe d'anglais de
l'année, où il est AFFECTÉ et non rattaché. Voir apps/edt/anglais.py.

Décor (L2) : e1 en « SEA L2 - G1 », affecté à Intermediate ; e2 en
« SEA L2 - G1 », sans groupe d'anglais.
"""
from decimal import Decimal

import pytest

from apps.edt import anglais
from apps.edt.annonces import annoncer_semaine
from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401
from tests.test_annonce_emploi import etudiant, notifs
from tests.test_fiches_cm import suivie


@pytest.fixture
def ang(monde):
    from apps.em.models import EM
    monde['ems']['ANG'] = EM.objects.create(
        code_em='HE0141', intitule='Anglais', filiere=monde['f_sea'], semestre=monde['s3'],
        institution=monde['inst'], seuil_eliminatoire=Decimal('6.00'))
    e1 = etudiant(monde, 'SEA L2 G1', '25001')
    e2 = etudiant(monde, 'SEA L2 G1', '25002')
    a = anglais.creer_groupe(ANNEE, monde['l2'].pk, 'Anglais L2 — Intermediate')
    anglais.affecter(ANNEE, [(e1.pk, a.pk)])
    monde['depts']['ANG A'] = a.departement
    return {'A': a.departement, 'e1': e1, 'e2': e2}


class TestLAnnonce:

    def test_les_affectes_d_un_groupe_d_anglais_sont_prevenus(self, monde, ang):
        bilan = annoncer_semaine(ANNEE, 'I', 1, [ang['A'].pk])
        assert bilan['valides'] == 1
        [n] = notifs(ang['e1'])
        assert n.titre == 'Emploi du temps de la semaine 1 validé'
        assert notifs(ang['e2']) == []

    def test_une_seule_notification_quand_ses_deux_groupes_sont_annonces(self, monde, ang):
        annoncer_semaine(ANNEE, 'I', 1, [monde['depts']['SEA L2 G1'].pk, ang['A'].pk])
        assert len(notifs(ang['e1'])) == 1
        assert len(notifs(ang['e2'])) == 1

    def test_valide_l_emporte_sur_modifie(self, monde, ang):
        annoncer_semaine(ANNEE, 'I', 1, [monde['depts']['SEA L2 G1'].pk])
        # Le groupe habituel est annoncé de nouveau (« modifié »), le groupe
        # d'anglais pour la première fois (« validé ») : e1 n'en reçoit qu'une.
        annoncer_semaine(ANNEE, 'I', 1, [monde['depts']['SEA L2 G1'].pk, ang['A'].pk])
        titres = [n.titre for n in notifs(ang['e1'])]
        assert titres == ['Emploi du temps de la semaine 1 validé'] * 2
        assert [n.titre for n in notifs(ang['e2'])][-1] == 'Emploi du temps de la semaine 1 modifié'


@pytest.fixture
def inscrits(monde, ang):
    """Les deux étudiants inscrits en S3 cette année : le portail part de
    l'inscription pédagogique."""
    from apps.inscriptions.models import InscriptionAdministrative, InscriptionPedagogique
    from apps.parametres.models import Year
    annee = Year.objects.get(annee=ANNEE)
    for i, e in enumerate((ang['e1'], ang['e2'])):
        ia = InscriptionAdministrative.objects.create(
            etudiant=e, annee_univ=annee, filiere=monde['f_sea'], institution=monde['inst'],
            niveau=2, numero_inscription=f'I-{i}')
        InscriptionPedagogique.objects.create(inscription_admin=ia, semestre=monde['s3'])
    suivie(monde, 'SEA L2 G1', em='SEA31', type_='td')
    suivie(monde, 'ANG A', em='ANG', type_='td', creneau='09h45-11h15',
           prof='Abderahmane', salle='102')
    return ang


def _codes(user):
    r = api(user).get('/api/v1/portail/emploi-du-temps/', {'semaine': 1})
    assert r.status_code == 200, r.data
    return sorted(s['em_code'] for jour in r.data['grille'].values()
                  for cases in jour.values() for s in cases)


class TestLEmploiDuTemps:

    def test_l_etudiant_affecte_voit_sa_seance_d_anglais(self, monde, inscrits):
        assert _codes(inscrits['e1'].user) == ['HE0141', 'SEA31']

    def test_les_autres_ne_la_voient_pas(self, monde, inscrits):
        assert _codes(inscrits['e2'].user) == ['SEA31']
