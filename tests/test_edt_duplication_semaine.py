"""
Recopier une semaine réelle sur d'autres semaines.

Le patron reste vide dans la vraie vie — six cases pour trente et une séances
réelles sur la base `iss`, et six grilles sur onze sans une seule case. Ce qu'on
bâtit, c'est la semaine 1 sur l'écran hebdomadaire ; ce qu'on veut, ce sont les
suivantes identiques.

Ces tests ne cherchent pas la couverture : chacun tombe si l'on casse ce qui
compte. Deux d'entre eux remplacent ceux qu'on écrirait ailleurs sur les
épreuves : à l'ISS, DS, EF et ER sont des TYPES DE SÉANCE du référentiel, posés
dans un créneau comme les autres. Aucun modèle de ce dépôt ne porte d'horaire
propre, donc aucun recouvrement partiel n'est possible — la protection d'une
épreuve passe par son origine, comme pour n'importe quelle séance.
"""
import datetime as dt

import pytest

from tests._edt_decor import ANNEE, api, gens, grille, monde  # noqa: F401

URL = '/api/v1/edt/grilles/%s/dupliquer/'


@pytest.fixture
def semaines_1_a_4(monde):
    """Quatre semaines de cours : deux ne suffisent pas à voir un lot."""
    from apps.parametres.models import Semaine

    base = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    for num in (3, 4):
        for i, nom in enumerate(('Lundi', 'Mardi', 'Mercredi')):
            monde['semaines'][(num, nom)] = Semaine.objects.create(
                numero_semaine=num, jour_fk=monde['jours'][nom],
                date=base + dt.timedelta(days=(num - 1) * 7 + i),
                annee_universitaire=ANNEE, type_semestre='I',
                type_semaine='cours')
    return monde


def poser(monde, dept, num, jour='Lundi', creneau='08h00-09h30',
          em='SEA11', prof='Moustapha', salle='101', type_seance='cm',
          origine='grille', seance_type=None):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept],
        semaine=monde['semaines'][(num, jour)],
        creneau_fk=monde['creneaux'][creneau],
        em=monde['ems'][em], prof=monde['profs'][prof],
        salle=monde['salles'][salle], type_seance_fk=monde[type_seance],
        origine=origine, seance_type=seance_type)


def recopier(user, g, source, **extra):
    corps = {'source': 'semaine', 'semaine_source': source}
    corps.update(extra)
    return api(user).post(URL % g.pk, corps, format='json')


def seances(monde, dept, num):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.filter(
        departement=monde['depts'][dept], semaine__numero_semaine=num)


# ── 1. L'origine de la copie ────────────────────────────────────────────────

class TestOrigineDeLaCopie:

    def test_la_copie_porte_sa_propre_origine_la_source_garde_la_sienne(self, monde, gens):
        """Hériter de l'origine de la source rouvrait le piège dans les deux sens :
        une copie « manuelle » devenait inécrasable, une copie « grille » mentait."""
        g = grille(monde, 'G1')
        src = poser(monde, 'G1', 1, origine='manuelle')

        r = recopier(gens['admin'], g, 1, numeros=[1, 2])
        assert r.status_code == 200, r.data
        assert r.data['creees'] == 1

        copie = seances(monde, 'G1', 2).get()
        assert copie.origine == 'recopie'
        src.refresh_from_db()
        assert src.origine == 'manuelle'

    def test_la_copie_ne_porte_aucun_lien_vers_une_case_de_patron(self, monde, gens):
        """C'est le critère structurel dont dépend le réétiquetage : une séance
        sans lien n'a pas été dupliquée DEPUIS LE PATRON."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        recopier(gens['admin'], g, 1, numeros=[1, 2])
        assert seances(monde, 'G1', 2).get().seance_type_id is None


class TestDefautDuChamp:

    def test_une_seance_creee_sans_origine_est_manuelle(self, monde):
        """Le défaut était « grille » : toute saisie à la main en héritait, et
        la promesse « les séances ajoutées à la main ne sont jamais écrasées »
        devenait fausse sur les séances mêmes qu'elle devait protéger."""
        from apps.edt.models import SeanceReelle
        s = SeanceReelle.objects.create(
            departement=monde['depts']['G1'],
            semaine=monde['semaines'][(1, 'Lundi')],
            creneau_fk=monde['creneaux']['08h00-09h30'],
            em=monde['ems']['SEA11'], prof=monde['profs']['Moustapha'],
            salle=monde['salles']['101'], type_seance_fk=monde['cm'])
        assert s.origine == 'manuelle'
        assert s.seance_type_id is None

    def test_le_critere_de_reetiquetage_est_structurel(self, monde):
        """C'est le critère de la migration 0002 : pas l'étiquette — elle est
        justement ce qu'on corrige — mais le LIEN vers une case de patron."""
        from apps.edt.models import SeanceReelle
        mal_etiquetee = poser(monde, 'G1', 1, origine='grille')   # sans lien
        assert mal_etiquetee.seance_type_id is None
        a_reetiqueter = SeanceReelle.objects.filter(
            origine='grille', seance_type__isnull=True)
        assert list(a_reetiqueter) == [mal_etiquetee]


