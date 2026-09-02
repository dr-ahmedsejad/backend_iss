"""
Diagnostic de rentrée : où en est le rattachement des réinscrits ?

Ce que ces tests verrouillent, c'est la capacité à voir un écart que rien ne
signale aujourd'hui. Sur 2026-2027, cent étudiants ont leur inscription
administrative et aucun n'est rattaché à un groupe de l'année — pourtant l'écran
des progressions affiche « exécutées ».

Le piège principal a sa propre vérification : un groupe qui existe pour une
AUTRE année ne compte pas. C'est exactement la situation réelle — les cent
réinscrits sont tous rattachés à un conteneur de 2024-2025 ou 2025-2026.
"""
import pytest
from rest_framework.test import APIClient

from tests.factories.auth import UserFactory
from tests.factories.parametres import InstitutionFactory, YearFactory
from tests.factories.scolarite import FiliereFactory

URL = '/api/v1/inscriptions/rentree/'


@pytest.fixture(autouse=True)
def _vider_le_cache_rbac():
    """Le cache RBAC est par PROCESSUS, pas par test.

    `tests/conftest.py` porte déjà cette purge, mais un conftest ne s'applique
    qu'à son propre dossier : les tests placés sous `apps/` n'en bénéficient
    pas. Sans elle, un utilisateur autorisé dans un test précédent laisse une
    entrée `rbac:<pk>:…` que le rollback de la base ne retire pas — et comme
    sqlite réattribue les mêmes identifiants, un autre compte hérite du droit.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _droit(role, module_code, action_code):
    from apps.authentication.models import (Action, Module, ModuleAction,
                                            RoleDefault)
    mod, _ = Module.objects.get_or_create(
        code=module_code, defaults={'nom': module_code})
    act, _ = Action.objects.get_or_create(
        code=action_code, defaults={'nom': action_code})
    ma, _ = ModuleAction.objects.get_or_create(module=mod, action=act)
    RoleDefault.objects.update_or_create(
        role=role, module_action=ma, defaults={'allowed': True})


@pytest.fixture
def monde(db):
    """Une année à préparer, calquée sur 2026-2027.

    Trois cohortes : une sans aucun groupe, une avec un groupe vide, une avec
    deux groupes. Plus un groupe que personne ne réclame, et un conteneur qui
    ne doit jamais passer pour une cible.
    """
    from apps.absence.models import Etudiant
    from apps.departement.models import Departement
    from apps.inscriptions.models import InscriptionAdministrative
    from apps.parametres.models import Niveau

    inst      = InstitutionFactory(acronyme='ISS', est_principale=True)
    precedent = YearFactory(annee='2025-2026')
    cible     = YearFactory(annee='2026-2027')

    l1 = Niveau.objects.create(niveau='L1')
    l2 = Niveau.objects.create(niveau='L2')
    l3 = Niveau.objects.create(niveau='L3')

    lpsea = FiliereFactory(code='LPSEA', intitule_fr='Statistiques, Economie')
    stat  = FiliereFactory(code='STAT',  intitule_fr='Statistique')
    sea   = FiliereFactory(code='SEA',   intitule_fr='Statistique et Economie')

    def groupe(nom, filiere, niveau, annee, container=False):
        return Departement.objects.create(
            nom=nom, annee_universitaire=annee, institution=inst,
            filiere=filiere, niveau=niveau, groupe='', is_container=container)

    # Le conteneur de l'an dernier : c'est là que tout le monde est resté.
    ancien = groupe('STAT_25-26', stat, l1, '2025-2026', container=True)

    depts = {
        # LPSEA L2 : AUCUN groupe pour l'année cible — la cohorte bloquée.
        # Un groupe LPSEA L2 existe pour l'année PRÉCÉDENTE : il ne doit pas
        # compter.
        'lpsea2_ancien': groupe('LPSEA L2', lpsea, l2, '2025-2026'),
        # STAT L1 : deux groupes, comme une promotion scindée en TD.
        'stat_g1': groupe('G1', stat, l1, '2026-2027'),
        'stat_g2': groupe('G2', stat, l1, '2026-2027'),
        # SEA L3 : un seul groupe.
        'sea':     groupe('SEA', sea, l3, '2026-2027'),
        # Un groupe que personne ne réclame.
        'orphelin': groupe('G1', lpsea, l1, '2026-2027'),
        # Un conteneur de l'année cible : jamais une cible d'affectation.
        'conteneur': groupe('STAT L1', stat, l1, '2026-2027', container=True),
    }

    compteur = {'n': 0}

    def inscrire(filiere, niveau, groupe_actuel):
        compteur['n'] += 1
        mat = f'M{compteur["n"]:04d}'
        etu = Etudiant.objects.create(
            matricule=mat, nom=f'Etu{mat}', nom_fr=f'Etu{mat}', prenom_fr='Test',
            genre='M', email=f'{mat}@test.mr', nationalite_fr='Mauritanienne',
            departement=groupe_actuel)
        return InscriptionAdministrative.objects.create(
            etudiant=etu, annee_univ=cible, filiere=filiere, niveau=niveau,
            institution=inst, numero_inscription=f'INS-{mat}')

    # LPSEA L2 : 3 inscrits, aucun groupe cible → sans_groupe
    for _ in range(3):
        inscrire(lpsea, 2, ancien)
    # STAT L1 : 4 inscrits, deux groupes, personne d'affecté → a_affecter
    for _ in range(4):
        inscrire(stat, 1, ancien)
    # SEA L3 : 2 inscrits, un groupe, un seul affecté → partiel
    inscrire(sea, 3, depts['sea'])
    inscrire(sea, 3, ancien)

    return dict(inst=inst, cible=cible, precedent=precedent, depts=depts,
                lpsea=lpsea, stat=stat, sea=sea, ancien=ancien,
                l1=l1, l2=l2, l3=l3, inscrire=inscrire)


@pytest.fixture
def client_scolarite(monde):
    for action in ('voir', 'modifier'):
        _droit('scolarite', 'insc_progression', action)
    c = APIClient()
    c.force_authenticate(user=UserFactory(username='u_scol', role='scolarite'))
    return c


def _par_filiere(reponse):
    return {c['filiere']['code'] + ' ' + c['niveau_code']: c
            for c in reponse.data['cohortes']}


# ── Les quatre états ────────────────────────────────────────────────────────

class TestEtatsDesCohortes:

    def test_une_cohorte_sans_groupe_est_signalee(self, client_scolarite, monde):
        """Le cas qui bloque tout : des inscrits, nulle part où les mettre."""
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        assert r.status_code == 200
        c = _par_filiere(r)['LPSEA L2']
        assert c['etat'] == 'sans_groupe'
        assert c['effectif'] == 3
        assert c['groupes'] == []

    def test_un_groupe_d_une_AUTRE_annee_ne_compte_pas(self, client_scolarite, monde):
        """Le piège principal, et la situation réelle de l'ISS.

        « LPSEA L2 » existe — pour 2025-2026. La cohorte de 2026-2027 n'a
        toujours nulle part où aller.
        """
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        assert _par_filiere(r)['LPSEA L2']['etat'] == 'sans_groupe'

    def test_un_groupe_vide_donne_a_affecter(self, client_scolarite, monde):
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        c = _par_filiere(r)['STAT L1']
        assert c['etat'] == 'a_affecter'
        assert c['effectif'] == 4 and c['affectes'] == 0
        assert sorted(g['nom'] for g in c['groupes']) == ['G1', 'G2']

    def test_une_affectation_commencee_donne_partiel(self, client_scolarite, monde):
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        c = _par_filiere(r)['SEA L3']
        assert c['etat'] == 'partiel'
        assert (c['effectif'], c['affectes']) == (2, 1)

    def test_une_cohorte_a_zero_inscrit_est_complete(self, client_scolarite, monde):
        """L'ordre des tests d'état compte.

        Zéro inscrit satisfait « personne d'affecté » autant que « rien à
        faire ». Tester le second en premier évite d'annoncer une cohorte vide
        comme restant à traiter — et un bouton « Affecter les 0 ».
        """
        from apps.inscriptions.views_rentree import _etat
        assert _etat(effectif=0, affectes=0, nb_groupes=1) == 'complet'
        assert _etat(effectif=0, affectes=0, nb_groupes=0) == 'sans_groupe'

    def test_tout_affecte_donne_complet(self, client_scolarite, monde):
        from apps.absence.models import Etudiant
        Etudiant.objects.filter(inscriptions_admin__filiere=monde['sea']).update(
            departement=monde['depts']['sea'])

        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        c = _par_filiere(r)['SEA L3']
        assert c['etat'] == 'complet'
        assert c['affectes'] == c['effectif']


# ── Ce qui ne doit PAS être compté ──────────────────────────────────────────

class TestCiblesLegitimes:

    def test_un_conteneur_n_est_jamais_une_cible(self, client_scolarite, monde):
        """« STAT L1 » est un conteneur d'inscription pour l'année cible : il
        reçoit les étudiants à l'inscription, il n'est pas la classe où l'on
        suit les cours."""
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        noms = {g['nom'] for g in _par_filiere(r)['STAT L1']['groupes']}
        assert noms == {'G1', 'G2'}
        assert 'STAT L1' not in noms

    def test_un_etudiant_reste_dans_un_conteneur_n_est_pas_rattache(
            self, client_scolarite, monde):
        """Le conteneur est l'endroit où l'on atterrit AVANT d'être réparti.

        Découvert sur une instance voisine : deux inscrits en IG L1, un dans le
        vrai groupe, l'autre encore dans le conteneur d'admission — et la
        cohorte s'affichait « complet ». Le travail restant devenait invisible.
        """
        from apps.absence.models import Etudiant
        from apps.departement.models import Departement

        conteneur = Departement.objects.create(
            nom='SEA_26_27', annee_universitaire='2026-2027',
            institution=monde['inst'], filiere=monde['sea'],
            niveau=monde['l3'], groupe='', is_container=True)
        # Les deux inscrits de SEA L3 : un dans le vrai groupe, un au conteneur.
        etus = list(Etudiant.objects.filter(inscriptions_admin__filiere=monde['sea']))
        etus[0].departement = monde['depts']['sea']
        etus[0].save(update_fields=['departement'])
        etus[1].departement = conteneur
        etus[1].save(update_fields=['departement'])

        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        c = _par_filiere(r)['SEA L3']
        assert c['affectes'] == 1, "le conteneur ne doit pas compter comme rattachement"
        assert c['etat'] == 'partiel'

    def test_les_groupes_sans_effectif_sont_listes(self, client_scolarite, monde):
        """Pas une erreur — mais on veut le savoir en cherchant pourquoi une
        cohorte n'a nulle part où aller."""
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        orphelins = {(g['filiere_code'], g['niveau_code'])
                     for g in r.data['groupes_sans_effectif']}
        assert ('LPSEA', 'L1') in orphelins

    def test_un_conteneur_ne_figure_pas_parmi_les_orphelins(self, client_scolarite, monde):
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        assert all(g['nom'] != 'STAT L1' for g in r.data['groupes_sans_effectif'])


