"""
Emploi du temps : règles de conflit et périmètres d'écriture.

Ce que la planification exige, et que rien ne vérifiait avant ce chantier —
`check_dispo` existe côté serveur depuis toujours, mais **aucun écran ne
l'appelle** :

  - qu'un enseignant, une salle ou un ÉTUDIANT ne soit jamais attendu à deux
    endroits en même temps. La troisième est la moins évidente et la plus
    coûteuse : elle ne se voit sur aucun écran ;
  - qu'un cours réellement partagé reste UNE séance, sans quoi il serait payé
    autant de fois qu'il compte de groupes ;
  - qu'un élément du semestre pair ne se planifie pas dans une période impaire,
    où il se générerait sans jamais apparaître au suivi ;
  - que chacun n'écrive que sur les groupes qui lui sont délégués.

La classe `TestConflitsEtudiants` est la plus importante du fichier : c'est
elle qui distingue le portage réussi de la copie fidèle mais fausse. À l'ISS,
« G1 » et « G2 » sont deux groupes de TD d'une même filière — donc deux publics
distincts — et rien dans `Departement.groupe` ne le dit. Une règle qui les
confondrait refuserait le geste quotidien du directeur des études.
"""
from tests._edt_decor import (URL_GRILLE, URL_SEANCE, URL_SEANCE_TYPE,  # noqa: F401
                              api, case, gens, grille, monde, seance)

URL = URL_SEANCE_TYPE


# ── 1. Conflits de ressource ────────────────────────────────────────────────

class TestConflitsDeRessource:
    """Un enseignant, une salle : une seule place à la fois."""

    def test_meme_prof_deux_cours_differents_refuse(self, monde, gens):
        c = api(gens['admin'])
        premiere = c.post(URL, case(monde, grille(monde, 'SEA L2 G1'),
                                    em='SEA31', prof='Moustapha'),
                          format='json')
        assert premiere.status_code == 201
        r = c.post(URL, case(monde, grille(monde, 'SDID L2'), em='SDID31',
                             prof='Moustapha', salle='102'), format='json')
        assert r.status_code == 400
        assert 'prof' in r.data['errors']
        # Le message dit OÙ est le conflit : sans cela on cherche à l'aveugle.
        assert 'SEA L2 - G1' in str(r.data['errors']['prof'])

    def test_meme_salle_deux_cours_differents_refuse(self, monde, gens):
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'SEA L2 G1'), em='SEA31',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'SDID L2'), em='SDID31',
                             prof='Abderahmane', salle='101'), format='json')
        assert r.status_code == 400
        assert 'salle' in r.data['errors']
        assert 'SEA L2 - G1' in str(r.data['errors']['salle'])

    def test_seance_reellement_partagee_acceptee(self, monde, gens):
        """Mêmes prof, EM, salle et type : c'est UN cours, pas deux.

        C'est le geste le plus courant de l'écran actuel — « dupliquer vers
        d'autres groupes ». Sans cette tolérance, il mourrait au premier clic.
        """
        c = api(gens['admin'])
        a = c.post(URL, case(monde, grille(monde, 'G1'), em='SEA11'),
                   format='json')
        b = c.post(URL, case(monde, grille(monde, 'G2'), em='SEA11'),
                   format='json')
        assert (a.status_code, b.status_code) == (201, 201)


# ── 2. Le conflit que personne ne voit : l'étudiant ─────────────────────────

