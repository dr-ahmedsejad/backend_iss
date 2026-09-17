"""
Les jours fériés — contre les services et l'API, pas l'écran.

La convention testée partout : un jour férié ISOLÉ garde le numéro de sa
semaine et prend le type 'ferie' ; une semaine ENTIÈRE hors cours perd son
numéro. Appartenir à la séquence, c'est avoir un numéro.

L'ENVELOPPE D'ERREUR de ce dépôt (`core/exceptions.py`), lue avant d'écrire les
assertions :
  * `ValidationError("texte")`, 404, 409 → message sous `error` ;
  * erreur d'un sérialiseur (dict) → sous `errors`, par champ.
"""
import datetime as dt

import pytest

from tests._edt_decor import ANNEE, api, gens, monde, seance  # noqa: F401

URL_SEANCES = '/api/v1/edt/seances/'
URL_SEMAINES = '/api/v1/parametres/semaines/'
URL_FIXES = '/api/v1/parametres/feries-fixes/'


# ── Aides ────────────────────────────────────────────────────────────────────

def ligne(monde, numero, jour):
    return monde['semaines'][(numero, jour)]


def poser(monde, dept, numero, jour, creneau='08h00-09h30', em='SEA11',
          prof='Moustapha', salle='101', annulee=False, motif=''):
    from apps.edt.models import SeanceReelle
    return SeanceReelle.objects.create(
        departement=monde['depts'][dept], semaine=ligne(monde, numero, jour),
        creneau_fk=monde['creneaux'][creneau], em=monde['ems'][em],
        prof=monde['profs'][prof], salle=monde['salles'][salle],
        type_seance_fk=monde['cm'], annulee=annulee, motif_annulation=motif)


def marquer(user, l, libelle='Fête de l’indépendance'):
    return api(user).post('%s%s/marquer-ferie/' % (URL_SEMAINES, l.pk),
                          {'libelle': libelle}, format='json')


def retirer(user, l):
    return api(user).post('%s%s/retirer-ferie/' % (URL_SEMAINES, l.pk), {}, format='json')


def suivi_genere(monde, numero):
    from apps.suivi.models import Suivie
    Suivie.objects.create(annee_universitaire=ANNEE, type_semestre='I',
                          numero_semaine=numero, institution=monde['inst'])


def ajouter_semaine(monde, numero):
    """Une semaine de plus, lundi-mercredi, après les deux du décor."""
    from apps.parametres.models import Semaine
    lundi = ligne(monde, 1, 'Lundi').date + dt.timedelta(weeks=numero - 1)
    for i, nom in enumerate(('Lundi', 'Mardi', 'Mercredi')):
        monde['semaines'][(numero, nom)] = Semaine.objects.create(
            numero_semaine=numero, jour_fk=monde['jours'][nom],
            date=lundi + dt.timedelta(days=i), annee_universitaire=ANNEE,
            type_semestre='I', type_semaine='cours')


def marquer_semaine(user, monde, numero, type_, description=''):
    return api(user).post(URL_SEMAINES + 'marquer-type/', {
        'annee_universitaire': ANNEE, 'type_semestre': 'I',
        'date_debut': ligne(monde, numero, 'Lundi').date.isoformat(),
        'nouveau_type': type_, 'description': description}, format='json')


def recharger(obj):
    obj.refresh_from_db()
    return obj


# ── 1. Marquer un jour ───────────────────────────────────────────────────────

