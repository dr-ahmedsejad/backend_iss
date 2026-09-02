"""
Où en est la rentrée ? — diagnostic du rattachement des réinscrits.

`ReinscriptionService.executer` crée l'`InscriptionAdministrative` de l'année
suivante, et s'arrête là : il ne touche jamais à `Etudiant.departement`. Le
rattachement de l'étudiant à un groupe de l'année est une étape distincte, faite
à la main sur « Affecter étudiants à un groupe » — et rien, aujourd'hui, ne dit
qu'elle reste à faire. L'écran des progressions affiche « exécutées » : le
travail a l'air fini.

Mesuré sur la base `iss` le 02/09/2026 pour 2026-2027 : 100 étudiants
réinscrits, **aucun** rattaché à un groupe de cette année, et 86 d'entre eux
n'ont même pas de groupe où aller — 47 montent en LPSEA L2 et 39 en LPSEA L3,
niveaux pour lesquels aucun groupe n'existe.

Ce module ne corrige rien tout seul : il **rend l'écart visible**, cohorte par
cohorte, pour que l'écran puisse proposer l'unique action qui débloque chacune.
Il est en LECTURE SEULE — aucune écriture, aucun effet de bord.

Pourquoi le diagnostic plutôt que l'automatisme : `Etudiant.departement` est une
clé unique **sans année**, et `InscriptionAdministrative` ne porte aucun champ
groupe. Y écrire le groupe de N+1 au moment de l'exécution — en juillet —
écraserait le groupe de l'année en cours, qui tourne encore avec ses absences et
son pointage. Et le partage d'une promotion entre G1 et G2 est une décision
pédagogique qu'aucun service ne peut deviner.
"""
import re
from collections import defaultdict

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import RBACPermission

# Les quatre états d'une cohorte, dans l'ordre où ils se règlent.
ETAT_SANS_GROUPE = 'sans_groupe'
ETAT_A_AFFECTER  = 'a_affecter'
ETAT_PARTIEL     = 'partiel'
ETAT_COMPLET     = 'complet'


_CHIFFRE_DU_NIVEAU = re.compile(r'^\s*[A-Za-z ]*?(\d+)\s*$')


def _annee_etude_du_libelle(libelle: str):
    """« L3 » → 3, « E2 » → 2. `None` si le libellé ne porte pas d'année.

    `InscriptionAdministrative.niveau` est une ANNÉE D'ÉTUDE (1, 2, 3) ;
    `parametres.Niveau` porte un LIBELLÉ. Il faut donc rapprocher les deux, et
    **sans présumer du préfixe** : l'ISS nomme ses niveaux « L1, L2, L3 », mais
    d'autres instances du même code les nomment « E1, E2, E3 », voire « MP » et
    « MPSI » — qui ne portent aucune année et n'en désignent donc aucune.

    Écrire `f'L{n}'` marcherait ici et nulle part ailleurs. « Transversal » rend
    `None`, ce qui est juste : ce n'est pas une année d'étude.
    """
    trouve = _CHIFFRE_DU_NIVEAU.match(libelle or '')
    return int(trouve.group(1)) if trouve else None


def _libelles_par_annee_etude() -> dict:
    """{3: « L3 »} — le libellé RÉEL du référentiel, pour l'affichage.

    On ne fabrique pas « L3 » : on va le chercher. L'écran doit dire « E3 » là
    où l'établissement dit « E3 ».
    """
    from apps.parametres.models import Niveau

    par_annee = {}
    for n in Niveau.objects.order_by('id'):
        a = _annee_etude_du_libelle(n.niveau)
        if a is not None:
            par_annee.setdefault(a, n.niveau)
    return par_annee


def _etat(effectif: int, affectes: int, nb_groupes: int) -> str:
    """Les quatre états, dans l'ordre où ils se testent.

    « Rien à faire » passe AVANT « personne d'affecté » : une cohorte à zéro
    inscrit satisfait les deux, et l'ordre inverse la présenterait comme restant
    à traiter. `frontend_iss/lib/rentree-etat.ts` applique la même règle, dans
    le même ordre — les deux ne doivent pas diverger.
    """
    if nb_groupes == 0:
        return ETAT_SANS_GROUPE
    if affectes >= effectif:
        return ETAT_COMPLET
    if affectes == 0:
        return ETAT_A_AFFECTER
    return ETAT_PARTIEL