class TestConflitsEtudiants:
    """Deux groupes se recoupent-ils ? La réponse se lit sur le NOM à l'ISS."""

    def test_deux_TD_simultanes_dans_G1_et_G2(self, monde, gens):
        """Le cas qui aurait cassé la production.

        « G1 » et « G2 » sont deux groupes de TD de la filière SEA : deux
        publics distincts, qui ont cours en même temps toute l'année — 679
        couples de ce type figurent déjà dans le suivi. La règle de l'ESP, qui
        conclut « mêmes étudiants » sur (même niveau + même filière), les
        aurait tous refusés, car `Departement.groupe` est vide à l'ISS.
        """
        c = api(gens['admin'])
        a = c.post(URL, case(monde, grille(monde, 'G1'), em='SEA11',
                             prof='Moustapha', salle='101'), format='json')
        b = c.post(URL, case(monde, grille(monde, 'G2'), em='SEA12',
                             prof='Abderahmane', salle='102'), format='json')
        assert (a.status_code, b.status_code) == (201, 201)

    def test_deux_TD_simultanes_dans_SEA_L2_G1_et_G2(self, monde, gens):
        """La même chose avec l'autre orthographe : « SEA L2 - G1 » / « - G2 »."""
        c = api(gens['admin'])
        a = c.post(URL, case(monde, grille(monde, 'SEA L2 G1'), em='SEA31',
                             prof='Moustapha', salle='101'), format='json')
        b = c.post(URL, case(monde, grille(monde, 'SEA L2 G2'), em='SEA31',
                             prof='Abderahmane', salle='102'), format='json')
        assert (a.status_code, b.status_code) == (201, 201)

    def test_deux_filieres_differentes_ne_se_croisent_pas(self, monde, gens):
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'SEA L2 G1'), em='SEA31',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'SDID L2'), em='SDID31',
                             prof='Abderahmane', salle='102'), format='json')
        assert r.status_code == 201

    def test_deux_niveaux_differents_ne_se_croisent_pas(self, monde, gens):
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'G1'), em='SEA11',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'SEA L2 G1'), em='SEA31',
                             prof='Abderahmane', salle='102'), format='json')
        assert r.status_code == 201

    def test_le_groupe_entier_et_son_sous_groupe_se_croisent(self, monde, gens):
        """« SDID L2 » contient « SDID L2 G1 » : pas de cours en même temps.

        Aucun des deux n'a de filière — c'est le cas de treize groupes de
        l'ISS. Seule la souche du nom permet de les rapprocher.
        """
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'SDID L2'), em='SDID31',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'SDID L2 G1'), em='SEA31',
                             prof='Abderahmane', salle='102'), format='json')
        assert r.status_code == 400
        assert 'deux endroits' in str(r.data['errors'])

    def test_un_enseignement_transversal_croise_les_groupes_de_son_annee(
            self, monde, gens):
        """HE réunit toute la promotion : personne d'autre à cette heure-là."""
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'HE'), em='HE11',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'G1'), em='SEA11',
                             prof='Abderahmane', salle='102'), format='json')
        assert r.status_code == 400
        assert 'deux endroits' in str(r.data['errors'])

    def test_deux_transversaux_se_croisent_entre_eux(self, monde, gens):
        """HE et ST s'adressent aux mêmes étudiants, pas à deux moitiés."""
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'HE'), em='HE11',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'ST'), em='ST11',
                             prof='Abderahmane', salle='102'), format='json')
        assert r.status_code == 400
        assert 'deux endroits' in str(r.data['errors'])

    def test_un_transversal_d_une_autre_annee_ne_croise_personne(
            self, monde, gens):
        """« HE » 2026-2027 n'a rien à voir avec la promotion de 2025-2026."""
        c = api(gens['admin'])
        c.post(URL, case(monde, grille(monde, 'HE 26'), em='HE11',
                         prof='Moustapha', salle='101'), format='json')
        r = c.post(URL, case(monde, grille(monde, 'G1'), em='SEA11',
                             prof='Abderahmane', salle='102'), format='json')
        assert r.status_code == 201

    def test_la_regle_vaut_aussi_sur_les_seances_datees(self, monde, gens):
        """Le patron et la semaine sont deux choses, la règle est la même."""
        c = api(gens['admin'])
        a = c.post(URL_SEANCE, seance(monde, 'G1', em='SEA11',
                                      prof='Moustapha', salle='101'),
                   format='json')
        assert a.status_code == 201
        r = c.post(URL_SEANCE, seance(monde, 'HE', em='HE11',
                                      prof='Abderahmane', salle='102'),
                   format='json')
        assert r.status_code == 400
        assert 'deux endroits' in str(r.data['errors'])


# ── 3. Cohérence du référentiel ─────────────────────────────────────────────

