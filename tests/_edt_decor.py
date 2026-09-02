"""
Le décor des tests de planification hebdomadaire.

Il reproduit la structure RÉELLE de l'ISS, mesurée sur la base `iss` le
02/09/2026 — et non celle de l'ESP dont le moteur est repris :

  * `Departement.groupe` est VIDE partout. Le sous-groupe n'est écrit que dans
    le nom : « G1 », « SEA L2 - G1 », « SDID L2 G1 ». C'est la différence qui
    coûte le plus cher : porter la règle « mêmes étudiants » sans en tenir
    compte aurait refusé 1 107 couples de séances déjà présents dans le suivi,
    tous parfaitement légitimes ;
  * il n'y a pas de pôles, et un seul planificateur — le directeur des études ;
  * treize groupes planifiables sur vingt-six ont `filiere IS NULL`, par simple
    héritage ; HE et ST s'en distinguent par leur NIVEAU, « Transversal » ;
  * 56 éléments sur 207 n'ont pas de `module_lmd` mais tous portent un
    `semestre` — d'où la règle de parité lue sur `EM.semestre`.

Tout tourne sur la base jetable de pytest (sqlite en mémoire,
`--no-migrations`) : la base `iss` n'est jamais touchée.
"""
import datetime as dt
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from tests.factories.auth import UserFactory
from tests.factories.parametres import (CreneauFactory, InstitutionFactory,
                                        JourFactory, NiveauFactory,
                                        SeanceFactory, SemestreFactory,
                                        YearFactory)
from tests.factories.scolarite import FiliereFactory

ANNEE          = '2025-2026'
ANNEE_SUIVANTE = '2026-2027'

URL_SEANCE_TYPE = '/api/v1/edt/seances-type/'
URL_SEANCE      = '/api/v1/edt/seances/'
URL_GRILLE      = '/api/v1/edt/grilles/'


