"""
« Avancement EMs » et « Avancement par semestre » : les EM de l'année.

Un EM est rattaché à sa filière et réutilisé d'année en année ; son
`departement` est VESTIGIAL. Les deux calculs filtraient pourtant
`departement__annee_universitaire=annee`. Mesuré sur le VPS le 06/10/2026 :
0 EM pour 2026-2027 — tous pointent vers des groupes de 2022 à 2026 — alors
que 18 séances étaient marquées « Fait ». Les deux écrans restaient vides.
"""
import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401


def pointer(monde, em, type_='cm', fait=True, semaine=1, jour='Lundi',
            creneau='08h00-09h30', prof='Moustapha'):
    from apps.suivi.models import SuiviePointage
    return SuiviePointage.objects.create(
        annee_universitaire=ANNEE, type_semestre='I', numero_semaine=semaine,
        commentaire='Fait' if fait else 'Non fait', duree_creneau=1.5,
        em=monde['ems'][em], prof=monde['profs'][prof], type_seance_fk=monde[type_],
        jour_fk=monde['jours'][jour], creneau_fk=monde['creneaux'][creneau],
        institution=monde['inst'])


def codes(qs):
    return sorted(qs.values_list('code_em', flat=True))


class TestLesEmDeLAnnee:

    def test_derives_des_groupes_par_filiere_et_niveau(self, monde):
        from apps.avancement.ems_annee import ems_de_l_annee
        # G1/G2 (SEA, L1) → S1 ; SEA L2 → S3 ; SDID L2 → S3. Pairs exclus.
        assert codes(ems_de_l_annee(ANNEE, 'I')) == ['HE11', 'SDID31', 'SEA11', 'SEA12', 'SEA31']
        assert 'SEA24' in codes(ems_de_l_annee(ANNEE, 'P'))

    def test_le_groupe_vestigial_d_une_autre_annee_n_exclut_plus(self, monde):
        """Le cas du VPS : l'EM pointe vers un groupe de 2022-2023."""
        from apps.avancement.ems_annee import ems_de_l_annee
        from apps.departement.models import Departement
        ancien = Departement.objects.create(nom='G1', annee_universitaire='2022-2023',
                                            filiere=monde['f_sea'], niveau=monde['l1'],
                                            institution=monde['inst'])
        em = monde['ems']['SEA11']
        em.departement = ancien
        em.save(update_fields=['departement'])
        assert 'SEA11' in codes(ems_de_l_annee(ANNEE, 'I'))

    def test_une_annee_sans_groupe_n_a_pas_d_em(self, monde):
        from apps.avancement.ems_annee import ems_de_l_annee
        assert codes(ems_de_l_annee('2030-2031', 'I')) == []

    def test_un_em_enseigne_au_suivi_n_est_jamais_oublie(self, monde):
        """Un EM rattaché autrement (transversal…) mais enseigné cette année."""
        from apps.avancement.ems_annee import ems_de_l_annee
        from apps.em.models import EM
        from apps.suivi.models import Suivie
        orphelin = EM.objects.create(code_em='XX51', intitule='Hors filière',
                                     semestre=monde['s1'], institution=monde['inst'])
        assert 'XX51' not in codes(ems_de_l_annee(ANNEE, 'I'))
        Suivie.objects.create(annee_universitaire=ANNEE, type_semestre='I', numero_semaine=1,
                              em=orphelin, institution=monde['inst'])
        assert 'XX51' in codes(ems_de_l_annee(ANNEE, 'I'))


class TestAvancementEms:

    def test_les_seances_faites_apparaissent(self, monde, gens):
        em = monde['ems']['SEA11']
        em.CM = 3
        em.save(update_fields=['CM'])
        pointer(monde, 'SEA11')
        pointer(monde, 'SEA11', semaine=2, fait=False)          # « Non fait » : ignorée
        r = api(gens['admin']).get('/api/v1/avancement/em/',
                                   {'annee_universitaire': ANNEE, 'type_semestre': 'I'})
        assert r.status_code == 200, r.data
        ligne = next(l for l in r.data if l['code_em'] == 'SEA11')
        assert (ligne['plan_CM'], ligne['real_CM'], ligne['pct_CM']) == (3, 1.5, 50)
        assert {l['code_em'] for l in r.data} >= {'SEA11', 'SEA12', 'SEA31', 'SDID31'}

    def test_un_em_sans_seance_apparait_a_zero(self, monde, gens):
        r = api(gens['admin']).get('/api/v1/avancement/em/',
                                   {'annee_universitaire': ANNEE, 'type_semestre': 'I'})
        ligne = next(l for l in r.data if l['code_em'] == 'SEA12')
        assert ligne['real_CM'] == 0 and ligne['pct_CM'] == 0


class TestAvancementParSemestre:

    def test_le_graphique_n_est_plus_vide(self, monde, gens):
        em = monde['ems']['SEA11']
        em.CM = 3
        em.save(update_fields=['CM'])
        pointer(monde, 'SEA11')
        r = api(gens['admin']).get('/api/v1/avancement/semestres/',
                                   {'annee_universitaire': ANNEE, 'semestres': 'Impairs'})
        assert r.status_code == 200, r.data
        assert r.data['labels'] == ['S1', 'S3']
        # S1 : 1,5 h faites sur 3 h prévues (SEA11 seul a du CM prévu).
        assert r.data['progress_cm'][0] == 50.0

    def test_les_semestres_pairs(self, monde, gens):
        r = api(gens['admin']).get('/api/v1/avancement/semestres/',
                                   {'annee_universitaire': ANNEE, 'semestres': 'Pairs'})
        assert r.data['labels'] == ['S2']