class TestMarquer:

    def test_le_jour_garde_son_numero_et_rien_d_autre_ne_bouge(self, monde, gens):
        from apps.parametres.models import Semaine
        avant = {s.pk: (s.numero_semaine, s.type_semaine)
                 for s in Semaine.objects.all()}
        mardi = ligne(monde, 1, 'Mardi')

        r = marquer(gens['admin'], mardi)
        assert r.status_code == 200, r.data

        mardi.refresh_from_db()
        assert (mardi.numero_semaine, mardi.type_semaine) == (1, 'ferie')
        assert mardi.description == 'Fête de l’indépendance'
        apres = {s.pk: (s.numero_semaine, s.type_semaine)
                 for s in Semaine.objects.exclude(pk=mardi.pk)}
        assert apres == {k: v for k, v in avant.items() if k != mardi.pk}

    def test_ses_seances_sont_annulees_pas_supprimees_et_le_message_les_compte(
            self, monde, gens):
        from apps.edt.models import SeanceReelle
        a = poser(monde, 'G1', 1, 'Mardi')
        b = poser(monde, 'G2', 1, 'Mardi', em='SEA12', prof='Abderahmane', salle='102')
        autre_jour = poser(monde, 'G1', 1, 'Lundi')

        r = marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        assert r.data['annulees'] == 2
        assert 'marqué férié — 2 séances annulées' in r.data['message']

        assert SeanceReelle.objects.count() == 3
        for s in (a, b):
            recharger(s)
            assert (s.annulee, s.motif_annulation) == (True, 'ferie')
        assert recharger(autre_jour).annulee is False

    def test_elles_sortent_de_la_projection(self, monde, gens):
        from apps.edt.services.planification import projeter_semaine
        from apps.emplois.models import Emplois
        poser(monde, 'G1', 1, 'Lundi')
        poser(monde, 'G1', 1, 'Mardi', creneau='09h45-11h15')
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))

        projeter_semaine(ANNEE, 'I', 1)
        jours = set(Emplois.objects.values_list('jour_fk__jour', flat=True))
        assert jours == {'Lundi'}

    def test_un_suivi_genere_bloque_le_marquage(self, monde, gens):
        suivi_genere(monde, 1)
        mardi = ligne(monde, 1, 'Mardi')
        r = marquer(gens['admin'], mardi)
        assert r.status_code == 409
        assert 'suivi de la semaine 1' in r.data['error']
        assert 'Supprimez' in r.data['error']
        assert recharger(mardi).type_semaine == 'cours'

    def test_jour_inconnu_404(self, monde, gens):
        r = api(gens['admin']).post(URL_SEMAINES + '999999/marquer-ferie/',
                                    {'libelle': 'X'}, format='json')
        assert r.status_code == 404

    def test_libelle_vide_400(self, monde, gens):
        mardi = ligne(monde, 1, 'Mardi')
        r = marquer(gens['admin'], mardi, libelle='   ')
        assert r.status_code == 400
        assert 'nom' in r.data['error']
        assert recharger(mardi).type_semaine == 'cours'


# ── 2-3. Retirer ─────────────────────────────────────────────────────────────

class TestRetirer:

    def test_le_retrait_retablit_celles_du_ferie_pas_une_annulation_manuelle(
            self, monde, gens):
        du_ferie = poser(monde, 'G1', 1, 'Mardi')
        malade = poser(monde, 'G2', 1, 'Mardi', em='SEA12', prof='Abderahmane',
                       salle='102', annulee=True)
        l = ligne(monde, 1, 'Mardi')
        marquer(gens['admin'], l)

        r = retirer(gens['admin'], l)
        assert r.status_code == 200, r.data
        assert r.data['retablies'] == 1
        assert '1 séance rétablie' in r.data['message']
        assert '1 annulée à la main reste annulée' in r.data['message']
        assert recharger(du_ferie).annulee is False
        assert recharger(du_ferie).motif_annulation == ''
        assert recharger(malade).annulee is True
        l.refresh_from_db()
        assert (l.type_semaine, l.numero_semaine, l.description) == ('cours', 1, '')

    def test_un_suivi_genere_bloque_le_retrait(self, monde, gens):
        l = ligne(monde, 1, 'Mardi')
        marquer(gens['admin'], l)
        suivi_genere(monde, 1)
        r = retirer(gens['admin'], l)
        assert r.status_code == 409
        assert recharger(l).type_semaine == 'ferie'

    def test_une_annulation_ou_un_retablissement_a_la_main_efface_le_motif(
            self, monde, gens):
        s = poser(monde, 'G1', 1, 'Lundi', annulee=True, motif='ferie')
        r = api(gens['admin']).patch('%s%s/' % (URL_SEANCES, s.pk),
                                     {'annulee': False}, format='json')
        assert r.status_code == 200, r.data
        assert (recharger(s).annulee, s.motif_annulation) == (False, '')


