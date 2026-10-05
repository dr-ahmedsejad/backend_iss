"""
L'INVARIANT du portail en ligne : ce qui est écrit en ligne survit à la
publication, et rien ne casse la restauration.

Les deux niveaux d'exclusion (siga/settings/base.py) :
  * exclusion TOTALE (--exclude-table) — boîte de réception et tables propres
    à l'instance : la restauration ne les touche pas ;
  * exclusion des DONNÉES (--exclude-table-data) — la table est VIDÉE.
Les confondre a coûté, ailleurs, CHAQUE réclamation d'étudiant à chaque
publication, pendant que l'écran promettait de les préserver.

Ces tests valent pour TOUTES les tables des listes, pas seulement les
nouvelles.
"""
import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection

from tests._miroir_decor import api, decor, miroir  # noqa: F401


def _modeles_par_table():
    return {m._meta.db_table: m for m in apps.get_models(include_auto_created=True)}


def _relations(modele):
    return [f for f in modele._meta.get_fields()
            if f.is_relation and f.concrete or f.many_to_many]


# ── Les listes ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('table', settings.SYNC_EXCLUDE_TABLE + settings.SYNC_EXCLUDE_TABLE_DATA)
def test_chaque_nom_de_la_liste_est_une_vraie_table(table):
    """Une faute de frappe ferait ENTRER la table dans le dump — et son DROP
    effacerait les données en ligne sans un message."""
    assert table in _modeles_par_table(), f'{table} : aucun modèle ne porte ce nom de table'


def test_aucune_table_dans_les_deux_listes():
    assert not set(settings.SYNC_EXCLUDE_TABLE) & set(settings.SYNC_EXCLUDE_TABLE_DATA)


# Les tables ÉCRITES EN LIGNE, nommées ici par leur MODÈLE — et non lues dans
# la liste des réglages : retirer une table de la liste la retirerait aussi de
# ce que les tests vérifient, et le test passerait pendant que chaque
# publication efface la table. C'est exactement ce qui est arrivé ailleurs aux
# réclamations des étudiants. Toute nouvelle écriture en ligne s'ajoute ICI.
ECRITES_EN_LIGNE = [
    'reclamations.Reclamation',
    'reclamations.ReclamationSeance',
    'saisie_en_ligne.SaisieNoteEnLigne',
    'authentication.IdentifiantPortail',
    'notifications.NotificationLecture',
    'notifications.AppareilPush',
    'notifications.PushEnvoye',
]


@pytest.mark.parametrize('modele', ECRITES_EN_LIGNE)
def test_chaque_table_ecrite_en_ligne_est_dans_la_boite_de_reception(modele):
    table = apps.get_model(modele)._meta.db_table
    assert table in settings.BOITE_DE_RECEPTION
    assert table in settings.SYNC_EXCLUDE_TABLE, f'{table} serait écrasée à la publication'
    assert table not in settings.SYNC_EXCLUDE_TABLE_DATA, f'{table} serait VIDÉE à la publication'


def test_la_boite_de_reception_est_entierement_exclue():
    assert set(settings.BOITE_DE_RECEPTION) <= set(settings.SYNC_EXCLUDE_TABLE)
    for t in settings.BOITE_DE_RECEPTION:
        assert t not in settings.SYNC_EXCLUDE_TABLE_DATA, \
            f'{t} est écrite en ligne : en exclusion de DONNÉES, chaque publication la vide'


# ── Aucune relation, dans le modèle ───────────────────────────────────────────

@pytest.mark.parametrize('table', settings.BOITE_DE_RECEPTION)
def test_la_boite_de_reception_n_a_aucune_relation(table):
    """Pas même une clé étrangère sans contrainte : elle ne bloque pas la
    restauration, mais elle casse l'affichage dès que la cible a disparu."""
    modele = _modeles_par_table()[table]
    assert _relations(modele) == [], f'{table} : relations {[f.name for f in _relations(modele)]}'


@pytest.mark.parametrize('table', settings.SYNC_EXCLUDE_TABLE)
def test_aucune_table_publiee_ne_pointe_vers_une_table_exclue(table):
    """Sinon le DROP de la table exclue… n'a pas lieu, mais la contrainte de la
    table publiée vise des lignes que le miroir n'a peut-être pas."""
    modele = _modeles_par_table()[table]
    exclues = set(settings.SYNC_EXCLUDE_TABLE)
    for m in apps.get_models(include_auto_created=True):
        if m._meta.db_table in exclues:
            continue
        for f in m._meta.get_fields():
            if f.is_relation and f.concrete and f.related_model is modele:
                pytest.fail(f'{m._meta.db_table}.{f.name} pointe vers {table}')


