"""
Promouvoir une semaine réelle en patron — le sens inverse de la duplication.

Personne ne compose un patron à vide : six grilles sur onze le sont ici, pendant
que les semaines portent trente et une séances. Ce qui justifie le patron, c'est
qu'il n'appartient à aucun semestre : rempli une fois, il ressert l'année
suivante, là où une semaine meurt avec son année.

Ces tests figent les DEUX ÉCARTS assumés entre la semaine et le patron — une
annulation n'entre pas, une permutation revient à son titulaire — et les deux
refus motivés. Chacun tombe si l'on casse ce qu'il surveille.

La table `prof_type_history` qu'un signal réclame à la création d'un enseignant
est créée par la fixture de session de `tests/conftest.py` : rien à réinventer
ici.
"""
import datetime as dt

import pytest

from tests._edt_decor import ANNEE, api, gens, grille, monde  # noqa: F401

URL = '/api/v1/edt/grilles/%s/reprendre-semaine/'


@pytest.fixture
def semaine_paire(monde):
    """Une semaine 1 de l'AUTRE parité, pour le refus qui va avec."""
    from apps.parametres.models import Semaine

    base = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    for i, nom in enumerate(('Lundi', 'Mardi', 'Mercredi')):
        monde['semaines'][('P', 1, nom)] = Semaine.objects.create(
            numero_semaine=1, jour_fk=monde['jours'][nom],
            date=base + dt.timedelta(days=60 + i),
            annee_universitaire=ANNEE, type_semestre='P',
            type_semaine='cours')
    return monde


def poser(monde, dept, num, jour='Lundi', creneau='08h00-09h30',
          em='SEA11', prof='Moustapha', salle='101', type_seance='cm',
          origine='manuelle', annulee=False, prof_initial=None):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept],
        semaine=monde['semaines'][(num, jour)],
        creneau_fk=monde['creneaux'][creneau],
        em=monde['ems'][em], prof=monde['profs'][prof],
        salle=monde['salles'][salle], type_seance_fk=monde[type_seance],
        origine=origine, annulee=annulee,
        prof_initial=monde['profs'][prof_initial] if prof_initial else None)


def reprendre(user, g, numero, **extra):
    corps = {'semaine_source': numero}
    corps.update(extra)
    return api(user).post(URL % g.pk, corps, format='json')


def cases(g):
    from apps.edt.models import SeanceType
    return SeanceType.objects.filter(grille=g)


# ── 1. La semaine devient le patron ─────────────────────────────────────────

class TestPromotion:

    def test_la_semaine_devient_le_patron(self, monde, gens):
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, jour='Lundi', creneau='08h00-09h30')
        poser(monde, 'G1', 1, jour='Mardi', creneau='09h45-11h15', em='SEA12')

        r = reprendre(gens['admin'], g, 1)
        assert r.status_code == 200, r.data
        assert r.data['creees'] == 2
        assert cases(g).count() == 2

        posee = cases(g).get(jour_fk=monde['jours']['Lundi'])
        assert posee.em.code_em == 'SEA11'
        assert posee.prof.nom == 'Moustapha'
        assert posee.salle.nom == '101'

    def test_le_patron_ne_porte_aucune_date(self, monde, gens):
        """C'est ce qui lui permet de resservir l'année suivante : une case de
        patron n'a ni date, ni lien vers une semaine."""
        from apps.edt.models import SeanceType
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1)
        reprendre(gens['admin'], g, 1)

        champs = {f.name for f in SeanceType._meta.get_fields()}
        assert 'date' not in champs and 'semaine' not in champs
        # Ce qui l'ancre, c'est le JOUR du référentiel, pas un jour du calendrier.
        assert cases(g).get().jour_fk == monde['jours']['Lundi']

    def test_plusieurs_creneaux_d_un_meme_jour_font_plusieurs_cases(self, monde, gens):
        """La case du patron est unique par (grille, jour, créneau) — contrainte
        `uniq_edt_seance_type_case`. Deux séances du même jour à des heures
        différentes ne doivent pas s'écraser l'une l'autre.

        À l'ISS il n'y a PAS de sous-groupe dans la clé : deux séances ne
        peuvent pas partager un créneau, la contrainte de la séance réelle
        l'interdit déjà en amont. L'axe de distinction est donc (jour, créneau),
        et c'est lui qu'on surveille.
        """
        g = grille(monde, 'G1')
        for c in ('08h00-09h30', '09h45-11h15', '11h30-13h00'):
            poser(monde, 'G1', 1, jour='Lundi', creneau=c)

        r = reprendre(gens['admin'], g, 1)
        assert r.data['creees'] == 3
        assert cases(g).count() == 3
        assert cases(g).values('creneau_fk').distinct().count() == 3


