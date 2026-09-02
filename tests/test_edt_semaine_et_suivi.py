"""
De la semaine planifiée au suivi généré.

Le socle lit `emplois.Emplois`, table qui ne porte AUCUN numéro de semaine :
elle dit « lundi 08h00 », jamais quel lundi. Tant que son remplissage est un
geste humain distinct de la génération, deux pannes muettes restent possibles —
transmettre une semaine et en générer une autre, ou corriger après avoir
transmis. Aucune n'est détectable, puisque la vérification porterait sur une
information que la table ne contient pas.

La projection se fait donc dans la requête même qui génère. `apps/suivi/` n'est
pas modifié : sa vue est héritée et appelée telle quelle, seule l'URL est
captée en amont dans `siga/urls.py`.

Ces tests vérifient qu'elle prend la bonne semaine, l'état le plus récent, le
bon périmètre, et qu'elle ne laisse rien traîner derrière elle.
"""
from tests._edt_decor import (ANNEE, URL_SEANCE_TYPE, _droit, api,  # noqa: F401
                              case, gens, grille, monde)


def poser(monde, dept, semaine_num, jour='Lundi', creneau='08h00-09h30',
          em='SEA11', prof='Moustapha', salle='101'):
    """Une séance datée, écrite directement — on teste la génération, pas l'API."""
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept],
        semaine=monde['semaines'][(semaine_num, jour)],
        creneau_fk=monde['creneaux'][creneau],
        em=monde['ems'][em],
        prof=monde['profs'][prof],
        salle=monde['salles'][salle],
        type_seance_fk=monde['cm'],
        origine='grille')


def generer(user, semaine):
    return api(user).post('/api/v1/suivi/suivies/ajouter/', {
        'annee_universitaire': ANNEE, 'type_semestre': 'I',
        'numero_semaine': semaine}, format='json')


# ── 1. La projection est implicite ──────────────────────────────────────────

class TestGenerationProjetteDElleMeme:

    def test_aucun_geste_prealable_n_est_requis(self, monde, gens):
        """Le planificateur édite, le générateur génère. Rien entre les deux."""
        from apps.emplois.models import Emplois
        from apps.suivi.models import Suivie

        poser(monde, 'G1', 1, em='SEA11')
        assert Emplois.objects.count() == 0        # rien n'a été transmis

        r = generer(gens['admin'], 1)
        assert r.status_code == 200
        assert Suivie.objects.filter(numero_semaine=1,
                                     em__code_em='SEA11').exists()

    def test_une_correction_de_derniere_minute_est_prise(self, monde, gens):
        """Corriger après coup n'oblige à rien : il n'y a plus de « après »."""
        from apps.suivi.models import Suivie

        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        generer(gens['admin'], 1)

        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof'])

        generer(gens['admin'], 1)
        profs = set(Suivie.objects.filter(numero_semaine=1, em__code_em='SEA11')
                    .values_list('prof__nom', flat=True))
        assert profs == {'Abderahmane'}

    def test_seule_la_semaine_demandee_est_prise(self, monde, gens):
        """La panne muette d'avant : projeter la S1, générer la S2."""
        from apps.suivi.models import Suivie

        poser(monde, 'G1', 1, em='SEA11')
        poser(monde, 'G2', 2, em='SEA12', prof='Abderahmane', salle='102')

        assert generer(gens['admin'], 2).status_code == 200
        codes = set(Suivie.objects.filter(numero_semaine=2)
                    .exclude(em__isnull=True).values_list('em__code_em', flat=True))
        assert codes == {'SEA12'}

    def test_une_seance_annulee_n_est_pas_transmise(self, monde, gens):
        """Ni pointée, ni payée : c'est tout l'objet de l'annulation."""
        from apps.suivi.models import Suivie

        s = poser(monde, 'G1', 1, em='SEA11')
        s.annulee = True
        s.save(update_fields=['annulee'])

        r = generer(gens['admin'], 1)
        assert r.status_code in (200, 400)   # 400 s'il ne reste rien à générer
        assert not Suivie.objects.filter(numero_semaine=1,
                                         em__code_em='SEA11').exists()

    def test_l_enseignant_effectif_est_celui_qui_est_paye(self, monde, gens):
        """Après permutation, la charge suit le remplaçant, pas le remplacé."""
        from apps.suivi.models import Suivie

        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        s.prof_initial = monde['profs']['Moustapha']
        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof', 'prof_initial'])

        generer(gens['admin'], 1)
        assert set(Suivie.objects.filter(numero_semaine=1)
                   .exclude(prof__isnull=True)
                   .values_list('prof__nom', flat=True)) == {'Abderahmane'}

    def test_la_boite_de_transfert_ne_garde_rien(self, monde, gens):
        """`Emplois` est un tampon : rempli et vidé dans la même requête."""
        from apps.emplois.models import Emplois

        poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        assert Emplois.objects.count() == 0

    def test_le_message_ne_parle_plus_de_vider_l_edt(self, monde, gens):
        """« EDT vidé » annonçait une catastrophe qui n'avait pas lieu."""
        poser(monde, 'G1', 1, em='SEA11')
        r = generer(gens['admin'], 1)
        assert 'vid' not in r.data['message'].lower()
        assert 'séance' in r.data['message']