@pytest.mark.parametrize('table', settings.TABLES_PROPRES_A_L_INSTANCE)
def test_une_table_propre_a_l_instance_n_a_que_des_relations_sans_contrainte(table):
    """Le journal d'audit garde ses relations pour l'ORM, mais sans contrainte :
    chaque instance garde le sien, et son écran tolère un compte disparu."""
    modele = _modeles_par_table()[table]
    for f in _relations(modele):
        assert getattr(f, 'db_constraint', True) is False, f'{table}.{f.name} a une contrainte'


# ── Aucune contrainte, lue dans la BASE ───────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize('table', settings.SYNC_EXCLUDE_TABLE)
def test_aucune_contrainte_de_cle_etrangere_en_base(table):
    """Le modèle peut dire une chose, la base une autre : on lit la base."""
    with connection.cursor() as c:
        if table not in connection.introspection.table_names(c):
            pytest.skip(f'{table} absente de la base de test')
        contraintes = connection.introspection.get_constraints(c, table)
    fks = {n: v['foreign_key'] for n, v in contraintes.items() if v.get('foreign_key')}
    assert fks == {}, f'{table} : {fks}'


# ── La commande réellement passée à pg_dump ───────────────────────────────────

def test_la_commande_pg_dump():
    from apps.publication.services import commande_pg_dump
    args = commande_pg_dump('/tmp/x.sql')
    assert '--clean' in args and '--if-exists' in args
    assert ['--format', 'plain'] == args[args.index('--format'):args.index('--format') + 2]

    def valeurs(option):
        return [args[i + 1] for i, a in enumerate(args) if a == option]

    assert sorted(valeurs('--exclude-table')) == sorted(settings.SYNC_EXCLUDE_TABLE)
    assert sorted(valeurs('--exclude-table-data')) == sorted(settings.SYNC_EXCLUDE_TABLE_DATA)
    for t in settings.BOITE_DE_RECEPTION:
        assert t not in valeurs('--exclude-table-data')


# ── La publication ────────────────────────────────────────────────────────────

class FauxProcessus:
    """Remplace subprocess.run : pg_dump écrit un fichier, ssh répond."""

    def __init__(self, ssh_code=0, ssh_sortie=b'OK publication appliquee', pg_code=0):
        self.appels = []
        self.ssh_code, self.ssh_sortie, self.pg_code = ssh_code, ssh_sortie, pg_code

    def __call__(self, args, **kw):
        import subprocess
        self.appels.append(args)
        if args[0] == settings.SYNC_PG_DUMP_BIN:
            if self.pg_code == 0:
                with open(args[args.index('--file') + 1], 'wb') as f:
                    f.write(b'-- dump de test\n')
            return subprocess.CompletedProcess(args, self.pg_code, '', 'pg_dump: erreur simulée')
        kw['stdin'].read()
        return subprocess.CompletedProcess(args, self.ssh_code, self.ssh_sortie, b'')


@pytest.mark.django_db
def test_publier_sans_cible_ne_transfere_rien_et_le_dit(decor, settings, monkeypatch):
    from apps.publication.models import PublicationJournal
    settings.SYNC_SSH_HOST = ''
    faux = FauxProcessus()
    monkeypatch.setattr('apps.publication.services.subprocess.run', faux)
    r = api(decor['admin']).post('/api/v1/synchronisation/publier/')
    assert r.status_code == 200, r.data
    assert r.data['statut'] == 'construit' and r.data['transfere'] is False
    assert 'RIEN' in r.data['reponse_vps']
    assert len(faux.appels) == 1                       # pg_dump seulement
    assert PublicationJournal.objects.count() == 1


@pytest.mark.django_db
def test_publier_avec_cible_une_seule_commande_ssh_portant_l_empreinte(decor, settings, monkeypatch):
    import hashlib
    settings.SYNC_SSH_HOST = 'miroir.example'
    faux = FauxProcessus()
    monkeypatch.setattr('apps.publication.services.subprocess.run', faux)
    r = api(decor['admin']).post('/api/v1/synchronisation/publier/')
    assert r.status_code == 200, r.data
    ssh = [a for a in faux.appels if a[0] == settings.SYNC_SSH_BIN]
    assert len(ssh) == 1
    attendu = hashlib.sha256(b'-- dump de test\n').hexdigest()
    assert ssh[0][-2:] == ['publier', attendu]
    assert r.data['statut'] == 'publie' and r.data['transfere'] is True
    assert r.data['sha256'] == attendu