def _cohortes_brutes(annee):
    """Les couples (filière, niveau) attendus cette année, et combien sont déjà
    rattachés à un groupe DE CETTE ANNÉE.

    Une ligne par étudiant : `InscriptionAdministrative` est unique par
    (étudiant, année).

    « Affecté » se lit sur une seule chose — le groupe de l'étudiant appartient à
    l'année visée. C'est le seul signal disponible, `Etudiant.departement` n'ayant
    pas d'année, et c'est précisément le fond du problème.
    """
    from django.db.models import Count, Q

    from apps.inscriptions.models import InscriptionAdministrative

    return (InscriptionAdministrative.objects
            .filter(annee_univ=annee)
            .values('filiere_id', 'filiere__code', 'filiere__intitule_fr', 'niveau')
            .annotate(
                effectif=Count('id'),
                affectes=Count('id', filter=Q(
                    etudiant__departement__annee_universitaire=annee.annee)),
            )
            .order_by('filiere__code', 'niveau'))


def _groupes_de_l_annee(annee):
    """Les groupes PLANIFIABLES de l'année, indexés par (filière, code niveau).

    Les conteneurs d'inscription sont écartés : ils reçoivent les étudiants au
    moment de l'inscription, ils ne sont pas la classe où l'on suit les cours.
    """
    from apps.departement.models import Departement

    par_cle = defaultdict(list)
    for d in (Departement.objects
              .filter(annee_universitaire=annee.annee, is_container=False)
              .select_related('filiere', 'niveau')
              .order_by('nom')):
        # La clé est l'ANNÉE D'ÉTUDE lue sur le libellé, jamais le libellé
        # lui-même : « L2 » ici, « E2 » ailleurs, c'est la même deuxième année.
        annee_etude = _annee_etude_du_libelle(d.niveau.niveau if d.niveau_id else '')
        par_cle[(d.filiere_id, annee_etude)].append(d)
    return par_cle


def _totaux(annee):
    """(inscrits, affectés) pour l'année — sert au choix automatique."""
    from django.db.models import Count, Q

    from apps.inscriptions.models import InscriptionAdministrative

    agg = (InscriptionAdministrative.objects
           .filter(annee_univ=annee)
           .aggregate(
               total=Count('id'),
               affectes=Count('id', filter=Q(
                   etudiant__departement__annee_universitaire=annee.annee)),
           ))
    return agg['total'] or 0, agg['affectes'] or 0


def _annee_a_preparer():
    """L'année dont la rentrée est à préparer : **la plus récente**, un point.

    Celle qui a des inscriptions et dont l'année est la plus grande — qu'elle
    soit terminée ou non. Si elle est complète, il n'y a rien à signaler et le
    bandeau s'éteint ; l'écran affiche « la rentrée est prête ».

    **On ne remonte JAMAIS dans le passé**, et c'est le point délicat. Un
    étudiant n'a qu'un seul `departement`, sans année : dès qu'il monte d'année,
    il « quitte » rétroactivement la précédente. Toute année révolue paraît donc
    incomplète — 2025-2026 affiche 0 rattachés sur 141, 2024-2025 en affiche 2
    sur 103 — et ce chiffre ne veut rien dire.

    Une version précédente cherchait « la plus récente qui soit INCOMPLÈTE », et
    se rabattait sur les suivantes. Le jour où 2026-2027 est passée à 100 sur
    100, elle a donc reculé jusqu'à 2025-2026 et annoncé 141 étudiants à
    affecter — une rentrée faite depuis un an. Le garde-fou était décrit ici
    sans être appliqué plus bas : il l'est maintenant.

    Le paramètre `?annee=` reste accepté pour un examen délibéré ou pour les
    tests ; aucun écran n'y renvoie.
    """
    from apps.inscriptions.models import InscriptionAdministrative
    from apps.parametres.models import Year

    avec_inscrits = set(
        InscriptionAdministrative.objects.values_list('annee_univ_id', flat=True))
    if not avec_inscrits:
        return None
    return Year.objects.filter(pk__in=avec_inscrits).order_by('-annee').first()