# ── 2. Le périmètre du générateur ───────────────────────────────────────────

class TestPerimetreDeGeneration:

    def test_le_de_ne_genere_que_ses_groupes(self, monde, gens):
        """Générer ne doit jamais écrire chez le voisin."""
        from apps.suivi.models import Suivie

        poser(monde, 'G1', 1, em='SEA11')
        poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane',
              creneau='09h45-11h15', salle='102')

        assert generer(gens['de'], 1).status_code == 200
        groupes = set(Suivie.objects.filter(numero_semaine=1)
                      .values_list('departement__nom', flat=True))
        assert groupes == {'G1'}

    def test_un_perimetre_vide_ne_genere_rien(self, monde, gens):
        poser(monde, 'G1', 1, em='SEA11')
        assert generer(gens['orphelin'], 1).status_code >= 400

    def test_la_projection_manuelle_reste_bornee_au_perimetre(self, monde, gens):
        """L'endpoint subsiste pour l'API ; il ne doit pas déborder."""
        from apps.emplois.models import Emplois

        poser(monde, 'G1', 1, em='SEA11')
        poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane',
              creneau='09h45-11h15', salle='102')

        r = api(gens['de']).post('/api/v1/edt/seances/projeter/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'numero_semaine': 1}, format='json')
        assert r.status_code == 200
        assert set(Emplois.objects.values_list('departement__nom', flat=True)) \
            == {'G1'}

    def test_projeter_sans_perimetre_est_refuse(self, monde, gens):
        """Un périmètre vide veut dire « rien », jamais « tout »."""
        poser(monde, 'G1', 1, em='SEA11')
        r = api(gens['orphelin']).post('/api/v1/edt/seances/projeter/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'numero_semaine': 1}, format='json')
        assert r.status_code == 403


# ── 3. La coexistence avec l'ancienne voie ──────────────────────────────────

class TestCoexistenceAvecLaSaisieManuelle:
    """Exigence du client : les deux voies d'alimentation cohabitent.

    L'écran actuel — `/dashboard/emplois/gerer` — écrit directement dans
    `Emplois`. Le nouveau moteur l'alimente par projection. Il faut vérifier
    explicitement qu'aucune des deux n'écrase l'autre par surprise.
    """

    def _saisie_manuelle(self, monde, dept, creneau='08h00-09h30'):
        """Une ligne écrite par l'écran actuel : pas de séance derrière elle."""
        from apps.emplois.models import Emplois
        return Emplois.objects.create(
            annee_universitaire=ANNEE, type_semestre='I',
            departement=monde['depts'][dept], em=monde['ems']['SDID31'],
            prof=monde['profs']['Moustapha'], salle=monde['salles']['101'],
            jour_fk=monde['jours']['Lundi'],
            creneau_fk=monde['creneaux'][creneau],
            type_seance_fk=monde['cm'], institution=monde['inst'])

    def test_un_groupe_non_bascule_n_est_jamais_purge(self, monde, gens):
        """Le groupe encore saisi à l'ancienne garde sa saisie.

        Sans cette garde, il suffisait de projeter pour effacer la saisie
        manuelle de TOUS les groupes du périmètre : la semaine paraissait vide
        et le suivi ne générait plus rien pour eux, sans un mot.
        """
        from apps.emplois.models import Emplois

        ancienne = self._saisie_manuelle(monde, 'SDID L2')
        r = api(gens['autre_de']).post('/api/v1/edt/seances/projeter/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'numero_semaine': 1}, format='json')
        assert r.status_code == 200
        assert r.data['projetees'] == 0
        assert Emplois.objects.filter(pk=ancienne.pk).exists(),             "un groupe sans aucune séance planifiée reste sur l'ancienne voie"

    def test_la_generation_du_suivi_ne_purge_pas_non_plus(self, monde, gens):
        """Le même risque, sur le chemin qui alimente la paie.

        La génération projette d'abord. Si cette projection purgeait le
        périmètre, le socle lirait ensuite une table vide pour les groupes non
        basculés : aucun pointage, aucune charge, aucune vacation.
        """
        from apps.suivi.models import Suivie

        self._saisie_manuelle(monde, 'SDID L2')
        r = api(gens['autre_de']).post('/api/v1/suivi/suivies/ajouter/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'numero_semaine': 1}, format='json')
        assert r.status_code == 200
        assert Suivie.objects.filter(numero_semaine=1,
                                     departement__nom='SDID L2').exists()

    def test_un_groupe_bascule_garde_sa_protection_contre_les_lignes_perimees(
            self, monde, gens):
        """Pour lui, la purge reste : sinon la semaine d'avant serait générée
        comme si elle était celle-ci."""
        from apps.emplois.models import Emplois

        perimee = self._saisie_manuelle(monde, 'SEA L2 G1',
                                        creneau='11h30-13h00')
        # Ce groupe a basculé : il porte une séance sur la période.
        poser(monde, 'SEA L2 G1', 1, em='SEA31', prof='Abderahmane',
              creneau='09h45-11h15', salle='102')

        r = api(gens['autre_de']).post('/api/v1/edt/seances/projeter/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'numero_semaine': 1}, format='json')
        assert r.status_code == 200
        assert not Emplois.objects.filter(pk=perimee.pk).exists()

    def test_la_projection_n_efface_pas_la_saisie_d_un_autre_groupe(
            self, monde, gens):
        """Le point le plus sensible de la coexistence.

        Le directeur des études planifie « G1 » avec le nouveau moteur pendant
        qu'un autre groupe reste saisi à l'ancienne. Projeter « G1 » ne doit
        rien retirer à l'autre.
        """
        from apps.emplois.models import Emplois

        ancienne = Emplois.objects.create(
            annee_universitaire=ANNEE, type_semestre='I',
            departement=monde['depts']['SDID L2'], em=monde['ems']['SDID31'],
            prof=monde['profs']['Abderahmane'], salle=monde['salles']['102'],
            jour_fk=monde['jours']['Lundi'],
            creneau_fk=monde['creneaux']['09h45-11h15'],
            type_seance_fk=monde['cm'], institution=monde['inst'])

        poser(monde, 'G1', 1, em='SEA11')
        r = api(gens['de']).post('/api/v1/edt/seances/projeter/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'numero_semaine': 1}, format='json')
        assert r.status_code == 200
        assert Emplois.objects.filter(pk=ancienne.pk).exists(), \
            "la saisie manuelle d'un autre groupe ne doit pas être purgée"

    def test_les_ecrans_du_socle_repondent_toujours(self, monde, gens):
        """Aucun écran ni endpoint du socle n'est retiré — §7 bis du brief."""
        c = api(gens['admin'])
        assert c.get('/api/v1/emplois/grille/').status_code == 200
        assert c.get('/api/v1/emplois/grille-all/', {
            'annee_universitaire': ANNEE,
            'type_semestre': 'I'}).status_code == 200
        assert c.post('/api/v1/emplois/check-dispo/', {
            'departement_id': monde['depts']['G1'].pk, 'jour': 'Lundi',
            'creneau_id': monde['creneaux']['08h00-09h30'].pk,
            'annee_universitaire': ANNEE}, format='json').status_code == 200
        assert c.get('/api/v1/emplois/count-scoped/', {
            'annee_universitaire': ANNEE,
            'type_semestre': 'I'}).status_code == 200
        # « Importer EDT depuis le suivi » : l'endpoint que l'ESP a perdu en
        # supprimant son écran. Ici l'écran reste, et l'endpoint répond.
        assert c.get('/api/v1/emplois/template-weeks/', {
            'annee_universitaire': ANNEE,
            'type_semestre': 'I'}).status_code == 200


# ── 4. L'archive : ce qui a servi ───────────────────────────────────────────

class TestArchive:

    def test_la_generation_fige_la_version_qui_a_servi(self, monde, gens):
        from apps.edt.models import EmploiArchive

        poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        assert EmploiArchive.objects.filter(numero_semaine=1).exists()

    def test_une_generation_refusee_ne_laisse_pas_d_archive(self, monde, gens):
        """Rien n'a servi : une prise de vue brouillerait le sélecteur."""
        from apps.edt.models import EmploiArchive

        r = generer(gens['admin'], 2)     # aucune séance en semaine 2
        assert r.status_code >= 400
        assert not EmploiArchive.objects.filter(numero_semaine=2).exists()

    def test_regenerer_cree_une_NOUVELLE_version_sans_ecraser(self, monde, gens):
        """C'est la raison d'être de l'écran « Historique » : comparer.

        Les heures ont été pointées sur une version précise, parfois payées.
        Une re-transmission ne doit donc jamais effacer celle d'avant.
        """
        from apps.edt.models import EmploiArchive

        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        generer(gens['admin'], 1)
        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof'])
        generer(gens['admin'], 1)

        versions = sorted(EmploiArchive.objects.filter(numero_semaine=1)
                          .values_list('version', flat=True).distinct())
        assert versions == [1, 2]
        gele = {v: set(EmploiArchive.objects.filter(numero_semaine=1, version=v)
                       .values_list('prof_nom', flat=True))
                for v in versions}
        assert gele[1] != gele[2], 'chaque version doit garder SON état'

    def test_l_archive_ne_suit_pas_le_referentiel(self, monde, gens):
        """Renommer une salle ne doit pas récrire le passé."""
        from apps.edt.models import EmploiArchive

        poser(monde, 'G1', 1, em='SEA11', salle='101')
        generer(gens['admin'], 1)

        salle = monde['salles']['101']
        salle.nom = 'Amphi rebaptisé'
        salle.save(update_fields=['nom'])

        noms = set(EmploiArchive.objects.filter(numero_semaine=1)
                   .values_list('salle_nom', flat=True))
        assert noms == {'101'}


# ── 5. Le patron et les semaines ────────────────────────────────────────────

class TestDuplicationDuPatron:
    """« Le patron dit ce qui devrait se passer, les semaines ce qui s'est
    passé. » Re-dupliquer ne doit jamais écraser une édition manuelle."""

    def _grille_remplie(self, monde, gens):
        g = grille(monde, 'G1')
        api(gens['admin']).post(URL_SEANCE_TYPE,
                                case(monde, g, em='SEA11'), format='json')
        return g

    def test_la_duplication_pose_le_patron_sur_les_semaines(self, monde, gens):
        from apps.edt.models import SeanceReelle

        g = self._grille_remplie(monde, gens)
        r = api(gens['admin']).post(f'/api/v1/edt/grilles/{g.pk}/dupliquer/',
                                    {'numeros': [1, 2]}, format='json')
        assert r.status_code == 200
        assert r.data['creees'] == 2
        assert SeanceReelle.objects.count() == 2

    def test_re_dupliquer_n_ecrase_pas_une_edition_manuelle(self, monde, gens):
        from apps.edt.models import SeanceReelle

        g = self._grille_remplie(monde, gens)
        c = api(gens['admin'])
        c.post(f'/api/v1/edt/grilles/{g.pk}/dupliquer/',
               {'numeros': [1]}, format='json')

        # Le planificateur corrige la semaine 1 à la main.
        s = SeanceReelle.objects.get()
        s.prof = monde['profs']['Abderahmane']
        s.origine = SeanceReelle.ORIGINE_MANUELLE
        s.save(update_fields=['prof', 'origine'])

        r = c.post(f'/api/v1/edt/grilles/{g.pk}/dupliquer/',
                   {'numeros': [1], 'ecraser': True}, format='json')
        assert r.status_code == 200
        assert r.data['ignorees'] == 1
        assert SeanceReelle.objects.get().prof.nom == 'Abderahmane'

    def test_une_semaine_hors_cours_ne_recoit_pas_le_patron(self, monde, gens):
        """Poser des séances sur des vacances les ferait facturer."""
        from apps.edt.models import SeanceReelle
        from apps.parametres.models import Semaine

        Semaine.objects.filter(numero_semaine=2).update(
            type_semaine=Semaine.TYPE_VACANCES)
        g = self._grille_remplie(monde, gens)
        r = api(gens['admin']).post(f'/api/v1/edt/grilles/{g.pk}/dupliquer/',
                                    {'numeros': [2]}, format='json')
        assert r.status_code == 400
        assert SeanceReelle.objects.count() == 0


# ── Portée de ce fichier ────────────────────────────────────────────────────
#
# Ces tests couvrent le SERVEUR et les contrats d'API que le navigateur
# consomme — les mêmes URL, les mêmes paramètres, les mêmes champs de réponse.
# Ils ne couvrent PAS le rendu : qu'un champ grisé le soit visiblement, qu'un
# message s'affiche au bon endroit, qu'un menu propose les bonnes entrées.
# Le projet n'a pas de harnais de navigateur (ni Playwright, ni Cypress) ; le
# poser serait un chantier à part.