# ── 5. Jour fermé : ce qu'on peut faire d'une séance ─────────────────────────

class TestJourFerme:

    def test_ajout_refuse_un_jour_ferie(self, monde, gens):
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        r = api(gens['admin']).post(URL_SEANCES, seance(
            monde, 'G1', numero=1, jour='Mardi', em='SEA11'), format='json')
        assert r.status_code == 400
        assert "n'est pas un jour de cours" in r.data['error']

    def test_ajout_refuse_sur_une_semaine_entiere_de_vacances(self, monde, gens):
        marquer_semaine(gens['admin'], monde, 2, 'vacances')
        r = api(gens['admin']).post(URL_SEANCES, seance(
            monde, 'G1', numero=2, jour='Lundi', em='SEA11'), format='json')
        assert r.status_code == 400

    def test_retablissement_refuse_un_jour_ferie(self, monde, gens):
        s = poser(monde, 'G1', 1, 'Mardi')
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        r = api(gens['admin']).patch('%s%s/' % (URL_SEANCES, s.pk),
                                     {'annulee': False}, format='json')
        assert r.status_code == 400
        assert (recharger(s).annulee, s.motif_annulation) == (True, 'ferie')

    def test_modification_refusee_et_la_seance_intacte_est_retablie(self, monde, gens):
        s = poser(monde, 'G1', 1, 'Mardi')
        l = ligne(monde, 1, 'Mardi')
        marquer(gens['admin'], l)
        r = api(gens['admin']).patch('%s%s/' % (URL_SEANCES, s.pk),
                                     {'salle': monde['salles']['102'].pk}, format='json')
        assert r.status_code == 400
        assert 'férié' in r.data['error']

        retirer(gens['admin'], l)
        recharger(s)
        assert (s.annulee, s.salle.nom) == (False, '101')

    def test_suppression_refusee_sur_un_ferie_isole(self, monde, gens):
        from apps.edt.models import SeanceReelle
        s = poser(monde, 'G1', 1, 'Mardi')
        l = ligne(monde, 1, 'Mardi')
        marquer(gens['admin'], l)
        r = api(gens['admin']).delete('%s%s/' % (URL_SEANCES, s.pk))
        assert r.status_code == 400
        assert SeanceReelle.objects.filter(pk=s.pk).exists()
        retirer(gens['admin'], l)
        assert recharger(s).annulee is False

    def test_partage_refuse_sur_un_ferie_isole(self, monde, gens):
        from apps.edt.models import SeanceReelle
        s = poser(monde, 'G1', 1, 'Mardi')
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        r = api(gens['admin']).post('%s%s/partager/' % (URL_SEANCES, s.pk),
                                    {'departements': [monde['depts']['G2'].pk]},
                                    format='json')
        assert r.status_code == 400
        assert SeanceReelle.objects.count() == 1
        assert recharger(s).cle_partage is None

    def test_permutation_refusee_sur_un_ferie_isole(self, monde, gens):
        a = poser(monde, 'G1', 1, 'Mardi')
        b = poser(monde, 'G2', 1, 'Mardi', em='SEA12', prof='Abderahmane', salle='102')
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        r = api(gens['admin']).post(URL_SEANCES + 'permuter/',
                                    {'seance_a': a.pk, 'seance_b': b.pk}, format='json')
        assert r.status_code == 400
        assert 'férié' in r.data['error']

    def test_liberation_de_salle_refusee_sur_un_ferie_isole(self, monde, gens):
        from apps.edt.models import DemandeLiberation
        s = poser(monde, 'G1', 1, 'Mardi')
        d = DemandeLiberation.objects.create(seance=s, salle=s.salle,
                                             demandeur=gens['de'])
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        r = api(gens['admin']).post('/api/v1/edt/liberations/%s/accorder/' % d.pk,
                                    {}, format='json')
        assert r.status_code == 400
        assert recharger(s).salle is not None

    def test_un_geste_manuel_sur_un_cours_partage_efface_le_motif_des_soeurs(
            self, monde, gens):
        """Le cours partagé est annulé par le férié, puis sa semaine entière
        passe en vacances — la séance n'est plus sur un férié ISOLÉ, on peut la
        rétablir à la main. Sa sœur suit : rétablie, et sans motif. Sans la
        propagation du motif, elle serait rétablie mais toujours marquée
        « férié »."""
        import uuid
        from apps.edt.models import SeanceReelle
        cle = uuid.uuid4()
        a = poser(monde, 'G1', 1, 'Mardi')
        b = poser(monde, 'G2', 1, 'Mardi')
        SeanceReelle.objects.filter(pk__in=[a.pk, b.pk]).update(cle_partage=cle)
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        marquer_semaine(gens['admin'], monde, 1, 'vacances')

        r = api(gens['admin']).patch('%s%s/' % (URL_SEANCES, a.pk),
                                     {'annulee': False}, format='json')
        assert r.status_code == 200, r.data
        assert (recharger(b).annulee, b.motif_annulation) == (False, '')

    def test_suppression_permise_sur_une_semaine_entiere_de_vacances(self, monde, gens):
        from apps.edt.models import SeanceReelle
        s = poser(monde, 'G1', 2, 'Mardi')
        r = marquer_semaine(gens['admin'], monde, 2, 'vacances')
        assert r.status_code == 200, r.data
        r = api(gens['admin']).delete('%s%s/' % (URL_SEANCES, s.pk))
        assert r.status_code == 204
        assert not SeanceReelle.objects.filter(pk=s.pk).exists()


