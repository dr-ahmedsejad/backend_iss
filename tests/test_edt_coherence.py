"""
Le témoin de cohérence entre l'emploi du temps et le suivi.

Ce qu'il doit dire, et surtout ce qu'il ne doit PAS dire : un faux positif
pousse à régénérer un suivi qui allait bien, et apprend à ignorer le signal.

Deux partis pris s'y jouent, et les deux se vérifient ici :

  * on compare le CONTENU, jamais des horodatages. Confronter une date de
    modification à une date de génération ne dit que « quelque chose a été
    touché » — un enregistrement sans changement suffisait à crier ;
  * la SALLE ne fait pas partie de l'empreinte. La déplacer ne change ni les
    heures dues, ni le programme avancé, ni ce qui sera payé.
"""
from apps.edt.services.coherence import (ETAT_ALIGNE, ETAT_DIVERGENT,
                                         ETAT_PREVISIONNEL, etat_semaine)
from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401
from tests.test_edt_semaine_et_suivi import generer, poser

URL_COHERENCE = '/api/v1/edt/seances/coherence/'


def etat(numero=1, departements=None):
    return etat_semaine(ANNEE, 'I', numero, departements=departements)


def purger_le_suivi(user, numero=1):
    return api(user).delete(
        f'/api/v1/suivi/suivies/par-semaine/?numero_semaine={numero}'
        f'&annee_universitaire={ANNEE}&type_semestre=I')


# ── Les trois états ─────────────────────────────────────────────────────────

class TestLesTroisEtats:

    def test_sans_suivi_la_semaine_est_previsionnelle(self, monde, gens):
        """L'emploi du temps n'engage que l'avenir : rien à signaler."""
        poser(monde, 'G1', 1, em='SEA11')
        assert etat() == ETAT_PREVISIONNEL

    def test_juste_apres_la_generation_la_semaine_est_alignee(self, monde, gens):
        poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        assert etat() == ETAT_ALIGNE

    def test_changer_l_enseignant_apres_coup_fait_diverger(self, monde, gens):
        """Le cas qui coûte : ce n'est plus le même qui sera payé."""
        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        generer(gens['admin'], 1)
        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof'])
        assert etat() == ETAT_DIVERGENT

    def test_ajouter_une_seance_apres_coup_fait_diverger(self, monde, gens):
        poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane',
              creneau='09h45-11h15', salle='102')
        assert etat() == ETAT_DIVERGENT

    def test_annuler_une_seance_apres_coup_fait_diverger(self, monde, gens):
        s = poser(monde, 'G1', 1, em='SEA11')
        poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane',
              creneau='09h45-11h15', salle='102')
        generer(gens['admin'], 1)
        s.annulee = True
        s.save(update_fields=['annulee'])
        assert etat() == ETAT_DIVERGENT

    def test_regenerer_realigne(self, monde, gens):
        """Le signal doit s'éteindre quand on a fait ce qu'il demandait."""
        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        generer(gens['admin'], 1)
        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof'])
        assert etat() == ETAT_DIVERGENT

        purger_le_suivi(gens['admin'], 1)
        generer(gens['admin'], 1)
        assert etat() == ETAT_ALIGNE


# ── Ce qui ne doit PAS déclencher le signal ─────────────────────────────────

class TestPasDeFauxPositif:

    def test_changer_de_salle_ne_fait_pas_diverger(self, monde, gens):
        """Ni les heures dues, ni le programme avancé, ni la paie n'en dépendent."""
        s = poser(monde, 'G1', 1, em='SEA11', salle='101')
        generer(gens['admin'], 1)
        s.salle = monde['salles']['102']
        s.save(update_fields=['salle'])
        assert etat() == ETAT_ALIGNE

    def test_enregistrer_sans_rien_changer_ne_fait_pas_diverger(self, monde, gens):
        """Comparer des horodatages aurait crié ici. On compare le contenu."""
        s = poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        s.save()
        assert etat() == ETAT_ALIGNE

    def test_modifier_puis_remettre_ne_fait_pas_diverger(self, monde, gens):
        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        generer(gens['admin'], 1)
        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof'])
        s.prof = monde['profs']['Moustapha']
        s.save(update_fields=['prof'])
        assert etat() == ETAT_ALIGNE

    def test_une_observation_ne_fait_pas_diverger(self, monde, gens):
        s = poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        s.observations = 'salle bruyante'
        s.save(update_fields=['observations'])
        assert etat() == ETAT_ALIGNE


