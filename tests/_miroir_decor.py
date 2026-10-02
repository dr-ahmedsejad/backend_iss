"""
Le décor des tests du portail en ligne (mode miroir).

Un établissement, une année active, une filière, un groupe ; deux éléments ;
deux enseignants qui enseignent chacun le leur (pointages) ; deux étudiants
inscrits à l'élément du premier ; une session ouverte ; le personnel.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


def api(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


@pytest.fixture
def miroir(settings):
    """Bascule l'instance en mode miroir pour ce test."""
    settings.MIRROR_MODE = True
    return settings


@pytest.fixture
def decor(db):
    from apps.absence.models import Etudiant
    from apps.departement.models import Departement
    from apps.em.models import EM
    from apps.evaluations.models import SessionEvaluation
    from apps.inscriptions.models import (InscriptionAdministrative, InscriptionElement,
                                          InscriptionPedagogique)
    from apps.parametres.models import Institution, Niveau, Semestre, Year
    from apps.prof.models import Prof
    from apps.scolarite.models import Filiere
    from apps.suivi.models import SuiviePointage

    User = get_user_model()
    inst = Institution.objects.create(acronyme='ISS', nom='Institut', est_principale=True)
    niveau = Niveau.objects.create(niveau='L1')
    filiere = Filiere.objects.create(code='SEA', intitule_fr='Statistique', institution=inst)
    dept = Departement.objects.create(nom='G1', institution=inst, filiere=filiere, niveau=niveau,
                                      annee_universitaire='2026-2027')
    sem = Semestre.objects.create(code_semestre='S1', semestre='Semestre 1',
                                  niveau_semestre=niveau, type_semestre='I')
    annee = Year.objects.create(annee='2026-2027', est_active=True)
    em_a = EM.objects.create(code_em='ST11', intitule='Probabilités', departement=dept,
                             semestre=sem, institution=inst)
    em_b = EM.objects.create(code_em='ST12', intitule='Algèbre', departement=dept,
                             semestre=sem, institution=inst)

    def compte(username, role, **kw):
        return User.objects.create_user(username=username, email=f'{username}@t.mr',
                                        password='Ancien-mdp-123', role=role, **kw)

    admin = compte('adm', 'admin')
    it = compte('info', 'IT')
    de = compte('de', 'DE')
    ens_a = compte('ens_a', 'enseignant')
    ens_b = compte('ens_b', 'enseignant')
    prof_a = Prof.objects.create(NNI=111, nom='Prof A', type='permanent', user=ens_a)
    prof_b = Prof.objects.create(NNI=222, nom='Prof B', type='permanent', user=ens_b)
    pointage_a = SuiviePointage.objects.create(prof=prof_a, institution=inst, em=em_a,
                                               annee_universitaire='2026-2027',
                                               numero_semaine=3, type_semestre='I')
    pointage_b = SuiviePointage.objects.create(prof=prof_b, institution=inst, em=em_b,
                                               annee_universitaire='2026-2027',
                                               numero_semaine=3, type_semestre='I')

    etudiants, ies, users_etu = [], [], []
    for i, matricule in enumerate(('23001', '23002')):
        u = compte(f'cni{matricule}', 'etudiant', doit_changer_mdp=True)
        e = Etudiant.objects.create(matricule=matricule, nom=f'Etudiant {matricule}',
                                    departement=dept, genre='M', user=u)
        adm = InscriptionAdministrative.objects.create(
            etudiant=e, annee_univ=annee, filiere=filiere, institution=inst,
            niveau=1, numero_inscription=f'INS-{matricule}')
        ped = InscriptionPedagogique.objects.create(inscription_admin=adm, semestre=sem)
        ies.append(InscriptionElement.objects.create(inscription_ped=ped, em=em_a))
        etudiants.append(e)
        users_etu.append(u)

    session = SessionEvaluation.objects.create(annee_univ=annee, institution=inst,
                                               type_session='normale', type_semestre='Impairs',
                                               est_ouverte=True)
    return dict(inst=inst, annee=annee, filiere=filiere, dept=dept, sem=sem,
                em_a=em_a, em_b=em_b, admin=admin, it=it, de=de,
                ens_a=ens_a, ens_b=ens_b, prof_a=prof_a, prof_b=prof_b,
                pointage_a=pointage_a, pointage_b=pointage_b,
                etudiants=etudiants, users_etu=users_etu, ies=ies, session=session)