# ── Les trois pièges du code existant ────────────────────────────────────────

class TestSemaines:

    def test_la_vue_regroupee_ne_coupe_pas_la_semaine(self, monde, gens):
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'), libelle='Tabaski')
        r = api(gens['admin']).get(URL_SEMAINES + 'grouped/',
                                   {'annee_universitaire': ANNEE, 'type_semestre': 'I'})
        assert r.status_code == 200
        s1 = [g for g in r.data if g['numero_semaine'] == 1]
        assert len(s1) == 1
        assert len(s1[0]['ids']) == 3
        assert s1[0]['type_semaine'] == 'cours'
        assert [(f['jour'], f['libelle']) for f in s1[0]['jours_feries']] == [('Mardi', 'Tabaski')]
        assert [(j['jour'], j['type_semaine']) for j in s1[0]['jours']] ==                [('Lundi', 'cours'), ('Mardi', 'ferie'), ('Mercredi', 'cours')]

    def test_une_semaine_contenant_un_ferie_isole_peut_etre_marquee(self, monde, gens):
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        r = marquer_semaine(gens['admin'], monde, 1, 'vacances')
        assert r.status_code == 200, r.data
        from apps.parametres.models import Semaine
        assert set(Semaine.objects.filter(numero_semaine__isnull=True)
                   .values_list('type_semaine', flat=True)) == {'vacances'}

    def test_marquer_la_semaine_entiere_renumerote_aussi_ses_feries_isoles(
            self, monde, gens):
        ajouter_semaine(monde, 3)
        mardi3 = ligne(monde, 3, 'Mardi')
        marquer(gens['admin'], mardi3)

        r = marquer_semaine(gens['admin'], monde, 2, 'vacances')
        assert r.status_code == 200, r.data
        numeros = {recharger(ligne(monde, 3, j)).numero_semaine
                   for j in ('Lundi', 'Mardi', 'Mercredi')}
        assert numeros == {2}
        assert recharger(mardi3).type_semaine == 'ferie'

    def test_reinserer_une_semaine_renumerote_aussi_les_feries_suivants(self, monde, gens):
        ajouter_semaine(monde, 3)
        marquer_semaine(gens['admin'], monde, 2, 'vacances')
        mardi3 = ligne(monde, 3, 'Mardi')
        marquer(gens['admin'], mardi3)                    # désormais S2
        r = marquer_semaine(gens['admin'], monde, 2, 'cours')
        assert r.status_code == 200, r.data
        assert {recharger(ligne(monde, 3, j)).numero_semaine
                for j in ('Lundi', 'Mardi', 'Mercredi')} == {3}

    def test_remettre_la_semaine_en_cours_retablit_les_seances_du_ferie(self, monde, gens):
        du_ferie = poser(monde, 'G1', 1, 'Mardi')
        malade = poser(monde, 'G2', 1, 'Lundi', em='SEA12', prof='Abderahmane',
                       salle='102', annulee=True)
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))
        marquer_semaine(gens['admin'], monde, 1, 'vacances')
        r = marquer_semaine(gens['admin'], monde, 1, 'cours')
        assert r.status_code == 200, r.data
        assert r.data['seances_retablies'] == 1
        assert recharger(du_ferie).annulee is False
        assert recharger(malade).annulee is True

    def test_changer_la_description_de_la_semaine_n_ecrase_pas_le_nom_du_ferie(
            self, monde, gens):
        mardi = ligne(monde, 1, 'Mardi')
        marquer(gens['admin'], mardi, libelle='Tabaski')
        r = marquer_semaine(gens['admin'], monde, 1, 'cours', description='Rattrapage')
        assert r.status_code == 200, r.data
        assert recharger(mardi).description == 'Tabaski'
        assert recharger(ligne(monde, 1, 'Lundi')).description == 'Rattrapage'


