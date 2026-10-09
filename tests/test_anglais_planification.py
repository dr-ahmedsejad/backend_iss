"""
Groupes d'anglais — étape 2 : la planification (09/10/2026).

Une séance d'anglais (élément dont l'intitulé commence par « Anglais ») se pose
sur un groupe d'anglais ; son créneau n'est pas fixe, chaque placement — grille
ou semaine — repasse par les règles. Voir apps/edt/anglais.py.

Décor (L2, semestres impairs) : e1 de « SEA L2 - G1 » est en groupe A, e2 de
« SDID L2 » en groupe B, e3 de « SEA L2 - G2 » n'est affecté nulle part.
"""
from decimal import Decimal

import pytest

from apps.edt import anglais
from tests._edt_decor import (ANNEE, URL_SEANCE, URL_SEANCE_TYPE, api, case,  # noqa: F401
                              gens, grille, monde, seance)


@pytest.fixture
def ang(monde):
    from apps.absence.models import Etudiant
    from apps.em.models import EM

    def em(code, semestre, filiere):
        return EM.objects.create(code_em=code, intitule='Anglais', filiere=filiere,
                                 semestre=semestre, institution=monde['inst'],
                                 seuil_eliminatoire=Decimal('6.00'))

    monde['ems'].update({
        'ANG-SEA':  em('HE0141', monde['s3'], monde['f_sea']),
        'ANG-SDID': em('HE141', monde['s3'], monde['f_sdid']),
        'ANG-L1':   em('HE51', monde['s1'], monde['f_sea']),
    })
    d = monde['depts']
    e1 = Etudiant.objects.create(matricule='25001', nom='Un', departement=d['SEA L2 G1'], genre='M')
    e2 = Etudiant.objects.create(matricule='25002', nom='Deux', departement=d['SDID L2'], genre='F')
    Etudiant.objects.create(matricule='25003', nom='Trois', departement=d['SEA L2 G2'], genre='M')
    a = anglais.creer_groupe(ANNEE, monde['l2'].pk, 'Anglais L2 — Intermediate')
    b = anglais.creer_groupe(ANNEE, monde['l2'].pk, 'Anglais L2 — Advanced')
    anglais.affecter(ANNEE, [(e1.pk, a.pk), (e2.pk, b.pk)])
    d['ANG A'], d['ANG B'] = a.departement, b.departement
    return {'A': a, 'B': b}


def _poser(monde, gens, dept, em, **kw):
    return api(gens['admin']).post(URL_SEANCE_TYPE, case(
        monde, grille(monde, dept), em=em, type_seance='td', **kw), format='json')


class TestCeQueRecoitUnGroupeDAnglais:

    def test_l_anglais_de_son_niveau(self, monde, gens, ang):
        r = _poser(monde, gens, 'ANG A', 'ANG-SEA')
        assert r.status_code == 201, r.data

    def test_pas_un_autre_cours(self, monde, gens, ang):
        r = _poser(monde, gens, 'ANG A', 'SEA31')
        assert r.status_code == 400
        assert 'groupe d\'anglais' in str(r.data['errors']['em'])

    def test_pas_l_anglais_d_un_autre_niveau(self, monde, gens, ang):
        r = _poser(monde, gens, 'ANG A', 'ANG-L1')
        assert r.status_code == 400
        assert 'pas de l\'anglais de L2' in str(r.data['errors']['em'])

    def test_n_importe_quelle_fiche_d_anglais_du_niveau(self, monde, gens, ang):
        """Une fiche EM par filière : celle de SDID convient aussi."""
        assert _poser(monde, gens, 'ANG A', 'ANG-SDID').status_code == 201