@pytest.fixture
def monde(db):
    """La structure de l'ISS : deux filières, des groupes de TD, deux
    enseignements transversaux."""
    from apps.departement.models import Departement
    from apps.em.models import EM
    from apps.modules.models import Module as ModuleLMD
    from apps.parametres.models import Semaine
    from apps.prof.models import Prof
    from apps.salle.models import Salle

    inst = InstitutionFactory(acronyme='ISS', est_principale=True)
    YearFactory(annee=ANNEE)
    YearFactory(annee=ANNEE_SUIVANTE)

    l1   = NiveauFactory(niveau='L1')
    l2   = NiveauFactory(niveau='L2')
    # Le niveau qui désigne un enseignement suivi par toute une promotion.
    # C'est son LIBELLÉ qui le signale, pas son identifiant — voir
    # `apps/edt/groupes.py`.
    tran = NiveauFactory(niveau='Transversal')

    s1 = SemestreFactory(code_semestre='S1', semestre='Semestre 1',
                         type_semestre='I', niveau_semestre=l1)
    s2 = SemestreFactory(code_semestre='S2', semestre='Semestre 2',
                         type_semestre='P', niveau_semestre=l1)
    s3 = SemestreFactory(code_semestre='S3', semestre='Semestre 3',
                         type_semestre='I', niveau_semestre=l2)

    f_sea  = FiliereFactory(code='SEA',  intitule_fr='Statistique et Économie Appliquée')
    f_sdid = FiliereFactory(code='SDID', intitule_fr='Science des Données')

    def dept(nom, filiere=None, niveau=l1, annee=ANNEE, container=False):
        # `groupe` est laissé VIDE, comme sur les 26 groupes de production.
        return Departement.objects.create(
            nom=nom, annee_universitaire=annee, institution=inst,
            filiere=filiere, niveau=niveau, groupe='', is_container=container)

    depts = {
        # Une filière scindée en deux groupes de TD. Le sous-groupe n'est que
        # dans le nom : c'est le cas que la règle de l'ESP aurait confondu avec
        # « les mêmes étudiants », interdisant tout cours simultané.
        'G1':          dept('G1', f_sea, l1),
        'G2':          dept('G2', f_sea, l1),
        # La même chose, écrite autrement — les deux orthographes coexistent
        # en production.
        'SEA L2 G1':   dept('SEA L2 - G1', f_sea, l2),
        'SEA L2 G2':   dept('SEA L2 - G2', f_sea, l2),
        # Un groupe entier d'une AUTRE filière, même niveau.
        'SDID L2':     dept('SDID L2', f_sdid, l2),
        # Héritage : pas de filière, le sous-groupe dans le nom. Sa souche
        # « SDID L2 » est celle du groupe entier ci-dessus.
        'SDID L2 G1':  dept('SDID L2 G1', None, l2),
        # Les deux enseignements transversaux, reconnaissables au niveau.
        'HE':          dept('HE', None, tran),
        'ST':          dept('ST', None, tran),
        # Un transversal de l'année SUIVANTE : il ne doit croiser personne ici.
        'HE 26':       dept('HE', None, tran, annee=ANNEE_SUIVANTE),
        # Un conteneur d'inscription : jamais planifié.
        'STAT L1':     dept('STAT L1', f_sea, l1, container=True),
    }

    def em(code, semestre, filiere, avec_module=True):
        module = None
        if avec_module:
            module = ModuleLMD.objects.create(
                code='MOD-' + code, intitule_fr='UE ' + code, semestre=semestre,
                filiere=filiere, institution=inst, credits=6,
                coefficient=Decimal('1.00'),
                seuil_compensation=Decimal('10.00'))
        return EM.objects.create(
            code_em=code, intitule='Cours ' + code, filiere=filiere,
            semestre=semestre, module_lmd=module, institution=inst,
            seuil_eliminatoire=Decimal('6.00'))

    ems = {
        'SEA11':  em('SEA11',  s1, f_sea),
        'SEA12':  em('SEA12',  s1, f_sea),
        # Semestre PAIR : posé dans une grille impaire, il se planifierait sans
        # jamais apparaître au suivi.
        'SEA24':  em('SEA24',  s2, f_sea),
        'SEA31':  em('SEA31',  s3, f_sea),
        'SDID31': em('SDID31', s3, f_sdid),
        # Sans `module_lmd` — 56 éléments sont dans ce cas à l'ISS. La parité
        # doit malgré tout être vérifiée, via `EM.semestre`.
        'HE11':   em('HE11', s1, f_sea, avec_module=False),
        'HE24':   em('HE24', s2, f_sea, avec_module=False),
        'ST11':   em('ST11', s1, f_sdid, avec_module=False),
    }

    jours    = {n: JourFactory(jour=n)
                for n in ('Lundi', 'Mardi', 'Mercredi')}
    creneaux = {n: CreneauFactory(creneau=n, ordre=i, duree=1.5)
                for i, n in enumerate(('08h00-09h30', '09h45-11h15',
                                       '11h30-13h00'), start=1)}
    cm    = SeanceFactory(type_seance='CM')
    td    = SeanceFactory(type_seance='TD')
    sport = SeanceFactory(type_seance='Sport', is_special=True)

    profs  = {n: Prof.objects.create(nom=n, NNI=1000 + i, actif=True)
              for i, n in enumerate(('Moustapha', 'Abderahmane'), start=1)}
    salles = {n: Salle.objects.create(nom=n) for n in ('101', '102')}

    # Une ligne `Semaine` est un JOUR. Deux semaines suffisent à montrer que la
    # génération ne prend que celle qu'on lui demande.
    #
    # Les dates partent du lundi de la semaine EN COURS, et non d'une date
    # fixe : le socle refuse à un non-administrateur la génération d'une
    # semaine close depuis plus de `SUIVI_GRACE_DAYS_AFTER_WEEK_END` jours
    # (`apps/suivi/views.py`). Une date figée aurait fait passer les tests de
    # périmètre le jour où on les écrit, puis échouer tout seuls ensuite.
    semaines = {}
    aujourdhui = dt.date.today()
    base = aujourdhui - dt.timedelta(days=aujourdhui.weekday())   # lundi
    for num in (1, 2):
        for i, nom in enumerate(('Lundi', 'Mardi', 'Mercredi')):
            semaines[(num, nom)] = Semaine.objects.create(
                numero_semaine=num, jour_fk=jours[nom],
                date=base + dt.timedelta(days=(num - 1) * 7 + i),
                annee_universitaire=ANNEE, type_semestre='I',
                type_semaine='cours')

    return dict(inst=inst, l1=l1, l2=l2, tran=tran, s1=s1, s2=s2, s3=s3,
                f_sea=f_sea, f_sdid=f_sdid, depts=depts, ems=ems, jours=jours,
                creneaux=creneaux, cm=cm, td=td, sport=sport, profs=profs,
                salles=salles, semaines=semaines)