# ── Totaux et calendrier ────────────────────────────────────────────────────

class TestTotaux:

    def test_les_totaux_recouvrent_les_cohortes(self, client_scolarite, monde):
        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        assert r.data['total_inscrits'] == 9      # 3 + 4 + 2
        assert r.data['total_affectes'] == 1
        assert sum(c['effectif'] for c in r.data['cohortes']) == 9

    def test_le_calendrier_est_rapporte_en_semaines(self, client_scolarite, monde):
        """Une ligne `Semaine` est un JOUR : on compte les semaines, pas les
        lignes, sinon 18 semaines s'annoncent comme 108."""
        import datetime as dt

        from apps.parametres.models import Jour, Semaine

        assert client_scolarite.get(
            URL, {'annee': monde['cible'].pk}).data['semaines_saisies'] == 0

        lundi = Jour.objects.create(jour='Lundi')
        mardi = Jour.objects.create(jour='Mardi')
        base  = dt.date(2026, 10, 5)
        for num in (1, 2):
            for i, jour in enumerate((lundi, mardi)):
                Semaine.objects.create(
                    numero_semaine=num, jour_fk=jour,
                    date=base + dt.timedelta(days=(num - 1) * 7 + i),
                    annee_universitaire='2026-2027', type_semestre='I',
                    type_semaine=Semaine.TYPE_COURS)

        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        assert r.data['semaines_saisies'] == 2