class TestLeGroupeHabituel:

    def test_ne_recoit_plus_l_anglais_quand_son_niveau_a_ses_groupes(self, monde, gens, ang):
        r = _poser(monde, gens, 'SEA L2 G1', 'ANG-SEA')
        assert r.status_code == 400
        assert 'Anglais L2 — Intermediate' in str(r.data['errors']['em'])

    def test_un_niveau_sans_groupe_d_anglais_continue_comme_avant(self, monde, gens, ang):
        assert _poser(monde, gens, 'G1', 'ANG-L1').status_code == 201

    def test_une_seance_d_anglais_deja_posee_reste_modifiable(self, monde, gens):
        """Posée AVANT la création des groupes d'anglais : on peut encore la
        corriger (le temps de la déplacer), pas y remettre de l'anglais."""
        from apps.edt.models import SeanceType
        from apps.em.models import EM
        ang_sea = EM.objects.create(code_em='HE0141', intitule='Anglais', filiere=monde['f_sea'],
                                    semestre=monde['s3'], institution=monde['inst'],
                                    seuil_eliminatoire=Decimal('6.00'))
        ang_sdid = EM.objects.create(code_em='HE141', intitule='Anglais', filiere=monde['f_sdid'],
                                     semestre=monde['s3'], institution=monde['inst'],
                                     seuil_eliminatoire=Decimal('6.00'))
        g = grille(monde, 'SEA L2 G1')
        st = SeanceType.objects.create(grille=g, jour_fk=monde['jours']['Lundi'],
                                       creneau_fk=monde['creneaux']['08h00-09h30'], em=ang_sea,
                                       type_seance_fk=monde['td'])
        anglais.creer_groupe(ANNEE, monde['l2'].pk)
        c = api(gens['admin'])
        r = c.patch(f'{URL_SEANCE_TYPE}{st.pk}/', {'salle': monde['salles']['102'].pk}, format='json')
        assert r.status_code == 200, r.data
        r = c.patch(f'{URL_SEANCE_TYPE}{st.pk}/', {'em': ang_sdid.pk}, format='json')
        assert r.status_code == 400


class TestLesEtudiantsCommuns:

    def test_le_groupe_d_un_etudiant_affecte_est_pris(self, monde, gens, ang):
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 201
        r = _poser(monde, gens, 'SEA L2 G1', 'SEA31', prof='Abderahmane', salle='102')
        assert r.status_code == 400
        assert 'creneau_fk' in r.data['errors']

    def test_dans_l_autre_sens_aussi(self, monde, gens, ang):
        assert _poser(monde, gens, 'SEA L2 G1', 'SEA31', prof='Abderahmane',
                      salle='102').status_code == 201
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 400

    def test_un_groupe_sans_etudiant_affecte_reste_libre(self, monde, gens, ang):
        """Personne de « SEA L2 - G2 » n'est en groupe A : il a cours."""
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 201
        assert _poser(monde, gens, 'SEA L2 G2', 'SEA31', prof='Abderahmane',
                      salle='102').status_code == 201

    def test_l_autre_groupe_d_anglais_n_est_pas_gene(self, monde, gens, ang):
        """Intermediate et Advanced au même créneau : aucun étudiant commun."""
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 201
        assert _poser(monde, gens, 'ANG B', 'ANG-SDID', prof='Abderahmane',
                      salle='102').status_code == 201

    def test_le_groupe_de_l_etudiant_de_l_autre_groupe_d_anglais_est_libre(self, monde, gens, ang):
        """e2 (SDID L2) est en B : quand A a cours, SDID L2 aussi peut."""
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 201
        assert _poser(monde, gens, 'SDID L2', 'SDID31', prof='Abderahmane',
                      salle='102').status_code == 201

    def test_un_transversal_croise_le_groupe_d_anglais(self, monde, gens, ang):
        assert _poser(monde, gens, 'HE', 'HE11', prof='Abderahmane', salle='102').status_code == 201
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 400

    def test_sans_affectation_le_niveau_entier_est_suppose(self, monde, gens, ang):
        from apps.edt.models import AffectationAnglais
        AffectationAnglais.objects.all().delete()
        assert _poser(monde, gens, 'ANG A', 'ANG-SEA').status_code == 201
        assert _poser(monde, gens, 'SEA L2 G2', 'SEA31', prof='Abderahmane',
                      salle='102').status_code == 400
        # … mais pas un autre niveau.
        assert _poser(monde, gens, 'G1', 'SEA11', prof='Abderahmane',
                      salle='102').status_code == 201


