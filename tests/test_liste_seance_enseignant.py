"""
L'enseignant consulte la liste des étudiants d'une de SES séances (app
« ISS Enseignant »), en lecture seule — l'appel reste sur la fiche papier.

La liste est celle de la fiche d'appel (`liste_appel`), groupe par groupe :
un CM commun à deux groupes donne deux listes.
"""
import pytest

from tests._edt_decor import ANNEE, api, gens, monde  # noqa: F401
from tests.test_liste_appel import etudiant, inscrire, seance

URL = '/api/v1/absences/enseignant/liste/'


def _prof(monde, nom='Moustapha'):
    from apps.authentication.models import CustomUser
    u = CustomUser.objects.create_user(username='prof_%s' % nom, email='%s@iss.mr' % nom,
                                       password='x', role='enseignant')
    p = monde['profs'][nom]
    p.user = u
    p.save(update_fields=['user'])
    return u


def _pointage(monde, groupes, prof='Moustapha', em='SEA11'):
    from apps.suivi.models import SuiviePointage
    sp = SuiviePointage.objects.create(
        annee_universitaire=ANNEE, numero_semaine=1, type_semestre='I',
        prof=monde['profs'][prof], em=monde['ems'][em], type_seance_fk=monde['cm'],
        creneau_fk=monde['creneaux']['08h00-09h30'], institution=monde['inst'])
    sp.departements.set([monde['depts'][g] for g in groupes])
    return sp


class TestListe:

    def test_les_etudiants_de_chaque_groupe_de_la_seance(self, monde):
        user = _prof(monde)
        for m, g in (('002', 'G1'), ('001', 'G1'), ('003', 'G2')):
            inscrire(monde, etudiant(monde, g, m), 'SEA11')
        # Les deux groupes enseignent l'élément (sinon 003 serait une « dette » de G1).
        seance(monde, 'G1', 'SEA11')
        seance(monde, 'G2', 'SEA11', jour='Mardi')
        sp = _pointage(monde, ['G1', 'G2'])

        r = api(user).get(URL, {'pointage': sp.pk})

        assert r.status_code == 200, r.data
        assert [(g['nom'], [e['matricule'] for e in g['etudiants']]) for g in r.data['groupes']] == [
            (monde['depts']['G1'].nom, ['001', '002']),
            (monde['depts']['G2'].nom, ['003']),
        ]
        assert r.data['total'] == 3
        assert r.data['seance']['em_code'] == monde['ems']['SEA11'].code_em

    def test_pas_la_seance_d_un_autre(self, monde):
        user = _prof(monde)
        sp = _pointage(monde, ['G1'], prof='Abderahmane')
        assert api(user).get(URL, {'pointage': sp.pk}).status_code == 404

    def test_reserve_aux_enseignants(self, monde, gens):
        sp = _pointage(monde, ['G1'])
        assert api(gens['de']).get(URL, {'pointage': sp.pk}).status_code == 403

    def test_parametre_requis(self, monde):
        assert api(_prof(monde)).get(URL).status_code == 400