class TestReferentielIndependantDuPrefixe:
    """Le rapprochement niveau ↔ groupe ne présume pas du préfixe.

    L'ISS nomme ses niveaux « L1, L2, L3 » ; d'autres instances du même code les
    nomment « E1, E2, E3 », et l'une d'elles « MP » / « MPSI ». Écrire
    `f'L{n}'` marcherait ici et nulle part ailleurs.
    """

    def test_le_chiffre_se_lit_quel_que_soit_le_prefixe(self):
        from apps.inscriptions.views_rentree import _annee_etude_du_libelle
        assert _annee_etude_du_libelle('L3') == 3
        assert _annee_etude_du_libelle('E3') == 3
        assert _annee_etude_du_libelle('M1') == 1
        assert _annee_etude_du_libelle(' L 2 ') == 2

    def test_un_libelle_sans_annee_ne_designe_aucune_annee_d_etude(self):
        from apps.inscriptions.views_rentree import _annee_etude_du_libelle
        for libelle in ('Transversal', 'MP', 'MPSI', '', None):
            assert _annee_etude_du_libelle(libelle) is None

    def test_un_groupe_nomme_a_l_esp_est_bien_rapproche(self, client_scolarite, monde):
        """Un référentiel en « E » doit fonctionner sans toucher au code."""
        from apps.departement.models import Departement
        from apps.parametres.models import Niveau

        # On renomme L2 en E2 : la cohorte LPSEA L2 doit rester rapprochee.
        Niveau.objects.filter(pk=monde['l2'].pk).update(niveau='E2')
        Departement.objects.create(
            nom='LPSEA E2', annee_universitaire='2026-2027',
            institution=monde['inst'], filiere=monde['lpsea'],
            niveau=monde['l2'], groupe='', is_container=False)

        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        c = next(x for x in r.data['cohortes']
                 if x['filiere']['code'] == 'LPSEA' and x['niveau'] == 2)
        assert c['etat'] == 'a_affecter', "le groupe « E2 » n'a pas ete rapproche"
        assert c['niveau_code'] == 'E2', "le libelle affiche doit etre celui du referentiel"