@pytest.mark.django_db
def test_une_publication_en_echec_laisse_une_ligne_d_historique(decor, settings, monkeypatch):
    from apps.publication.models import PublicationJournal
    monkeypatch.setattr('apps.publication.services.subprocess.run', FauxProcessus(pg_code=1))
    r = api(decor['admin']).post('/api/v1/synchronisation/publier/')
    assert r.status_code == 502
    j = PublicationJournal.objects.get()
    assert j.statut == 'echec' and 'pg_dump' in j.erreur
    h = api(decor['admin']).get('/api/v1/synchronisation/historique/')
    assert [x['statut'] for x in h.data] == ['echec']


@pytest.mark.django_db
def test_le_miroir_qui_repond_mal_est_un_echec(decor, settings, monkeypatch):
    settings.SYNC_SSH_HOST = 'miroir.example'
    monkeypatch.setattr('apps.publication.services.subprocess.run',
                        FauxProcessus(ssh_code=3, ssh_sortie=b'ECHEC empreinte'))
    r = api(decor['admin']).post('/api/v1/synchronisation/publier/')
    assert r.status_code == 502 and r.data['statut'] == 'echec'


@pytest.mark.django_db
@pytest.mark.parametrize('adresse,methode', [
    ('/api/v1/synchronisation/publier/', 'post'),
    ('/api/v1/synchronisation/historique/', 'get'),
    ('/api/v1/synchronisation/plan/', 'get'),
])
@pytest.mark.parametrize('role', ['it', 'de'])
def test_un_non_administrateur_est_refuse(decor, adresse, methode, role):
    r = getattr(api(decor[role]), methode)(adresse)
    assert r.status_code == 403


@pytest.mark.django_db
def test_la_publication_est_refusee_sur_le_miroir_par_la_fonction_elle_meme(decor, miroir):
    from apps.publication.services import PublicationRefusee, publier
    with pytest.raises(PublicationRefusee):
        publier(decor['admin'])


# ── L'intercepteur ────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_en_miroir_une_ecriture_hors_liste_est_refusee(decor, miroir):
    r = api(decor['admin']).post('/api/v1/salles/', {'nom': 'X', 'capacite': 1})
    assert r.status_code == 403
    assert 'serveur de travail' in r.json()['error']


@pytest.mark.django_db
@pytest.mark.parametrize('adresse', [
    '/api/v1/auth/profil/',                      # sous /auth/, mais pas dans la liste
    '/api/v1/auth/rbac/toggle/',
    '/api/v1/reclamations/periodes/',            # sous /reclamations/, mais pas dans la liste
    '/api/v1/portail/profil/',
    '/api/v1/evaluations/notes/saisir-bulk/',    # la note OFFICIELLE
    '/api/v1/synchronisation/publier/',
])
def test_en_miroir_les_voisins_de_la_liste_blanche_sont_refuses(decor, miroir, adresse):
    """Une liste par PRÉFIXE les laisserait passer — et la publication suivante
    les effacerait."""
    assert api(decor['admin']).post(adresse, {}).status_code == 403


@pytest.mark.django_db
def test_en_miroir_une_ecriture_listee_passe_l_intercepteur(decor, miroir):
    r = api(decor['users_etu'][0]).post('/api/v1/portail/reclamations/',
                                        {'type_reclamation': 'autre', 'motif': 'Bonjour'})
    assert r.status_code == 201, r.content


@pytest.mark.django_db
def test_en_miroir_la_lecture_passe(decor, miroir):
    assert api(decor['admin']).get('/api/v1/salles/').status_code == 200


@pytest.mark.django_db
def test_sur_le_serveur_de_travail_l_intercepteur_ne_fait_rien(decor):
    r = api(decor['admin']).post('/api/v1/salles/', {'nom': 'Salle 9', 'capacite': 30})
    assert r.status_code != 403


@pytest.mark.django_db
@pytest.mark.parametrize('mode,attendu', [(False, 'travail'), (True, 'miroir')])
def test_l_adresse_publique_dit_le_role(settings, mode, attendu):
    settings.MIRROR_MODE = mode
    r = api().get('/api/v1/instance/')
    assert r.status_code == 200
    assert r.data['mode'] == attendu and r.data['lecture_seule'] is mode
