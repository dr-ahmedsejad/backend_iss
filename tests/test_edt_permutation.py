"""
Permuter deux enseignants depuis la grille.

Le geste vient d'IPGEI ; le circuit d'approbation, non — l'ISS n'a qu'un
planificateur. Ce que ces tests verrouillent, c'est ce qui touche à la paie :
l'enseignant effectif change, le titulaire est retenu une fois pour toutes, et
rien ne se fait à moitié.
"""
from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL = '/api/v1/edt/seances/permuter/'


def poser(monde, dept, num, jour='Lundi', creneau='08h00-09h30',
          em='SEA11', prof='Moustapha', salle='101'):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept],
        semaine=monde['semaines'][(num, jour)],
        creneau_fk=monde['creneaux'][creneau],
        em=monde['ems'][em], prof=monde['profs'][prof],
        salle=monde['salles'][salle], type_seance_fk=monde['cm'],
        origine='grille')


def permuter(user, a, b, **extra):
    return api(user).post(URL, {'seance_a': a.pk, 'seance_b': b.pk, **extra},
                          format='json')


class TestEchange:

    def test_enseignant_salle_et_element_s_echangent_le_creneau_reste(self, monde, gens):
        a = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha',   salle='101')
        b = poser(monde, 'G2', 1, em='SEA12', prof='Abderahmane', salle='102')

        r = permuter(gens['de'], a, b)
        assert r.status_code == 200, r.data
        assert r.data['seances_impactees'] == 2

        a.refresh_from_db(); b.refresh_from_db()
        assert (a.prof.nom, a.salle.nom, a.em.code_em) == ('Abderahmane', '102', 'SEA12')
        assert (b.prof.nom, b.salle.nom, b.em.code_em) == ('Moustapha',   '101', 'SEA11')
        # Le créneau, lui, n'a pas bougé.
        assert a.creneau_fk == b.creneau_fk == monde['creneaux']['08h00-09h30']

    def test_le_titulaire_est_retenu_et_l_origine_posee(self, monde, gens):
        """C'est ce qui fait payer le remplaçant, pas le titulaire."""
        a = poser(monde, 'G1', 1, prof='Moustapha')
        b = poser(monde, 'G2', 1, prof='Abderahmane', em='SEA12', salle='102')
        permuter(gens['de'], a, b)

        a.refresh_from_db(); b.refresh_from_db()
        assert a.prof_initial.nom == 'Moustapha'
        assert b.prof_initial.nom == 'Abderahmane'
        assert a.origine == b.origine == 'permutation'

    def test_un_second_echange_n_efface_pas_le_premier_titulaire(self, monde, gens):
        a = poser(monde, 'G1', 1, prof='Moustapha')
        b = poser(monde, 'G2', 1, prof='Abderahmane', em='SEA12', salle='102')
        permuter(gens['de'], a, b)
        permuter(gens['de'], a, b)          # retour à la situation de départ

        a.refresh_from_db()
        assert a.prof.nom == 'Moustapha'            # revenu
        assert a.prof_initial.nom == 'Moustapha'    # jamais réécrit

    def test_le_motif_est_conserve_sur_les_deux_seances(self, monde, gens):
        a = poser(monde, 'G1', 1)
        b = poser(monde, 'G2', 1, prof='Abderahmane', em='SEA12', salle='102')
        permuter(gens['de'], a, b, motif='Mission à Nouadhibou')
        a.refresh_from_db(); b.refresh_from_db()
        assert 'Mission à Nouadhibou' in a.observations
        assert 'Mission à Nouadhibou' in b.observations


class TestLot:

    def test_le_lot_ne_touche_que_les_semaines_ou_les_deux_existent(self, monde, gens):
        """Semaine 2 : G1 a sa séance, G2 non. Un échange à moitié n'en est pas un."""
        a1 = poser(monde, 'G1', 1)
        b1 = poser(monde, 'G2', 1, prof='Abderahmane', em='SEA12', salle='102')
        a2 = poser(monde, 'G1', 2)

        r = permuter(gens['de'], a1, b1, nb_semaines=2)
        assert r.status_code == 200, r.data
        assert r.data['seances_impactees'] == 2

        a2.refresh_from_db()
        assert a2.prof.nom == 'Moustapha' and a2.origine == 'grille'

    def test_le_lot_couvre_la_semaine_suivante_quand_elle_est_complete(self, monde, gens):
        a1 = poser(monde, 'G1', 1)
        b1 = poser(monde, 'G2', 1, prof='Abderahmane', em='SEA12', salle='102')
        a2 = poser(monde, 'G1', 2)
        b2 = poser(monde, 'G2', 2, prof='Abderahmane', em='SEA12', salle='102')

        r = permuter(gens['de'], a1, b1, nb_semaines=2)
        assert r.data['seances_impactees'] == 4
        a2.refresh_from_db(); b2.refresh_from_db()
        assert a2.prof.nom == 'Abderahmane' and b2.prof.nom == 'Moustapha'


