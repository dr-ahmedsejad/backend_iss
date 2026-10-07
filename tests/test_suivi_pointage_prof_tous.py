"""
Le vacataire voit, mois par mois, ce qu'il a gagné ET le détail de ses
séances : faites (payées), non faites ou reportées (non payées), avec leurs
dates — `avancement/suivi-pointage-prof/?tous=1` (app « ISS Enseignant »).

Sans `tous`, la réponse ne change pas (portail web) ; avec, les totaux ne
comptent toujours que ce qui est payé.
"""
import datetime as dt

import pytest

from tests._edt_decor import ANNEE, api, monde  # noqa: F401

URL = '/api/v1/avancement/suivi-pointage-prof/'


def _prof(monde):
    from apps.authentication.models import CustomUser
    u = CustomUser.objects.create_user(username='vac', email='vac@iss.mr', password='x', role='enseignant')
    p = monde['profs']['Moustapha']
    p.user = u
    p.type = 'vacataire'
    p.save(update_fields=['user', 'type'])
    return u


def _pointage(monde, jour, commentaire):
    from apps.suivi.models import SuiviePointage
    return SuiviePointage.objects.create(
        annee_universitaire=ANNEE, numero_semaine=1, type_semestre='I', date_suivie=jour,
        prof=monde['profs']['Moustapha'], em=monde['ems']['SEA11'], type_seance_fk=monde['cm'],
        creneau_fk=monde['creneaux']['08h00-09h30'], institution=monde['inst'],
        commentaire=commentaire, duree_creneau=1.5, taux_paiement=2000)


def test_tous_ajoute_les_seances_non_faites_passees_sans_les_payer(monde):
    user = _prof(monde)
    passe = dt.date.today() - dt.timedelta(days=3)
    _pointage(monde, passe, 'Fait')
    _pointage(monde, passe - dt.timedelta(days=1), 'Non fait')
    _pointage(monde, passe - dt.timedelta(days=2), 'Reporté')
    _pointage(monde, dt.date.today() + dt.timedelta(days=5), 'Non fait')   # à venir : absente

    sans = api(user).get(URL, {'annee_universitaire': ANNEE}).data
    assert [r['statut'] for r in sans['rows']] == ['Fait']

    avec = api(user).get(URL, {'annee_universitaire': ANNEE, 'tous': 1}).data
    assert sorted(r['statut'] for r in avec['rows']) == ['Fait', 'Non fait', 'Reporté']
    assert all(r['date_suivie'] for r in avec['rows'])
    assert all(r['id'] and r['creneau'] == '08h00-09h30' for r in avec['rows'])
    # Seule la séance faite est payée.
    assert (avec['total_heures'], avec['total_montant']) == (1.5, 3000)
    assert (sans['total_heures'], sans['total_montant']) == (1.5, 3000)