# ── 2. Une séance annulée n'entre pas ───────────────────────────────────────

class TestAnnulees:

    def test_une_seance_annulee_n_entre_pas_dans_le_patron(self, monde, gens):
        """On reprend un emploi du temps, pas l'histoire de ses accidents."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, jour='Lundi')
        poser(monde, 'G1', 1, jour='Mardi', annulee=True)

        r = reprendre(gens['admin'], g, 1)
        assert r.data['creees'] == 1
        assert r.data['annulees_ecartees'] == 1
        assert cases(g).count() == 1
        assert cases(g).get().jour_fk == monde['jours']['Lundi']

    def test_une_semaine_entierement_annulee_est_refusee(self, monde, gens):
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, annulee=True)
        r = reprendre(gens['admin'], g, 1)
        assert r.status_code == 400
        assert 'annul' in str(r.data).lower()


# ── 4-5. La permutation revient à son titulaire ─────────────────────────────

class TestPermutations:

    def test_une_permutation_revient_au_titulaire_et_le_compte_le_dit(self, monde, gens):
        """Recopier le remplaçant graverait l'exception dans le modèle : il
        deviendrait titulaire pour toutes les années à venir. L'écarter est
        pire — le patron garderait un trou à chaque duplication future."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='permutation',
              prof='Abderahmane', prof_initial='Moustapha')

        r = reprendre(gens['admin'], g, 1)
        assert r.status_code == 200, r.data
        assert r.data['permutations_ramenees'] == 1
        assert cases(g).get().prof.nom == 'Moustapha'      # le TITULAIRE

    def test_une_permutation_sans_titulaire_connu_garde_l_enseignant_effectif(
            self, monde, gens):
        """On ne peut reprendre que ce qu'on voit. Prétendre avoir normalisé
        une permutation dont le titulaire est inconnu serait mentir."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='permutation',
              prof='Abderahmane', prof_initial=None)

        r = reprendre(gens['admin'], g, 1)
        assert r.data['permutations_ramenees'] == 0
        assert cases(g).get().prof.nom == 'Abderahmane'

    def test_une_seance_ordinaire_n_est_pas_comptee_comme_ramenee(self, monde, gens):
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='grille', prof='Abderahmane',
              prof_initial='Moustapha')       # titulaire renseigné, mais PAS une permutation
        r = reprendre(gens['admin'], g, 1)
        assert r.data['permutations_ramenees'] == 0
        assert cases(g).get().prof.nom == 'Abderahmane'


# ── 6-7. Ce que « le patron suit la semaine » commande ──────────────────────

class TestEcrasement:

    def _patron_compose(self, monde, g):
        from apps.edt.models import SeanceType
        return SeanceType.objects.create(
            grille=g, jour_fk=monde['jours']['Lundi'],
            creneau_fk=monde['creneaux']['08h00-09h30'],
            em=monde['ems']['SEA12'], prof=monde['profs']['Abderahmane'],
            salle=monde['salles']['102'], type_seance_fk=monde['cm'])

    def test_sans_ecraser_une_case_composee_a_la_main_ne_bouge_pas(self, monde, gens):
        """On complète un modèle sans défaire ce qu'on y a réglé."""
        g = grille(monde, 'G1')
        deja = self._patron_compose(monde, g)
        poser(monde, 'G1', 1)          # même jour, même créneau, autre contenu

        r = reprendre(gens['admin'], g, 1)
        assert r.status_code == 200, r.data
        assert (r.data['creees'], r.data['remplacees'], r.data['ignorees']) == (0, 0, 1)
        deja.refresh_from_db()
        assert deja.em.code_em == 'SEA12' and deja.prof.nom == 'Abderahmane'
        assert r.data['conflits'][0]['exemples'] == ['Lundi 08h00-09h30']

    def test_avec_ecraser_le_patron_suit_la_semaine(self, monde, gens):
        g = grille(monde, 'G1')
        deja = self._patron_compose(monde, g)
        poser(monde, 'G1', 1)

        r = reprendre(gens['admin'], g, 1, ecraser=True)
        assert (r.data['creees'], r.data['remplacees'], r.data['ignorees']) == (0, 1, 0)
        deja.refresh_from_db()          # la MÊME case, mise à jour
        assert deja.em.code_em == 'SEA11' and deja.prof.nom == 'Moustapha'
        assert cases(g).count() == 1