class TestCoherenceReferentiel:

    def test_em_du_semestre_pair_refuse_en_periode_impaire(self, monde, gens):
        """SEA24 est en S2 : planifié en S1, il n'apparaîtrait sur aucun écran."""
        r = api(gens['admin']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA24'), format='json')
        assert r.status_code == 400
        assert 'S2' in str(r.data['errors']['em'][0])

    def test_em_du_bon_semestre_accepte(self, monde, gens):
        r = api(gens['admin']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA11'), format='json')
        assert r.status_code == 201

    def test_la_parite_se_lit_meme_sans_module_lmd(self, monde, gens):
        """56 éléments sur 207 n'ont pas d'UE LMD à l'ISS.

        L'ESP lisait le semestre sur `module_lmd.semestre` ; ici cela aurait
        laissé filer un quart du catalogue sans un mot. On lit `EM.semestre`.
        """
        assert monde['ems']['HE24'].module_lmd_id is None
        r = api(gens['admin']).post(
            URL, case(monde, grille(monde, 'HE'), em='HE24'), format='json')
        assert r.status_code == 400
        assert 'S2' in str(r.data['errors']['em'][0])

    def test_une_seance_speciale_n_a_ni_prof_ni_em_ni_salle(self, monde, gens):
        """Sport, instruction militaire : y laisser un enseignant lui compterait
        des heures — donc une vacation — pour un cours qu'il ne donne pas."""
        r = api(gens['admin']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA11',
                      prof='Moustapha', salle='101', type_seance='sport'),
            format='json')
        assert r.status_code == 201
        assert (r.data['em'], r.data['prof'], r.data['salle']) == (None, None, None)


# ── 4. Droits sur les cases ─────────────────────────────────────────────────

class TestDroitsSurLesCases:
    """Le périmètre de l'ISS a un seul axe : les groupes délégués."""

    def test_le_de_ecrit_dans_son_groupe(self, monde, gens):
        r = api(gens['de']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA11'), format='json')
        assert r.status_code == 201

    def test_le_de_ne_peut_pas_ecrire_chez_le_voisin(self, monde, gens):
        r = api(gens['de']).post(
            URL, case(monde, grille(monde, 'SDID L2'), em='SDID31'),
            format='json')
        assert r.status_code == 403

    def test_sans_perimetre_on_n_ecrit_nulle_part(self, monde, gens):
        r = api(gens['orphelin']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA11'), format='json')
        assert r.status_code == 403

    def test_lecture_ouverte_le_filtrage_se_fait_au_queryset(self, monde, gens):
        api(gens['admin']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA11'), format='json')
        assert api(gens['orphelin']).get(URL).status_code == 200

    def test_le_de_ne_peut_pas_modifier_la_case_du_voisin(self, monde, gens):
        pose = api(gens['admin']).post(
            URL, case(monde, grille(monde, 'SDID L2'), em='SDID31'),
            format='json')
        assert pose.status_code == 201
        r = api(gens['de']).patch(f"{URL}{pose.data['id']}/",
                                  {'salle': monde['salles']['102'].pk},
                                  format='json')
        assert r.status_code == 403

    def test_le_de_ne_peut_pas_supprimer_la_case_du_voisin(self, monde, gens):
        pose = api(gens['admin']).post(
            URL, case(monde, grille(monde, 'SDID L2'), em='SDID31'),
            format='json')
        r = api(gens['de']).delete(f"{URL}{pose.data['id']}/")
        assert r.status_code == 403

    def test_modifiable_dit_a_l_ecran_ce_qu_il_peut_ouvrir(self, monde, gens):
        """Une case qu'on ne peut pas écrire doit se donner à lire, pas à
        remplir : sinon le refus n'arrive qu'après la saisie."""
        api(gens['admin']).post(
            URL, case(monde, grille(monde, 'SDID L2'), em='SDID31'),
            format='json')
        lignes = api(gens['de']).get(URL).data
        assert lignes and all(l['modifiable'] is False for l in lignes)

    def test_modifiable_est_vrai_sur_ses_propres_cases(self, monde, gens):
        api(gens['de']).post(
            URL, case(monde, grille(monde, 'G1'), em='SEA11'), format='json')
        lignes = api(gens['de']).get(URL).data
        assert lignes and all(l['modifiable'] is True for l in lignes)


# ── 5. Droits sur la grille ─────────────────────────────────────────────────

class TestDroitsSurLaGrille:

    def _payload(self, monde, dept):
        return {'departement': monde['depts'][dept].pk, 'type_semestre': 'I',
                'annee_universitaire': '2025-2026', 'actif': True}

    def test_le_de_cree_la_grille_de_son_groupe(self, monde, gens):
        r = api(gens['de']).post(URL_GRILLE,
                                 self._payload(monde, 'G1'), format='json')
        assert r.status_code == 201

    def test_le_de_ne_cree_pas_la_grille_du_voisin(self, monde, gens):
        r = api(gens['de']).post(URL_GRILLE,
                                 self._payload(monde, 'SDID L2'), format='json')
        assert r.status_code == 403

    def test_sans_perimetre_aucune_grille(self, monde, gens):
        r = api(gens['orphelin']).post(URL_GRILLE,
                                       self._payload(monde, 'G1'), format='json')
        assert r.status_code == 403