# ── 2. La source n'est jamais sa propre cible ───────────────────────────────

class TestSourceJamaisCible:

    def test_la_semaine_source_est_ecartee_des_cibles(self, monde, gens):
        """Avec « écraser », la source s'effacerait puis se recréerait.

        La source porte ici l'origine « recopie », donc ÉCRASABLE : c'est la
        seule façon de surveiller ce garde-fou. Avec une source « manuelle », le
        test passait même garde-fou retiré — la séance était sauvée par la règle
        d'écrasement, pas par l'exclusion de la source. Un test qui réussit pour
        la mauvaise raison ne garde rien.
        """
        g = grille(monde, 'G1')
        src = poser(monde, 'G1', 1, origine='recopie')
        pk_origine = src.pk

        r = recopier(gens['admin'], g, 1, numeros=[1, 2], ecraser=True)
        assert r.status_code == 200, r.data
        assert r.data['semaines'] == [2]

        # La MÊME ligne, au même identifiant : ni supprimée, ni recréée.
        restante = seances(monde, 'G1', 1).get()
        assert restante.pk == pk_origine
        assert r.data['remplacees'] == 0

    def test_recopier_une_semaine_sur_elle_seule_est_refuse(self, monde, gens):
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1)
        r = recopier(gens['admin'], g, 1, numeros=[1])
        assert r.status_code == 400
        assert 'source' in str(r.data).lower()


# ── 3. Ce que « écraser » reprend, et ce qu'il épargne ──────────────────────

class TestEcrasement:

    def _quatre_cas(self, monde):
        """Quatre créneaux, quatre origines dans la semaine 2."""
        creneaux = ['08h00-09h30', '09h45-11h15', '11h30-13h00']
        for c in creneaux:
            poser(monde, 'G1', 1, creneau=c, origine='manuelle')
        poser(monde, 'G1', 2, creneau=creneaux[0], origine='grille')
        poser(monde, 'G1', 2, creneau=creneaux[1], origine='recopie')
        poser(monde, 'G1', 2, creneau=creneaux[2], origine='manuelle')
        return creneaux

    def test_sans_ecraser_rien_ne_bouge(self, monde, gens):
        g = grille(monde, 'G1')
        self._quatre_cas(monde)
        avant = {s.creneau_fk_id: s.pk for s in seances(monde, 'G1', 2)}

        r = recopier(gens['admin'], g, 1, numeros=[2])
        assert r.status_code == 200, r.data
        assert (r.data['creees'], r.data['remplacees'], r.data['ignorees']) == (0, 0, 3)
        assert {s.creneau_fk_id: s.pk for s in seances(monde, 'G1', 2)} == avant

    def test_avec_ecraser_seules_les_duplications_cedent(self, monde, gens):
        g = grille(monde, 'G1')
        creneaux = self._quatre_cas(monde)
        manuelle = seances(monde, 'G1', 2).get(
            creneau_fk=monde['creneaux'][creneaux[2]])

        r = recopier(gens['admin'], g, 1, numeros=[2], ecraser=True)
        assert r.status_code == 200, r.data
        # « grille » et « recopie » remplacées, « manuelle » épargnée.
        assert (r.data['creees'], r.data['remplacees'], r.data['ignorees']) == (2, 2, 1)

        manuelle.refresh_from_db()     # la MÊME ligne : jamais supprimée
        assert manuelle.origine == 'manuelle'
        assert set(seances(monde, 'G1', 2).values_list('origine', flat=True)) \
            == {'recopie', 'manuelle'}

    def test_une_permutation_survit_a_l_ecrasement(self, monde, gens):
        """Effacer un remplacement remettrait le titulaire — et ferait payer
        celui qui n'a pas assuré le cours."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        perm = poser(monde, 'G1', 2, origine='permutation', prof='Abderahmane')

        r = recopier(gens['admin'], g, 1, numeros=[2], ecraser=True)
        assert r.data['remplacees'] == 0 and r.data['ignorees'] == 1
        perm.refresh_from_db()
        assert perm.origine == 'permutation' and perm.prof.nom == 'Abderahmane'


# ── 4-5. Les évaluations, telles que ce dépôt les connaît ───────────────────

class TestEvaluations:

    def test_un_ds_saisi_a_la_main_bloque_sa_case_meme_avec_ecraser(self, monde, gens):
        """À l'ISS un DS est une séance du référentiel, dans un créneau. Ce qui
        le protège, c'est son origine — et « écraser » n'y change rien."""
        from apps.parametres.models import Seance
        ds = Seance.objects.create(type_seance='DS')
        monde['ds'] = ds
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        epreuve = poser(monde, 'G1', 2, type_seance='ds', origine='manuelle')

        r = recopier(gens['admin'], g, 1, numeros=[2], ecraser=True)
        assert (r.data['creees'], r.data['remplacees']) == (0, 0)
        epreuve.refresh_from_db()
        assert epreuve.type_seance_fk.type_seance == 'DS'
        assert "main" in r.data['conflits'][0]['motif']

    def test_une_seance_d_un_autre_groupe_ne_bloque_rien(self, monde, gens):
        """La case est unique par (groupe, semaine, créneau) : ce que fait G2 au
        même moment ne concerne pas la duplication de G1."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        poser(monde, 'G2', 2, origine='manuelle')

        r = recopier(gens['admin'], g, 1, numeros=[2])
        assert r.status_code == 200, r.data
        assert (r.data['creees'], r.data['ignorees']) == (1, 0)