def _droit(role, module_code, action_code):
    """Accorde `module:action` à un rôle, en recréant la ligne RBAC.

    `get_or_create` et non les factories : le même couple module/action sert à
    plusieurs rôles, et sa contrainte d'unicité rejetterait le second appel.
    """
    from apps.authentication.models import (Action, Module, ModuleAction,
                                            RoleDefault)
    mod, _ = Module.objects.get_or_create(
        code=module_code, defaults={'nom': module_code})
    act, _ = Action.objects.get_or_create(
        code=action_code, defaults={'nom': action_code})
    ma, _  = ModuleAction.objects.get_or_create(module=mod, action=act)
    RoleDefault.objects.update_or_create(
        role=role, module_action=ma, defaults={'allowed': True})


@pytest.fixture
def gens(monde):
    """Les comptes de l'ISS : un directeur des études, un admin, un orphelin.

    C'est la situation mesurée en production : deux comptes seulement portent
    des `managed_departements`, et 183 n'en ont aucun. Le compte sans
    périmètre n'est pas un cas d'école — c'est le cas le plus fréquent.
    """
    for action in ('voir', 'modifier', 'supprimer'):
        _droit('DE', 'emplois', action)
    for action in ('voir', 'modifier'):
        _droit('DE', 'suivi_saisie', action)
        _droit('admin', 'suivi_saisie', action)

    admin = UserFactory(username='u_admin', role='admin', is_superuser=True)
    # Le directeur des études : le planificateur de l'ISS.
    de       = UserFactory(username='u_de',       role='DE')
    autre_de = UserFactory(username='u_autre_de', role='DE')
    orphelin = UserFactory(username='u_orphelin', role='DE')

    for nom in ('G1', 'G2', 'HE', 'ST'):
        de.managed_departements.add(monde['depts'][nom])
    for nom in ('SEA L2 G1', 'SEA L2 G2', 'SDID L2'):
        autre_de.managed_departements.add(monde['depts'][nom])

    return dict(admin=admin, de=de, autre_de=autre_de, orphelin=orphelin)


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def grille(monde, dept, type_semestre='I'):
    from apps.edt.models import GrilleType
    return GrilleType.objects.create(
        departement=monde['depts'][dept], type_semestre=type_semestre,
        annee_universitaire=ANNEE, actif=True)


def case(monde, g, jour='Lundi', creneau='08h00-09h30', em=None,
         prof='Moustapha', salle='101', type_seance='cm'):
    """Le payload d'une case de patron, tel que l'envoie l'écran."""
    return {
        'grille':         g.pk,
        'jour_fk':        monde['jours'][jour].pk,
        'creneau_fk':     monde['creneaux'][creneau].pk,
        'em':             monde['ems'][em].pk if em else None,
        'prof':           monde['profs'][prof].pk if prof else None,
        'salle':          monde['salles'][salle].pk if salle else None,
        'type_seance_fk': monde[type_seance].pk,
    }


def seance(monde, dept, numero=1, jour='Lundi', creneau='08h00-09h30',
           em=None, prof='Moustapha', salle='101', type_seance='cm'):
    """Le payload d'une séance datée."""
    return {
        'departement':    monde['depts'][dept].pk,
        'semaine':        monde['semaines'][(numero, jour)].pk,
        'creneau_fk':     monde['creneaux'][creneau].pk,
        'em':             monde['ems'][em].pk if em else None,
        'prof':           monde['profs'][prof].pk if prof else None,
        'salle':          monde['salles'][salle].pk if salle else None,
        'type_seance_fk': monde[type_seance].pk,
    }