class RentreeView(APIView):
    """
    GET /api/v1/inscriptions/rentree/[?annee=<id>]

    Rend l'état du rattachement, cohorte par cohorte. Lecture seule.

    Le droit réutilisé est `insc_progression` : c'est la suite immédiate du même
    travail, et un droit de plus n'aurait fait qu'un écran de permissions plus
    long à remplir sans rien distinguer de neuf.
    """
    permission_classes = [RBACPermission]
    required_module    = 'insc_progression'

    def get(self, request):
        from apps.parametres.models import Semaine, Year

        demandee = request.query_params.get('annee')
        if demandee:
            if not str(demandee).isdigit():
                return Response({'error': 'annee doit être un identifiant.'},
                                status=status.HTTP_400_BAD_REQUEST)
            annee = Year.objects.filter(pk=demandee).first()
            if annee is None:
                return Response({'error': f'Année #{demandee} introuvable.'},
                                status=status.HTTP_404_NOT_FOUND)
            choisie_auto = False
        else:
            annee = _annee_a_preparer()
            choisie_auto = True

        if annee is None:
            # Aucune inscription nulle part : il n'y a pas de rentrée à préparer.
            return Response({
                'annee': None, 'choisie_automatiquement': True,
                'total_inscrits': 0, 'total_affectes': 0,
                'cohortes': [], 'groupes_sans_effectif': [], 'semaines_saisies': 0,
            })

        groupes  = _groupes_de_l_annee(annee)
        libelles = _libelles_par_annee_etude()
        cohortes, servies = [], set()
        total_inscrits = total_affectes = 0

        for ligne in _cohortes_brutes(annee):
            # Le libellé du référentiel quand il existe ; sinon on écrit
            # l'année d'étude telle quelle plutôt que d'inventer un préfixe.
            code = libelles.get(ligne['niveau'], str(ligne['niveau'] or ''))
            cle  = (ligne['filiere_id'], ligne['niveau'])
            servies.add(cle)
            candidats = groupes.get(cle, [])
            effectif, affectes = ligne['effectif'], ligne['affectes']
            total_inscrits += effectif
            total_affectes += affectes

            cohortes.append({
                'filiere': {
                    'id':       ligne['filiere_id'],
                    'code':     ligne['filiere__code'],
                    'intitule': ligne['filiere__intitule_fr'],
                },
                'niveau':      ligne['niveau'],
                'niveau_code': code,
                'effectif':    effectif,
                'affectes':    affectes,
                'groupes': [{'id': d.pk, 'nom': d.nom} for d in candidats],
                'etat': _etat(effectif, affectes, len(candidats)),
            })

        # Les groupes que personne ne réclame. Ce n'est pas une erreur — ils
        # attendent peut-être de nouveaux entrants — mais c'est une chose à
        # savoir quand on cherche pourquoi une cohorte n'a nulle part où aller.
        sans_effectif = [
            {'id': d.pk, 'nom': d.nom,
             'filiere_code': d.filiere.code if d.filiere_id else None,
             'niveau_code': d.niveau.niveau if d.niveau_id else ''}
            for cle_g, liste in sorted(
                groupes.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
            if cle_g not in servies
            for d in liste
        ]

        # Second blocage de la rentrée, indépendant du premier : sans calendrier,
        # aucun patron d'emploi du temps ne peut être dupliqué sur les semaines.
        # On compte les SEMAINES, pas les lignes — une ligne `Semaine` est un jour.
        semaines = len({
            n for n in Semaine.objects
            .filter(annee_universitaire=annee.annee, type_semaine=Semaine.TYPE_COURS)
            .values_list('numero_semaine', flat=True)
            if n is not None
        })

        return Response({
            'annee': {'id': annee.pk, 'annee': annee.annee},
            'choisie_automatiquement': choisie_auto,
            'total_inscrits': total_inscrits,
            'total_affectes': total_affectes,
            'cohortes': cohortes,
            'groupes_sans_effectif': sans_effectif,
            'semaines_saisies': semaines,
        })