# ── 6. Reprise en patron ─────────────────────────────────────────────────────

class TestReprise:

    def test_la_reprise_garde_le_jour_ferie_pas_l_annulation_manuelle(self, monde, gens):
        from tests._edt_decor import grille
        g = grille(monde, 'G1')
        poser(monde, 'G1', 1, 'Lundi')
        poser(monde, 'G1', 1, 'Mardi', creneau='09h45-11h15')
        poser(monde, 'G1', 1, 'Mercredi', creneau='11h30-13h00', annulee=True)
        marquer(gens['admin'], ligne(monde, 1, 'Mardi'))

        r = api(gens['admin']).post('/api/v1/edt/grilles/%s/reprendre-semaine/' % g.pk,
                                    {'semaine_source': 1}, format='json')
        assert r.status_code == 200, r.data
        jours = set(g.seances.values_list('jour_fk__jour', flat=True))
        assert jours == {'Lundi', 'Mardi'}
        assert r.data['annulees_ecartees'] == 1


# ── 7. Fériés fixes ──────────────────────────────────────────────────────────

class TestFeriesFixes:

    def _fixe(self, date, libelle='Fixe', actif=True):
        from apps.parametres.models import JourFerieFixe
        return JourFerieFixe.objects.create(jour=date.day, mois=date.month,
                                            libelle=libelle, actif=actif)

    def test_le_29_fevrier_est_valide_et_le_30_ne_l_est_pas(self, monde, gens):
        ok = api(gens['admin']).post(URL_FIXES, {'jour': 29, 'mois': 2, 'libelle': 'Bissextile'},
                                     format='json')
        assert ok.status_code == 201, ok.data
        ko = api(gens['admin']).post(URL_FIXES, {'jour': 30, 'mois': 2, 'libelle': 'Non'},
                                     format='json')
        assert ko.status_code == 400
        assert 'jour' in ko.data['errors']

    def test_unicite_jour_mois(self, monde, gens):
        api(gens['admin']).post(URL_FIXES, {'jour': 1, 'mois': 5, 'libelle': 'A'}, format='json')
        r = api(gens['admin']).post(URL_FIXES, {'jour': 1, 'mois': 5, 'libelle': 'B'},
                                    format='json')
        assert r.status_code == 400

    def test_appliques_aux_jours_tout_juste_generes_et_a_eux_seuls(self, monde, gens):
        from apps.parametres.models import Semaine
        ancien = ligne(monde, 1, 'Mardi')                    # existe déjà
        debut = ligne(monde, 2, 'Lundi').date + dt.timedelta(weeks=1)
        nouveau_mardi = debut + dt.timedelta(days=1)
        self._fixe(ancien.date, 'Ancien')
        self._fixe(nouveau_mardi, 'Nouveau')

        r = api(gens['admin']).post(URL_SEMAINES + 'ajouter-batch/', {
            'annee_universitaire': ANNEE, 'type_semestre': 'I',
            'date_debut': debut.isoformat(), 'nombre_semaines': 1}, format='json')
        assert r.status_code == 201, r.data
        assert [m['libelle'] for m in r.data['feries']['marques']] == ['Nouveau']
        assert recharger(ancien).type_semaine == 'cours'
        nouveau = Semaine.objects.get(date=nouveau_mardi)
        assert (nouveau.type_semaine, nouveau.numero_semaine) == ('ferie', 3)

    def test_appliquer_au_calendrier_inactifs_ignores_idempotent(self, monde, gens):
        self._fixe(ligne(monde, 1, 'Mardi').date, 'Actif')
        self._fixe(ligne(monde, 2, 'Lundi').date, 'Inactif', actif=False)
        url = URL_SEMAINES + 'appliquer-feries-fixes/'
        corps = {'annee_universitaire': ANNEE, 'type_semestre': 'I'}

        r1 = api(gens['admin']).post(url, corps, format='json')
        assert [m['libelle'] for m in r1.data['marques']] == ['Actif']
        assert recharger(ligne(monde, 2, 'Lundi')).type_semaine == 'cours'
        r2 = api(gens['admin']).post(url, corps, format='json')
        assert r2.data['marques'] == [] and r2.data['ecartes'] == []

    def test_un_jour_bloque_par_un_suivi_est_ecarte_sans_empecher_les_autres(
            self, monde, gens):
        self._fixe(ligne(monde, 1, 'Mardi').date, 'Bloqué')
        self._fixe(ligne(monde, 2, 'Mardi').date, 'Libre')
        suivi_genere(monde, 1)
        r = api(gens['admin']).post(URL_SEMAINES + 'appliquer-feries-fixes/',
                                    {'annee_universitaire': ANNEE}, format='json')
        assert [m['libelle'] for m in r.data['marques']] == ['Libre']
        assert [e['libelle'] for e in r.data['ecartes']] == ['Bloqué']
        assert 'suivi' in r.data['ecartes'][0]['motif']