# ── Le périmètre ────────────────────────────────────────────────────────────

class TestPerimetre:

    def test_la_divergence_du_voisin_ne_m_est_pas_imputee(self, monde, gens):
        """Un signal qu'on ne peut pas corriger est un signal qu'on ignorera."""
        a = poser(monde, 'G1', 1, em='SEA11')
        poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane',
              creneau='09h45-11h15', salle='102')
        generer(gens['admin'], 1)

        # C'est le voisin qui bouge, pas moi.
        b = monde['depts']['SDID L2'].seances_edt.get()
        b.prof = monde['profs']['Moustapha']
        b.save(update_fields=['prof'])

        assert etat(departements=[monde['depts']['G1'].pk]) == ETAT_ALIGNE
        assert etat(departements=[monde['depts']['SDID L2'].pk]) == ETAT_DIVERGENT
        assert a.prof.nom == 'Moustapha'

    def test_un_compte_sans_aucun_perimetre_ne_voit_rien(self, monde, gens):
        """Un périmètre vide veut dire « rien », jamais « tout »."""
        poser(monde, 'G1', 1, em='SEA11')
        generer(gens['admin'], 1)
        assert etat(departements=[]) == ETAT_PREVISIONNEL


# ── L'endpoint ──────────────────────────────────────────────────────────────

class TestEndpoint:

    def _appel(self, user, **params):
        return api(user).get(URL_COHERENCE, {
            'annee_universitaire': ANNEE, 'type_semestre': 'I', **params})

    def test_l_endpoint_rend_l_etat_de_chaque_semaine(self, monde, gens):
        poser(monde, 'G1', 1, em='SEA11')
        poser(monde, 'G1', 2, em='SEA12', creneau='09h45-11h15')
        generer(gens['admin'], 1)

        r = self._appel(gens['admin'])
        assert r.status_code == 200
        assert r.data['etats']['1']['etat'] == ETAT_ALIGNE
        assert r.data['etats']['2']['etat'] == ETAT_PREVISIONNEL
        # Le libellé accompagne l'état : l'écran ne doit pas le réinventer.
        assert r.data['etats']['1']['libelle']

    def test_l_endpoint_liste_les_semaines_divergentes(self, monde, gens):
        s = poser(monde, 'G1', 1, em='SEA11', prof='Moustapha')
        generer(gens['admin'], 1)
        s.prof = monde['profs']['Abderahmane']
        s.save(update_fields=['prof'])

        r = self._appel(gens['admin'])
        assert r.data['divergentes'] == [1]

    def test_l_endpoint_borne_au_perimetre_de_l_appelant(self, monde, gens):
        poser(monde, 'G1', 1, em='SEA11')
        b = poser(monde, 'SDID L2', 1, em='SDID31', prof='Abderahmane',
                  creneau='09h45-11h15', salle='102')
        generer(gens['admin'], 1)
        b.prof = monde['profs']['Moustapha']
        b.save(update_fields=['prof'])

        # Le directeur des études ne gère pas « SDID L2 ».
        assert self._appel(gens['de']).data['divergentes'] == []
        assert self._appel(gens['autre_de']).data['divergentes'] == [1]

    def test_parametres_manquants_refuses(self, monde, gens):
        r = api(gens['admin']).get(URL_COHERENCE, {'type_semestre': 'I'})
        assert r.status_code == 400