# ── Choix automatique de l'année ────────────────────────────────────────────

class TestChoixDeLAnnee:

    def test_sans_parametre_le_serveur_prend_l_annee_incomplete(
            self, client_scolarite, monde):
        """C'est ce qui permet au bandeau de s'allumer sans réglage."""
        r = client_scolarite.get(URL)
        assert r.status_code == 200
        assert r.data['annee']['annee'] == '2026-2027'
        assert r.data['choisie_automatiquement'] is True

    def test_le_serveur_ne_REMONTE_JAMAIS_dans_le_passe(self, client_scolarite, monde):
        """Régression du 02/09/2026.

        Le jour où la rentrée la plus récente a été terminée, l'ancienne version
        — qui cherchait « la plus récente qui soit INCOMPLÈTE » — a reculé d'un
        an et annoncé 141 étudiants à affecter pour une rentrée faite depuis
        longtemps. Un étudiant n'ayant qu'un seul groupe, sans année, toute
        année révolue est structurellement incomplète : elle ne doit jamais être
        proposée.
        """
        from apps.absence.models import Etudiant
        from apps.departement.models import Departement

        # On termine l'année cible : plus rien à préparer en 2026-2027.
        cible_lpsea = Departement.objects.create(
            nom='LPSEA L2', annee_universitaire='2026-2027',
            institution=monde['inst'], filiere=monde['lpsea'],
            niveau=monde['l2'], groupe='', is_container=False)
        for etu in Etudiant.objects.all():
            ia = etu.inscriptions_admin.first()
            etu.departement = (
                cible_lpsea if ia.filiere_id == monde['lpsea'].pk
                else monde['depts']['stat_g1'] if ia.filiere_id == monde['stat'].pk
                else monde['depts']['sea'])
            etu.save(update_fields=['departement'])

        # L'annee PRECEDENTE, elle, a des inscrits que personne n'a rattaches.
        from apps.inscriptions.models import InscriptionAdministrative
        etu = Etudiant.objects.first()
        InscriptionAdministrative.objects.create(
            etudiant=etu, annee_univ=monde['precedent'], filiere=monde['stat'],
            niveau=1, institution=monde['inst'], numero_inscription='INS-VIEUX')

        r = client_scolarite.get(URL)
        assert r.data['annee']['annee'] == '2026-2027', (
            "le serveur a recule sur une annee revolue")
        assert r.data['total_affectes'] == r.data['total_inscrits']

    def test_avec_parametre_le_serveur_le_respecte(self, client_scolarite, monde):
        r = client_scolarite.get(URL, {'annee': monde['precedent'].pk})
        assert r.data['annee']['annee'] == '2025-2026'
        assert r.data['choisie_automatiquement'] is False

    def test_une_annee_inconnue_est_refusee(self, client_scolarite, monde):
        assert client_scolarite.get(URL, {'annee': 99999}).status_code == 404

    def test_un_parametre_non_numerique_est_refuse(self, client_scolarite, monde):
        assert client_scolarite.get(URL, {'annee': '2026-2027'}).status_code == 400