# ── 8-9. Deux refus motivés ─────────────────────────────────────────────────

class TestRefusMotives:

    def test_une_semaine_de_l_autre_parite_est_refusee(self, monde, gens, semaine_paire):
        """Le patron tient au TYPE de semestre : une semaine paire n'entre pas
        dans un patron impair."""
        from apps.edt.models import SeanceReelle
        g = grille(monde, 'G1', type_semestre='I')
        SeanceReelle.objects.create(
            departement=monde['depts']['G1'],
            semaine=monde['semaines'][('P', 1, 'Lundi')],
            creneau_fk=monde['creneaux']['08h00-09h30'],
            em=monde['ems']['SEA11'], prof=monde['profs']['Moustapha'],
            salle=monde['salles']['101'], type_seance_fk=monde['cm'])
        # La semaine 1 impaire existe aussi : on force la paire en supprimant
        # l'impaire, sinon la vue trouve d'abord celle de la bonne parité.
        from apps.parametres.models import Semaine
        Semaine.objects.filter(annee_universitaire=ANNEE, type_semestre='I',
                               numero_semaine=1).delete()

        r = reprendre(gens['admin'], g, 1)
        assert r.status_code == 400
        assert 'pair' in str(r.data).lower() and 'impair' in str(r.data).lower()
        assert cases(g).count() == 0

    def test_une_semaine_vide_est_refusee_avec_son_motif(self, monde, gens):
        g = grille(monde, 'G1')
        r = reprendre(gens['admin'], g, 1)
        assert r.status_code == 400
        assert 'aucune séance' in str(r.data).lower()
        assert 'G1' in str(r.data)

    def test_une_semaine_inexistante_est_refusee(self, monde, gens):
        g = grille(monde, 'G1')
        r = reprendre(gens['admin'], g, 99)
        assert r.status_code == 400
        assert '99' in str(r.data)

    def test_la_semaine_source_est_obligatoire(self, monde, gens):
        g = grille(monde, 'G1')
        r = api(gens['admin']).post(URL % g.pk, {}, format='json')
        assert r.status_code == 400


# ── Le périmètre ────────────────────────────────────────────────────────────

class TestPerimetre:

    def test_un_groupe_hors_perimetre_est_refuse(self, monde, gens):
        """`autre_de` ne gère ni G1 ni G2 : le patron de G1 ne lui est pas
        accessible."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1)
        r = reprendre(gens['autre_de'], g, 1)
        assert r.status_code in (403, 404)
        assert cases(g).count() == 0