class TestRefus:

    def test_deux_creneaux_differents_sont_refuses(self, monde, gens):
        a = poser(monde, 'G1', 1, creneau='08h00-09h30')
        b = poser(monde, 'G2', 1, creneau='09h45-11h15', prof='Abderahmane')
        r = permuter(gens['de'], a, b)
        assert r.status_code == 400
        a.refresh_from_db()
        assert a.prof.nom == 'Moustapha'

    def test_deux_semaines_differentes_sont_refusees(self, monde, gens):
        a = poser(monde, 'G1', 1)
        b = poser(monde, 'G2', 2, prof='Abderahmane')
        assert permuter(gens['de'], a, b).status_code == 400

    def test_deux_annees_d_etude_differentes_sont_refusees(self, monde, gens):
        """G1 est en L1, « SEA L2 - G1 » en L2 : même filière, pas la même promotion."""
        a = poser(monde, 'G1', 1)
        b = poser(monde, 'SEA L2 G1', 1, em='SEA31', prof='Abderahmane', salle='102')
        r = permuter(gens['admin'], a, b)
        assert r.status_code == 400
        a.refresh_from_db()
        assert a.prof.nom == 'Moustapha'

    def test_deux_filieres_differentes_sont_refusees(self, monde, gens):
        a = poser(monde, 'SEA L2 G1', 1, em='SEA31')
        b = poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane', salle='102')
        assert permuter(gens['admin'], a, b).status_code == 400

    def test_un_element_d_une_autre_filiere_bloque_l_echange(self, monde, gens):
        """G1 et G2 sont de la même filière, mais G1 porte un élément de SDID :
        l'échange le promènerait jusqu'à G2. C'est l'élément « perdu »."""
        a = poser(monde, 'G1', 1, em='SDID31')      # élément SDID sur un groupe SEA
        b = poser(monde, 'G2', 1, em='SEA12', prof='Abderahmane', salle='102')
        r = permuter(gens['de'], a, b)
        assert r.status_code == 400
        assert 'SDID31' in str(r.data)
        b.refresh_from_db()
        assert b.em.code_em == 'SEA12' and b.prof.nom == 'Abderahmane'

    def test_un_groupe_sans_filiere_ne_permute_pas(self, monde, gens):
        """HE et ST sont transversaux : pas de promotion, rien à échanger."""
        a = poser(monde, 'HE', 1, em='HE11')
        b = poser(monde, 'ST', 1, em='ST11', prof='Abderahmane', salle='102')
        assert permuter(gens['de'], a, b).status_code == 400

    def test_une_seance_annulee_ne_se_permute_pas(self, monde, gens):
        a = poser(monde, 'G1', 1)
        b = poser(monde, 'G2', 1, prof='Abderahmane')
        b.annulee = True
        b.save(update_fields=['annulee'])
        assert permuter(gens['de'], a, b).status_code == 400

    def test_hors_perimetre_rien_ne_bouge(self, monde, gens):
        """L'autre directeur ne gère ni G1 ni G2 : les séances lui sont invisibles."""
        a = poser(monde, 'G1', 1)
        b = poser(monde, 'G2', 1, prof='Abderahmane')
        r = permuter(gens['autre_de'], a, b)
        assert r.status_code in (400, 403, 404)
        a.refresh_from_db()
        assert a.prof.nom == 'Moustapha' and a.origine == 'grille'

    def test_l_admin_permute_sans_perimetre(self, monde, gens):
        a = poser(monde, 'G1', 1)
        b = poser(monde, 'G2', 1, prof='Abderahmane', em='SEA12', salle='102')
        assert permuter(gens['admin'], a, b).status_code == 200