# ── La migration de données ──────────────────────────────────────────────────

class TestMigrationDonnees:

    def _migration(self):
        import importlib
        return importlib.import_module(
            'apps.parametres.migrations.0017_feries_fixes_mauritanie')

    def test_cree_les_4_ne_double_pas_n_ecrase_pas_et_ne_retire_que_les_siens(self, db):
        from django.apps import apps as registre
        from apps.parametres.models import JourFerieFixe
        m = self._migration()

        m.inserer(registre, None)
        assert set(JourFerieFixe.objects.values_list('jour', 'mois')) == \
               {(1, 1), (1, 5), (25, 5), (28, 11)}
        assert JourFerieFixe.objects.filter(actif=True).count() == 4

        JourFerieFixe.objects.filter(jour=28, mois=11).update(libelle='Modifié')
        m.inserer(registre, None)
        assert JourFerieFixe.objects.count() == 4
        assert JourFerieFixe.objects.get(jour=28, mois=11).libelle == 'Modifié'

        JourFerieFixe.objects.create(jour=6, mois=3, libelle='Autre')
        m.retirer(registre, None)
        assert list(JourFerieFixe.objects.values_list('jour', 'mois')) == [(6, 3)]

    def test_aucune_fete_religieuse(self):
        noms = ' '.join(l for _, _, l in self._migration().FERIES).lower()
        for fete in ('aïd', 'aid', 'hégire', 'mawlid', 'tabaski', 'ramadan'):
            assert fete not in noms