class TestLaSemaine:
    """Le créneau n'est pas fixe : une séance déplacée dans la semaine
    repasse par les mêmes règles."""

    def test_une_seance_de_la_semaine(self, monde, gens, ang):
        c = api(gens['admin'])
        r = c.post(URL_SEANCE, seance(monde, 'ANG A', jour='Mardi', em='ANG-SEA',
                                      type_seance='td'), format='json')
        assert r.status_code == 201, r.data
        r = c.post(URL_SEANCE, seance(monde, 'SEA L2 G1', jour='Mardi', em='SEA31',
                                      prof='Abderahmane', salle='102', type_seance='td'),
                   format='json')
        assert r.status_code == 400

    def test_pas_d_autre_cours_dans_la_semaine(self, monde, gens, ang):
        r = api(gens['admin']).post(URL_SEANCE, seance(monde, 'ANG A', em='SEA31',
                                                       type_seance='td'), format='json')
        assert r.status_code == 400 and 'em' in r.data['errors']


class TestLeGroupeDansLePlanificateur:

    def test_propose_meme_sans_etudiant_rattache(self, monde, gens, ang):
        r = api(gens['admin']).get('/api/v1/departements/all/',
                                   {'avec_etudiants': 1, 'annee_universitaire': ANNEE})
        par_nom = {d['nom']: d for d in r.data}
        assert par_nom['Anglais L2 — Intermediate']['groupe_anglais'] == 1
        assert par_nom['Anglais L2 — Advanced']['groupe_anglais'] == 2
        assert par_nom['SEA L2 - G1']['groupe_anglais'] is None

    def test_qui_planifie_le_niveau_planifie_ses_groupes_d_anglais(self, monde, gens, ang):
        assert gens['autre_de'].managed_departements.filter(pk=ang['A'].departement_id).exists()
        assert not gens['de'].managed_departements.filter(pk=ang['A'].departement_id).exists()

    def test_la_delegation_n_empeche_pas_de_supprimer(self, monde, gens):
        g = anglais.creer_groupe(ANNEE, monde['l2'].pk)
        anglais.supprimer(g)

    def test_son_niveau_ne_change_pas_par_l_ecran_des_groupes(self, monde, gens, ang):
        r = api(gens['admin']).patch(f'/api/v1/departements/{ang["A"].departement_id}/',
                                     {'niveau': monde['l1'].pk}, format='json')
        assert r.status_code == 400
        assert 'groupe d\'anglais' in str(r.data)


class TestLePartage:

    def test_l_anglais_ne_se_partage_pas_avec_un_groupe_habituel(self, monde, gens, ang):
        c = api(gens['admin'])
        r = c.post(URL_SEANCE, seance(monde, 'ANG A', em='ANG-SEA', type_seance='td'), format='json')
        assert r.status_code == 201, r.data
        r = c.post(f'{URL_SEANCE}{r.data["id"]}/partager/',
                   {'departements': [monde['depts']['SEA L2 G2'].pk]}, format='json')
        assert r.status_code == 400
        assert 'groupes d\'anglais' in r.data['detail']

    def test_un_autre_cours_ne_se_partage_pas_avec_un_groupe_d_anglais(self, monde, gens, ang):
        c = api(gens['admin'])
        r = c.post(URL_SEANCE, seance(monde, 'SEA L2 G2', em='SEA31', type_seance='td'), format='json')
        assert r.status_code == 201, r.data
        r = c.post(f'{URL_SEANCE}{r.data["id"]}/partager/',
                   {'departements': [monde['depts']['ANG B'].pk]}, format='json')
        assert r.status_code == 400
