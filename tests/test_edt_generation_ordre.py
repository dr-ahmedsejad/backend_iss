"""
Le suivi se génère dans l'ordre, et se refait en reculant.

Régénérer la semaine 1 quand la 2 existe réécrirait un pointage sur lequel la
suite s'appuie — absences, rattrapages, vacations — sans que rien ne le dise.
La règle vit dans la capture de `suivies/ajouter/` (apps/edt/generation.py) :
le socle n'est pas modifié.
"""
from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401

URL_GENERER   = '/api/v1/suivi/suivies/ajouter/'
URL_SUPPRIMER = '/api/v1/suivi/suivies/par-semaine/'


def poser(monde, dept, semaine_num, em='SEA11', prof='Moustapha', salle='101'):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept],
        semaine=monde['semaines'][(semaine_num, 'Lundi')],
        creneau_fk=monde['creneaux']['08h00-09h30'],
        em=monde['ems'][em], prof=monde['profs'][prof],
        salle=monde['salles'][salle], type_seance_fk=monde['cm'],
        origine='grille')


def generer(user, semaine):
    return api(user).post(URL_GENERER, {
        'annee_universitaire': ANNEE, 'type_semestre': 'I',
        'numero_semaine': semaine}, format='json')


def supprimer(user, semaine):
    return api(user).delete(
        f'{URL_SUPPRIMER}?numero_semaine={semaine}'
        f'&annee_universitaire={ANNEE}&type_semestre=I&force=1')


class TestOrdreDeGeneration:

    def test_la_semaine_1_ne_se_regenere_plus_une_fois_la_2_generee(self, monde, gens):
        poser(monde, 'G1', 1)
        poser(monde, 'G1', 2)
        assert generer(gens['admin'], 1).status_code == 200
        assert generer(gens['admin'], 2).status_code == 200

        r = generer(gens['admin'], 1)
        assert r.status_code == 409, r.data
        assert r.data['semaines_posterieures'] == [2]
        assert 'supprimez' in r.data['detail'].lower()

    def test_une_semaine_jamais_generee_est_aussi_bloquee_par_la_suite(self, monde, gens):
        """Générer la 2 puis vouloir la 1 pour la première fois : même refus.
        Le trou ne se comble pas par-dessous."""
        poser(monde, 'G1', 1)
        poser(monde, 'G1', 2)
        assert generer(gens['admin'], 2).status_code == 200
        assert generer(gens['admin'], 1).status_code == 409

    def test_supprimer_la_2_rouvre_la_1(self, monde, gens):
        poser(monde, 'G1', 1)
        poser(monde, 'G1', 2)
        generer(gens['admin'], 1)
        generer(gens['admin'], 2)
        assert generer(gens['admin'], 1).status_code == 409

        r = supprimer(gens['admin'], 2)
        assert r.status_code < 300, getattr(r, 'data', r.content)

        assert generer(gens['admin'], 1).status_code == 200

    def test_la_derniere_semaine_se_regenere_librement(self, monde, gens):
        """C'est en reculant qu'on corrige : la dernière n'a rien après elle."""
        poser(monde, 'G1', 1)
        poser(monde, 'G1', 2)
        generer(gens['admin'], 1)
        generer(gens['admin'], 2)
        assert generer(gens['admin'], 2).status_code == 200

    def test_le_perimetre_d_un_autre_ne_bloque_pas(self, monde, gens):
        """L'autre directeur a généré la 2 sur SES groupes : le nôtre refait
        sa 1 sans être gêné — la règle est bornée au périmètre de l'appelant."""
        from apps.suivi.models import Suivie
        poser(monde, 'G1', 1)
        assert generer(gens['de'], 1).status_code == 200
        # La semaine 2 de l'autre directeur, écrite directement : on teste la
        # borne, pas sa génération.
        Suivie.objects.create(
            annee_universitaire=ANNEE, type_semestre='I', numero_semaine=2,
            commentaire='Non fait', departement=monde['depts']['SDID L2'],
            institution=monde['inst'], duree_creneau=1.5, taux_paiement=0)
        assert generer(gens['de'], 1).status_code == 200