# ── Le signal s'éteint ──────────────────────────────────────────────────────

class TestExtinctionDuBandeau:

    def test_quand_tout_est_affecte_il_ne_reste_rien_a_signaler(
            self, client_scolarite, monde):
        """C'est cette condition, et elle seule, qui retire le bandeau."""
        from apps.absence.models import Etudiant
        from apps.departement.models import Departement

        # On crée le groupe qui manquait, puis on affecte tout le monde.
        cible_lpsea = Departement.objects.create(
            nom='LPSEA L2', annee_universitaire='2026-2027',
            institution=monde['inst'], filiere=monde['lpsea'],
            niveau=monde['l2'], groupe='', is_container=False)
        for etu in Etudiant.objects.all():
            ia = etu.inscriptions_admin.first()
            etu.departement = (
                cible_lpsea if ia.filiere_id == monde['lpsea'].pk
                else monde['depts']['stat_g1'] if ia.filiere_id == monde['stat'].pk
                else monde['depts']['sea'])
            etu.save(update_fields=['departement'])

        r = client_scolarite.get(URL, {'annee': monde['cible'].pk})
        assert r.data['total_affectes'] == r.data['total_inscrits']
        assert all(c['etat'] == 'complet' for c in r.data['cohortes'])


# ── Droits ──────────────────────────────────────────────────────────────────

class TestDroits:

    def test_sans_le_droit_progression_l_acces_est_refuse(self, monde):
        c = APIClient()
        c.force_authenticate(user=UserFactory(username='u_ens', role='enseignant'))
        assert c.get(URL).status_code == 403

    def test_non_authentifie_refuse(self, monde):
        assert APIClient().get(URL).status_code in (401, 403)