# ── 6. Le bilan se lit ──────────────────────────────────────────────────────

class TestBilan:

    def test_deux_cases_bloquees_donnent_deux_libelles_distincts(self, monde, gens,
                                                                 semaines_1_a_4):
        """Sans le numéro de semaine dans le libellé, deux cases bloquées au
        même créneau rendaient deux lignes rigoureusement identiques — et le
        navigateur refusait la clé en double."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        poser(monde, 'G1', 2, origine='manuelle')
        poser(monde, 'G1', 3, origine='manuelle')

        r = recopier(gens['admin'], g, 1, numeros=[2, 3], ecraser=True)
        exemples = r.data['conflits'][0]['exemples']
        assert len(exemples) == 2
        assert len(set(exemples)) == 2, exemples
        assert exemples[0].startswith('S2 ·') and exemples[1].startswith('S3 ·')

    def test_un_motif_repete_est_compte_pas_repete(self, monde, gens, semaines_1_a_4):
        """Seize semaines bloquées pour la même raison donnaient seize lignes."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        for num in (2, 3, 4):
            poser(monde, 'G1', num, origine='manuelle')

        r = recopier(gens['admin'], g, 1, numeros=[2, 3, 4], ecraser=True)
        assert len(r.data['conflits']) == 1
        assert r.data['conflits'][0]['nombre'] == 3
        assert len(r.data['conflits'][0]['exemples']) <= 5

    def test_le_bilan_dit_d_abord_ce_qui_a_reussi(self, monde, gens, semaines_1_a_4):
        """La duplication est déjà enregistrée quand le bilan s'affiche."""
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, origine='manuelle')
        poser(monde, 'G1', 2, origine='manuelle')

        r = recopier(gens['admin'], g, 1, numeros=[2, 3, 4], ecraser=True)
        assert r.data['creees'] == 2          # semaines 3 et 4
        assert r.data['ignorees'] == 1        # semaine 2
        assert seances(monde, 'G1', 3).count() == 1
        assert seances(monde, 'G1', 4).count() == 1


# ── 7. Refuser plutôt que rendre zéro ───────────────────────────────────────

class TestRefusPlutotQueZero:

    def test_une_semaine_source_vide_est_refusee_avec_son_motif(self, monde, gens):
        g = grille(monde, 'G1')
        r = recopier(gens['admin'], g, 1, numeros=[2])
        assert r.status_code == 400
        assert 'aucune séance' in str(r.data).lower()

    def test_un_patron_vide_est_refuse_avec_son_motif(self, monde, gens):
        """« 0 séance créée sur 0 semaine » ressemble à une panne."""
        g = grille(monde, 'G1')
        r = api(gens['admin']).post(URL % g.pk, {'source': 'patron'}, format='json')
        assert r.status_code == 400
        assert 'vide' in str(r.data).lower()

    def test_une_semaine_source_inexistante_est_refusee(self, monde, gens):
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1)
        r = recopier(gens['admin'], g, 99, numeros=[2])
        assert r.status_code == 400
        assert '99' in str(r.data)


# ── Le patron continue de fonctionner ───────────────────────────────────────

class TestPatronInchange:

    def test_le_patron_pose_ses_cases_et_son_origine(self, monde, gens):
        from apps.edt.models import SeanceType
        g = grille(monde, 'G1')
        SeanceType.objects.create(
            grille=g, jour_fk=monde['jours']['Lundi'],
            creneau_fk=monde['creneaux']['08h00-09h30'],
            em=monde['ems']['SEA11'], prof=monde['profs']['Moustapha'],
            salle=monde['salles']['101'], type_seance_fk=monde['cm'])

        r = api(gens['admin']).post(URL % g.pk, {'source': 'patron'}, format='json')
        assert r.status_code == 200, r.data
        assert r.data['creees'] == 2       # semaines 1 et 2
        posee = seances(monde, 'G1', 1).get()
        assert posee.origine == 'grille' and posee.seance_type_id is not None

    def test_une_source_inconnue_est_refusee(self, monde, gens):
        g = grille(monde, 'G1')
        r = api(gens['admin']).post(URL % g.pk, {'source': 'lune'}, format='json')
        assert r.status_code == 400
