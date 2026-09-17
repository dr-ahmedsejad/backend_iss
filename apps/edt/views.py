"""
API de la planification hebdomadaire.

Le périmètre est celui du socle — `DepartementScopedMixin` — c'est-à-dire les
groupes délégués par `CustomUser.managed_departements`. Rien de nouveau à
configurer : les délégations en place valent pour ces écrans comme pour les
autres.

Le droit RBAC réutilisé est `emplois` : c'est le même métier, et un droit de
plus n'aurait fait qu'un écran de permissions plus long à remplir sans rien
distinguer de neuf.
"""
import logging

from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from core.mixins import AuditMixin, DepartementScopedMixin
from core.pagination import StandardPagination
from core.permissions import EDTDepartementPermission, RBACPermission

from .groupes import est_transversal
from .models import EmploiArchive, GrilleType, SeanceReelle, SeanceType
from .serializers import (EmploiArchiveSerializer, GrilleTypeListSerializer,
                          GrilleTypeSerializer, SeanceReelleSerializer,
                          SeanceTypeSerializer, VersionArchiveSerializer)
# `partager` est renommé : l'action de la vue porte le même nom, et deux
# `partager` dans un fichier finissent par se confondre à la lecture.
from .services.partage import partager as etendre_partage
from apps.parametres.feries import refuser_ajout, refuser_sur_ferie_isole
from .services.permutation import permuter_enseignants
from .services.archive import archiver_semaine
from .services.coherence import (ETAT_DIVERGENT, LIBELLES,
                                 etats_en_lot)
from .services.recopie import recopier

logger = logging.getLogger('siga')


def _ligne_case(entree: dict) -> None:
    """Écrit les deux dernières lignes d'une case imprimée.

    Les groupes sont TRIÉS : leur ordre en base n'a rien à dire, et
    « Groupe 3, Groupe 2, Groupe 1 » se lit de travers.

    Sur l'emploi du temps d'une SALLE, l'enseignant occupe sa ligne et les
    groupes la suivante : accolés, la liste des groupes poussait le nom hors
    de la case — or c'est le nom qu'on cherche d'abord quand on regarde qui
    occupe une salle.
    """
    groupes = ', '.join(sorted(entree['_lignes']))
    if entree.get('_enseignant'):
        entree['prof_nom'] = entree['_enseignant']
        entree['groupes']  = groupes
    else:
        entree['prof_nom'] = groupes
        entree['groupes']  = ''


def _wkhtmltopdf():
    """Où trouver wkhtmltopdf.

    Dans le PATH d'abord — c'est le cas d'une installation propre et d'un
    conteneur Linux — puis à l'emplacement d'installation par défaut sous
    Windows. Un chemin recopié en dur d'une vue à l'autre finit par être faux
    dans l'une des deux.
    """
    import shutil
    trouve = shutil.which('wkhtmltopdf')
    if trouve:
        return trouve
    sep = chr(92)
    return sep.join(['C:', 'Program Files', 'wkhtmltopdf', 'bin', 'wkhtmltopdf.exe'])
from .services.partage import propager, retirer_du_partage
from .services.planification import (dupliquer_grille, dupliquer_semaine,
                                     projeter_semaine, reprendre_semaine,
                                     semaines_du_lot)


class GrillePermission(EDTDepartementPermission):
    """Le patron d'un groupe se garde par le GROUPE.

    Une grille ne porte pas de champ `departement` dans son payload de la même
    façon que les autres écritures — elle en porte un, mais le contrôle
    générique ne s'applique qu'à l'action `create`. On l'étend ici aux
    modifications et aux suppressions : sans cela, quiconque a le droit de
    modifier les emplois pouvait récrire le patron d'un autre groupe.

    L'ISS n'a qu'un axe de périmètre — les groupes délégués via
    `managed_departements`. L'ESP en avait deux, le second étant le pôle ; il
    ne se porte pas, faute de pôles.
    """

    def _autorise(self, user, dept_id):
        if dept_id is None:
            return False
        return user.managed_departements.filter(pk=dept_id).exists()

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if u.is_superuser or request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        if request.method == 'POST' and getattr(view, 'action', None) == 'create':
            try:
                return self._autorise(u, int(request.data.get('departement')))
            except (TypeError, ValueError):
                return False
        return True

    def has_object_permission(self, request, view, obj):
        u = request.user
        if u.is_superuser or request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return self._autorise(u, obj.departement_id)


class GrilleTypeViewSet(DepartementScopedMixin, AuditMixin, viewsets.ModelViewSet):
    """Patrons d'emploi du temps — un par groupe et par parité de semestre."""
    queryset = GrilleType.objects.select_related('departement').all()
    permission_classes = [RBACPermission, GrillePermission]
    required_module    = 'emplois'
    filter_backends    = [DjangoFilterBackend, OrderingFilter]
    filterset_fields   = ['departement', 'type_semestre', 'annee_universitaire', 'actif']
    ordering           = ['annee_universitaire', 'departement']
    pagination_class   = StandardPagination

    def get_serializer_class(self):
        return GrilleTypeListSerializer if self.action == 'list' else GrilleTypeSerializer

    @action(detail=True, methods=['post'], url_path='dupliquer-vers')
    def dupliquer_vers(self, request, pk=None):
        """
        Recopie ce patron — ou quelques-unes de ses cases — vers d'autres
        groupes.

        POST { "departements": [69, 70], "seances": [12, 13] }
        `seances` omis : toute la grille.

        Le groupe cible n'a pas besoin d'avoir déjà une grille : elle est
        créée au passage, vide, ce qu'on vient précisément remplir. Une case
        déjà prise chez lui n'est jamais écrasée — elle est signalée.
        """
        source = self.get_object()
        demandes = request.data.get('departements') or []
        if not isinstance(demandes, (list, tuple)) or not demandes:
            return Response({'detail': 'Indiquez au moins un groupe cible.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Le droit s'apprécie groupe par groupe : chaque cible doit être dans
        # le périmètre de l'appelant.
        garde = GrillePermission()
        refuses = [int(d) for d in demandes
                   if not (request.user.is_superuser
                           or garde._autorise(request.user, int(d)))]
        if refuses:
            from apps.departement.models import Departement
            noms = list(Departement.objects.filter(pk__in=refuses)
                        .values_list('nom', flat=True))
            return Response(
                {'detail': f'Hors de votre périmètre : {", ".join(noms) or refuses}.'},
                status=status.HTTP_403_FORBIDDEN)

        resultat = recopier(source, [int(d) for d in demandes],
                            seances=request.data.get('seances'))
        if resultat['occupees']:
            resultat['detail'] = (
                "Certaines cases étaient déjà occupées chez la cible : elles "
                "n'ont pas été remplacées.")
        return Response(resultat, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='dupliquer')
    def dupliquer(self, request, pk=None):
        """
        Pose un emploi du temps sur les semaines demandées — depuis DEUX sources.

        POST { "source": "patron" }                   — la grille type (défaut)
        POST { "source": "semaine", "semaine_source": 1 }
                                                      — une semaine déjà bâtie

        Le lot de semaines cibles se désigne de trois façons, comme avant :
          { "numeros": [1,2,3] } · { "depuis": 3, "nombre": 5 } · { } (tout)

        Option { "ecraser": true } : reprend ce qu'une DUPLICATION avait posé —
        patron ou recopie — jamais une saisie manuelle ni une permutation.

        Une seule action pour les deux sources, délibérément : deux chemins
        concurrents pour remplir un emploi du temps finiraient par se
        contredire.

        Le lot cible est calculé dans l'année et la parité de CETTE grille, et
        la semaine source y est cherchée de la même façon : source et cibles
        partagent donc toujours le même espace de semaines. À l'ISS une ligne
        `Semaine` est identifiée par (année, parité, numéro) — deux semestres de
        même parité partagent leurs semaines, et il n'y a pas d'autre axe à
        contraindre.
        """
        from apps.parametres.models import Semaine

        grille = self.get_object()
        semaines = list(semaines_du_lot(
            grille.annee_universitaire, grille.type_semestre,
            numeros=request.data.get('numeros'),
            depuis=request.data.get('depuis'),
            nombre=request.data.get('nombre'),
        ))
        if not semaines:
            return Response(
                {'detail': "Aucune semaine de cours pour cette année et ce semestre. "
                           "Générez d'abord le calendrier dans Paramètres → Semaines."},
                status=status.HTTP_400_BAD_REQUEST)

        ecraser = bool(request.data.get('ecraser'))
        source = (request.data.get('source') or 'patron').strip().lower()

        if source == 'semaine':
            numero = request.data.get('semaine_source')
            try:
                numero = int(numero)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'Indiquez la semaine à recopier (`semaine_source`).'},
                    status=status.HTTP_400_BAD_REQUEST)
            # N'importe laquelle de ses lignes-jour suffit : elles portent toutes
            # l'année, la parité et le numéro qui identifient la semaine.
            ligne_source = Semaine.objects.filter(
                annee_universitaire=grille.annee_universitaire,
                type_semestre=grille.type_semestre,
                numero_semaine=numero).first()
            if ligne_source is None:
                return Response(
                    {'detail': "La semaine %s n'existe pas dans le calendrier de "
                               "cette période." % numero},
                    status=status.HTTP_400_BAD_REQUEST)
            resultat = dupliquer_semaine(grille.departement, ligne_source,
                                         semaines, ecraser=ecraser)
            resultat['source'] = 'semaine'
            resultat['semaine_source'] = numero
            touchees = {s.numero_semaine for s in semaines
                        if s.numero_semaine not in (None, numero)}
        elif source == 'patron':
            resultat = dupliquer_grille(grille, semaines, ecraser=ecraser)
            resultat['source'] = 'patron'
            touchees = {s.numero_semaine for s in semaines
                        if s.numero_semaine is not None}
        else:
            return Response(
                {'detail': "`source` attend « patron » ou « semaine »."},
                status=status.HTTP_400_BAD_REQUEST)

        resultat['semaines'] = sorted(touchees)
        return Response(resultat, status=status.HTTP_200_OK)


    @action(detail=True, methods=['post'], url_path='reprendre-semaine')
    def reprendre_semaine_action(self, request, pk=None):
        """
        Promeut une semaine réelle en patron — le sens inverse de `dupliquer`.

        POST { "semaine_source": 3, "ecraser": false }

        Deux écarts assumés entre la semaine et le patron obtenu : une séance
        annulée n'entre pas, et une permutation revient à son titulaire. Le
        compte rendu les chiffre tous les deux.

        La semaine est cherchée d'abord dans la PARITÉ de ce patron, puis sans
        elle : ainsi une semaine qui n'existe que dans l'autre parité est
        trouvée, et refusée avec son motif, plutôt que déclarée introuvable.
        """
        from apps.parametres.models import Semaine

        grille = self.get_object()
        try:
            numero = int(request.data.get('semaine_source'))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'Indiquez la semaine à reprendre (`semaine_source`).'},
                status=status.HTTP_400_BAD_REQUEST)

        dans_l_annee = Semaine.objects.filter(
            annee_universitaire=grille.annee_universitaire,
            numero_semaine=numero)
        ligne = (dans_l_annee.filter(type_semestre=grille.type_semestre).first()
                 or dans_l_annee.first())
        if ligne is None:
            return Response(
                {'detail': "La semaine %s n'existe pas dans le calendrier de "
                           "%s." % (numero, grille.annee_universitaire)},
                status=status.HTTP_400_BAD_REQUEST)

        resultat = reprendre_semaine(
            grille, ligne, ecraser=bool(request.data.get('ecraser')))
        return Response(resultat, status=status.HTTP_200_OK)


class SeanceTypePermission(EDTDepartementPermission):
    """Le patron se garde comme la séance datée.

    Le payload d'une case ne porte pas de `departement` mais une `grille` : le
    contrôle générique ne trouvait donc rien à vérifier et laissait tout
    passer. N'importe qui ayant le droit de modifier les emplois pouvait
    récrire le patron d'un autre groupe.

    On traduit ici la grille en groupe, puis on applique la règle du périmètre :
    mes groupes délégués. L'ESP y ajoutait un second axe — le pôle de
    l'enseignement — qui ne se porte pas, faute de pôles à l'ISS.
    """

    @staticmethod
    def _dept_de_la_grille(grille_id):
        if not grille_id:
            return None
        return (GrilleType.objects.filter(pk=grille_id)
                .values_list('departement_id', flat=True).first())

    def _autorise(self, user, dept_id, em_id=None):
        # `em_id` n'est plus consulté : il ne servait qu'à départager deux
        # pôles sur une même grille. On garde le paramètre pour que la
        # signature reste celle de l'ESP, et le portage relisible.
        if dept_id is None:
            return False
        return user.managed_departements.filter(pk=dept_id).exists()

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if u.is_superuser or request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        if request.method == 'POST':
            try:
                dept = self._dept_de_la_grille(int(request.data.get('grille')))
            except (TypeError, ValueError):
                return False
            return self._autorise(u, dept, request.data.get('em'))
        # PATCH / PUT / DELETE : objet par objet.
        return True

    def has_object_permission(self, request, view, obj):
        u = request.user
        if u.is_superuser or request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return self._autorise(u, obj.grille.departement_id)


class SeanceTypeViewSet(AuditMixin, viewsets.ModelViewSet):
    """Cases du patron."""
    queryset = SeanceType.objects.select_related(
        'grille', 'grille__departement', 'jour_fk', 'creneau_fk',
        'em', 'prof', 'salle', 'type_seance_fk').all()
    serializer_class   = SeanceTypeSerializer
    permission_classes = [RBACPermission, SeanceTypePermission]
    required_module    = 'emplois'
    filter_backends    = [DjangoFilterBackend, OrderingFilter]
    filterset_fields   = ['grille', 'jour_fk', 'creneau_fk', 'prof', 'em']
    ordering           = ['jour_fk', 'creneau_fk__ordre']
    pagination_class   = None


    @action(detail=False, methods=['get'], url_path='occupation')
    def occupation(self, request):
        """
        Qui et quoi est déjà pris, sur les patrons de la période.

        Sans cela, la liste des enseignants proposait celui qu'un autre groupe
        avait déjà placé sur ce créneau : on le choisissait, et le refus
        n'arrivait qu'à l'enregistrement — après la saisie. Un champ ne doit
        pas offrir ce que le serveur refusera.

        Volontairement minimal — le créneau, l'enseignant, la salle, le
        groupe. Assez pour éviter la collision et dire où elle est, pas assez
        pour lire l'emploi du temps d'un collègue.
        """
        p = request.query_params
        manquants = [c for c in ('annee_universitaire', 'type_semestre')
                     if not p.get(c)]
        if manquants:
            return Response({'detail': f'Paramètres requis : {", ".join(manquants)}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        qs = (SeanceType.objects
              .filter(grille__annee_universitaire=p['annee_universitaire'],
                      grille__type_semestre=p['type_semestre'])
              .select_related('grille__departement', 'prof', 'salle', 'em',
                              'em__module_lmd', 'type_seance_fk'))

        # `bloquant` : cette séance attend-elle MES étudiants ? Un groupe
        # transversal — HE, ST — réunit toute la promotion, et un groupe entier
        # contient ses sous-groupes : dans les deux cas l'étudiant serait
        # attendu à deux endroits. L'écran doit le dire avant la saisie, pas le
        # serveur après.
        from apps.departement.models import Departement

        from .serializers import _memes_etudiants
        mien = None
        if p.get('departement'):
            mien = Departement.objects.filter(pk=p['departement']).first()

        return Response([{
            'departement': x.grille.departement_id,
            'groupe':      x.grille.departement.nom,
            'jour_fk':     x.jour_fk_id,
            'creneau_fk':  x.creneau_fk_id,
            'prof':        x.prof_id,
            'prof_nom':    x.prof.nom if x.prof_id else None,
            'salle':       x.salle_id,
            'salle_nom':   x.salle.nom if x.salle_id else None,
            'em_code':     x.em.code_em if x.em_id else None,
            'em_intitule': x.em.intitule if x.em_id else None,
            'type_libelle': (x.type_seance_fk.type_seance
                             if x.type_seance_fk_id else ''),
            # Le NOM du groupe : c'est celui qu'on lui a donné, et celui que
            # portent la liste et les onglets. Passer par le libellé interne
            # faisait diverger les écrans dès qu'on renommait l'un sans
            # l'autre.
            'groupe_court': (x.grille.departement.nom
                             or x.grille.departement.groupe),
            'bloquant':    bool(mien
                                and x.grille.departement_id != mien.pk
                                and _memes_etudiants(mien, x.grille.departement)),
        } for x in qs])


class SeanceReelleViewSet(DepartementScopedMixin, AuditMixin, viewsets.ModelViewSet):
    """Séances datées — ce que l'on édite semaine par semaine."""
    queryset = SeanceReelle.objects.select_related(
        'departement', 'semaine', 'semaine__jour_fk', 'creneau_fk',
        'em', 'prof', 'prof_initial', 'salle', 'type_seance_fk').all()
    serializer_class   = SeanceReelleSerializer
    permission_classes = [RBACPermission, EDTDepartementPermission]
    required_module    = 'emplois'
    filter_backends    = [DjangoFilterBackend, OrderingFilter]
    filterset_fields   = {
        'departement':                    ['exact', 'in'],
        'semaine':                        ['exact'],
        'semaine__numero_semaine':        ['exact'],
        'semaine__annee_universitaire':   ['exact'],
        'semaine__type_semestre':         ['exact'],
        'prof':                           ['exact'],
        'salle':                          ['exact'],
        'em':                             ['exact'],
        'annulee':                        ['exact'],
    }
    ordering         = ['semaine__date', 'creneau_fk__ordre']
    pagination_class = None

    # Les refus d'un JOUR FERMÉ vivent ici, dans la vue, et non dans
    # `SeanceReelleSerializer.validate` : levée dans le sérialiseur, l'erreur
    # arrive sous `errors.non_field_errors`, que `apiFetch` ne lit pas — le
    # message s'afficherait en JSON brut. Voir `apps/parametres/feries.py`.

    def perform_create(self, serializer):
        # AJOUTER : refusé sur TOUT jour hors cours.
        refuser_ajout(serializer.validated_data.get('semaine'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        """Une séance partagée reste identique sur tous ses groupes.

        La clé de fusion du socle inclut la salle et l'enseignant : laisser
        deux groupes d'un même cours diverger sur l'un de ces axes ferait payer
        deux fois un enseignant qui n'a donné qu'un cours.
        """
        # MODIFIER — rétablir compris : refusé sur un férié isolé. La séance
        # doit rester telle quelle pour être rétablie au retrait du férié.
        refuser_sur_ferie_isole(serializer.instance.semaine, 'modifier')
        cible = serializer.validated_data.get('semaine')
        if cible is not None and cible.pk != serializer.instance.semaine_id:
            # La déplacer vers un jour fermé, c'est l'y ajouter.
            refuser_ajout(cible)
        seance = serializer.save()
        if seance.cle_partage:
            propager(seance)

    def perform_destroy(self, instance):
        # SUPPRIMER : refusé sur un férié isolé — supprimée, la séance ne
        # pourrait plus être rétablie. PERMIS sur une semaine entière hors
        # cours : elle n'a pas de rétablissement, et il faut pouvoir la nettoyer.
        refuser_sur_ferie_isole(instance.semaine, 'supprimer')
        # Supprimer une séance partagée retire SON groupe du cours, pas le cours.
        retirer_du_partage(instance)

    @action(detail=False, methods=['post'], url_path='permuter')
    def permuter(self, request):
        """
        Échange deux séances d'un même créneau : enseignant, salle, élément.

        POST { "seance_a": 12, "seance_b": 15, "nb_semaines": 1, "motif": "" }

        Le geste d'IPGEI, sans son circuit d'approbation : l'ISS n'a qu'un
        planificateur. Les deux séances sont cherchées dans le PÉRIMÈTRE de
        l'appelant — une séance d'un groupe qui ne lui est pas délégué n'existe
        pas pour lui. Voir `services/permutation.py` pour ce qui s'échange et ce
        qui est retenu.
        """
        d = request.data
        try:
            ida, idb = int(d.get('seance_a')), int(d.get('seance_b'))
        except (TypeError, ValueError):
            raise ValidationError('Indiquez les deux séances à échanger.')

        qs = self.get_queryset()
        a = qs.filter(pk=ida).first()
        b = qs.filter(pk=idb).first()
        if a is None or b is None:
            raise ValidationError('Séance introuvable, ou hors de votre périmètre.')

        n = permuter_enseignants(a, b, d.get('nb_semaines', 1),
                                 (d.get('motif') or '').strip())
        return Response({'seances_impactees': n})

    @action(detail=True, methods=['post'], url_path='partager')
    def partager(self, request, pk=None):
        """
        Étend cette séance à d'autres groupes — le même cours, plusieurs groupes.

        POST { "departements": [12, 15] }

        Chaque groupe reçoit sa propre séance, identique : c'est la forme que le
        socle attend, puisqu'il les refusionne au pointage et ne compte les
        heures qu'une fois. Un groupe dont la case est déjà prise est signalé,
        jamais écrasé.
        """
        seance = self.get_object()
        # PARTAGER : refusé sur un férié isolé — les copies ne porteraient pas
        # le motif, et le retrait du férié ne les rétablirait pas.
        refuser_sur_ferie_isole(seance.semaine, 'partager')
        demandes = request.data.get('departements') or []
        if not isinstance(demandes, (list, tuple)) or not demandes:
            return Response({'detail': 'Indiquez au moins un groupe.'},
                            status=status.HTTP_400_BAD_REQUEST)

        perimetre = self.user_dept_ids()
        if perimetre is not None:
            hors = [int(d) for d in demandes if int(d) not in set(perimetre)]
            if hors:
                return Response(
                    {'detail': f'Groupes hors de votre périmètre : {hors}.'},
                    status=status.HTTP_403_FORBIDDEN)

        resultat = etendre_partage(seance, [int(d) for d in demandes])
        if resultat['cases_occupees']:
            resultat['detail'] = (
                "Certains groupes ont déjà une séance sur ce créneau : "
                "elle n'a pas été remplacée.")
        return Response(resultat, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='semaine')
    def par_semaine(self, request):
        """
        L'emploi du temps d'une semaine, pour un ou plusieurs groupes.

        Paramètres : `annee_universitaire`, `type_semestre`, `numero_semaine`,
        et `departements` (liste d'ids séparés par des virgules ; par défaut,
        tout le périmètre de l'utilisateur).
        """
        p = request.query_params
        manquants = [c for c in ('annee_universitaire', 'type_semestre', 'numero_semaine')
                     if not p.get(c)]
        if manquants:
            return Response({'detail': f'Paramètres requis : {", ".join(manquants)}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        qs = self.get_queryset().filter(
            semaine__annee_universitaire=p['annee_universitaire'],
            semaine__type_semestre=p['type_semestre'],
            semaine__numero_semaine=p['numero_semaine'],
        )
        bruts = (p.get('departements') or '').strip()
        if bruts:
            try:
                qs = qs.filter(departement_id__in=[int(x) for x in bruts.split(',') if x])
            except ValueError:
                return Response({'detail': 'departements : liste d\'identifiants attendue.'},
                                status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='occupation')
    def occupation(self, request):
        """
        Ce qui est déjà pris sur cette semaine, **chez les autres**.

        Le périmètre masque les séances des collègues : sans cette lecture, un
        responsable ne voit pas qu'une salle est occupée et ne peut donc ni
        l'éviter ni la demander. La contrainte est de portée établissement, elle
        ne peut venir que du serveur.

        Volontairement minimal — le groupe, l'enseignant, la salle, la case.
        Assez pour savoir que c'est pris et à qui le demander, pas assez pour
        lire l'emploi du temps d'un collègue.
        """
        p = request.query_params
        manquants = [c for c in ('annee_universitaire', 'type_semestre', 'numero_semaine')
                     if not p.get(c)]
        if manquants:
            return Response({'detail': f'Paramètres requis : {", ".join(manquants)}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # `SeanceReelle.objects` et non `self.get_queryset()` : on cherche
        # justement ce qui est HORS périmètre.
        qs = (SeanceReelle.objects
              .filter(annulee=False,
                      semaine__annee_universitaire=p['annee_universitaire'],
                      semaine__type_semestre=p['type_semestre'],
                      semaine__numero_semaine=p['numero_semaine'])
              .select_related('departement', 'semaine', 'semaine__jour_fk',
                              'creneau_fk', 'salle', 'prof', 'em'))
        miens = self.user_dept_ids()
        if miens is not None:
            qs = qs.exclude(departement_id__in=miens)

        return Response([{
            'seance':          s.pk,
            'departement':     s.departement_id,
            'departement_nom': s.departement.nom,
            'jour_libelle':    s.semaine.jour_fk.jour if s.semaine.jour_fk_id else None,
            'date':            s.semaine.date,
            'creneau_fk':      s.creneau_fk_id,
            'creneau_libelle': s.creneau_fk.creneau,
            'salle':           s.salle_id,
            'salle_nom':       s.salle.nom if s.salle_id else None,
            'prof':            s.prof_id,
            'prof_nom':        s.prof.nom if s.prof_id else None,
            'em_code':         s.em.code_em if s.em_id else None,
        } for s in qs])

    @action(detail=False, methods=['get'], url_path='consultation')
    def consultation(self, request):
        """
        Une semaine EN LECTURE, sur l'un des trois axes : groupe, enseignant,
        salle.

        Le périmètre ne s'applique pas ici, et c'est voulu : l'emploi du temps
        d'un enseignant traverse les départements, celui d'une salle aussi. Le
        borner au périmètre du lecteur en donnerait une version tronquée —
        pire qu'aucune, parce qu'elle a l'air complète. C'est déjà la règle du
        socle, dont les écrans « emploi par filière / salle / professeur » sont
        ouverts à quiconque a le droit de voir les emplois.

        Paramètres : `annee_universitaire`, `type_semestre`, `numero_semaine`,
        puis l'un de `departement`, `prof`, `salle`.
        """
        p = request.query_params
        manquants = [c for c in ('annee_universitaire', 'type_semestre', 'numero_semaine')
                     if not p.get(c)]
        if manquants:
            return Response({'detail': f'Paramètres requis : {", ".join(manquants)}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        qs = (SeanceReelle.objects
              .filter(semaine__annee_universitaire=p['annee_universitaire'],
                      semaine__type_semestre=p['type_semestre'],
                      semaine__numero_semaine=p['numero_semaine'])
              .select_related('departement', 'semaine', 'semaine__jour_fk',
                              'creneau_fk', 'em', 'prof', 'prof_initial',
                              'salle', 'type_seance_fk')
              .order_by('semaine__date', 'creneau_fk__ordre'))

        for champ in ('departement', 'prof', 'salle'):
            valeur = (p.get(champ) or '').strip()
            if not valeur:
                continue
            try:
                qs = qs.filter(**{f'{champ}_id': int(valeur)})
            except ValueError:
                return Response({'detail': f'{champ} : identifiant attendu.'},
                                status=status.HTTP_400_BAD_REQUEST)

        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='pdf')
    def pdf(self, request):
        """
        L'emploi du temps d'une semaine, en PDF — même gabarit que le socle.

        On reprend `emploi_filiere_pdf.html` et wkhtmltopdf, comme le reste du
        projet : un second gabarit divergerait au premier changement d'en-tête,
        et deux emplois du temps imprimés côte à côte ne se ressembleraient
        plus.

        Le gabarit prévoyait déjà la mention de semaine ; elle ne servait pas,
        faute d'un emploi du temps daté à lui donner.
        """
        from collections import defaultdict

        import pdfkit
        from django.http import HttpResponse
        from django.template.loader import render_to_string

        from apps.departement.models import Departement
        from apps.parametres.models import Creneau, Jour, Semaine, Semestre
        from apps.prof.models import Prof
        from apps.salle.models import Salle
        from core.pdf_utils import get_institution_context

        p = request.query_params
        manquants = [c for c in ('annee_universitaire', 'type_semestre', 'numero_semaine')
                     if not p.get(c)]
        if manquants:
            return Response({'detail': f'Paramètres requis : {", ".join(manquants)}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        qs = (SeanceReelle.objects
              .filter(semaine__annee_universitaire=p['annee_universitaire'],
                      semaine__type_semestre=p['type_semestre'],
                      semaine__numero_semaine=p['numero_semaine'],
                      annulee=False)
              .select_related('departement', 'semaine', 'semaine__jour_fk',
                              'creneau_fk', 'em', 'em__module_lmd', 'prof',
                              'salle', 'type_seance_fk'))

        # L'axe de lecture donne aussi le sous-titre du document : une grille
        # imprimée circule détachée de l'écran, elle doit dire de qui elle est.
        titre, axe_label, groupe = '', 'Filière', None
        if p.get('departement'):
            qs = qs.filter(departement_id=p['departement'])
            groupe = (Departement.objects.select_related('filiere')
                      .filter(pk=p['departement']).first())
            # « Filière : G1 » ne disait pas de quelle filière. On imprime
            # l'INTITULÉ de la filière, puis le groupe — « Licence
            # Professionnelle Statistique — G1 ». Un groupe sans filière (HE,
            # ST) ne porte que son nom : il n'y a rien à mettre devant.
            #
            # Et quand la filière n'a QU'UN groupe à ce niveau, le nom du
            # groupe n'apprend rien : « Science des Données — SDID » répète,
            # « Statistique… — SEA » n'aide personne. Seul l'intitulé reste.
            # Pas l'année d'étude non plus : la ligne du dessous porte déjà le
            # semestre — « Semestre S3 » dit L2, « S5 » dit L3 — et la répéter
            # ici serait la dire deux fois.
            if groupe is None:
                titre = ''
            elif groupe.filiere_id and groupe.filiere:
                intitule = groupe.filiere.intitule_fr or groupe.filiere.code or ''
                freres = Departement.objects.filter(
                    annee_universitaire=groupe.annee_universitaire,
                    filiere_id=groupe.filiere_id, niveau_id=groupe.niveau_id,
                    is_container=False).count()
                if not intitule:
                    titre = groupe.nom
                elif freres <= 1:
                    titre = intitule
                else:
                    titre = f'{intitule} — {groupe.nom}'
            else:
                titre = groupe.nom
        elif p.get('prof'):
            qs = qs.filter(prof_id=p['prof'])
            x = Prof.objects.filter(pk=p['prof']).first()
            titre, axe_label = (x.nom if x else ''), 'Enseignant'
        elif p.get('salle'):
            qs = qs.filter(salle_id=p['salle'])
            x = Salle.objects.filter(pk=p['salle']).first()
            titre, axe_label = (x.nom if x else ''), 'Salle'
        else:
            return Response(
                {'detail': 'Indiquez un groupe, un enseignant ou une salle.'},
                status=status.HTTP_400_BAD_REQUEST)

        seances = list(qs)
        if not seances:
            return Response({'detail': "Aucune séance sur cette semaine."},
                            status=status.HTTP_404_NOT_FOUND)

        # Un groupe TRANSVERSAL — HE, ST — n'a pas de filière : « Filière : HE »
        # nommerait une filière qui n'existe pas. On dit ce que la grille est
        # réellement : un enseignement suivi par toute la promotion.
        if groupe is not None and est_transversal(groupe):
            axe_label = 'Enseignement transversal'

        jours    = list(Jour.objects.all().order_by('id'))
        creneaux = list(Creneau.objects.filter(is_actif=True).order_by('ordre', 'creneau'))

        # Un cours partagé produit une séance par groupe. Les imprimer l'une
        # sous l'autre répétait la matière et son intitulé autant de fois, et
        # la case débordait de la page. On les réunit : une entrée, et les
        # groupes énumérés sur la ligne du bas — comme à l'écran.
        grille = defaultdict(lambda: defaultdict(dict))
        for s in seances:
            jour = s.semaine.jour_fk.jour if s.semaine.jour_fk_id else ''
            # Ce que la ligne nomme dépend de l'axe : sur l'emploi d'un
            # enseignant, répéter son nom n'apprend rien, c'est le groupe qui
            # manque ; sur celui d'une SALLE, il faut les deux — qui y
            # enseigne, et à qui.
            ligne = (s.departement.nom if (p.get('prof') or p.get('salle'))
                     else (s.prof.nom if s.prof_id else ''))
            enseignant = s.prof.nom if (p.get('salle') and s.prof_id) else ''
            cle = (s.em_id, s.type_seance_fk_id, s.salle_id)
            case = grille[jour][s.creneau_fk_id]
            if cle in case:
                if ligne and ligne not in case[cle]['_lignes']:
                    case[cle]['_lignes'].append(ligne)
                    _ligne_case(case[cle])
                continue
            case[cle] = {
                'type_seance': s.type_seance_fk.type_seance if s.type_seance_fk_id else '',
                'type_seance_is_special': bool(
                    s.type_seance_fk_id and s.type_seance_fk.is_special),
                'salle_nom':   s.salle.nom if s.salle_id else '',
                'em_code':     s.em.code_em if s.em_id else '',
                'em_intitule': s.em.intitule if s.em_id else '',
                '_lignes':     [ligne] if ligne else [],
                '_enseignant': enseignant,
            }
            _ligne_case(case[cle])

        # Le semestre CONCRET — « Semestre S1 » — plutôt que la parité de la
        # session. « Semestres impairs » ne dit pas de quel semestre il s'agit,
        # et c'est précisément ce qu'un emploi du temps imprimé doit porter.
        # Il se déduit de l'année d'étude des groupes concernés et de la
        # parité : il n'y a rien à demander. Un PDF par enseignant peut en
        # traverser plusieurs — on les nomme alors tous.
        niveaux = {s.departement.niveau_id for s in seances if s.departement.niveau_id}
        codes = sorted(Semestre.objects
                       .filter(niveau_semestre_id__in=niveaux,
                               type_semestre=p['type_semestre'])
                       .values_list('code_semestre', flat=True))
        if len(codes) == 1:
            semestre_nom = f'Semestre {codes[0]}'
        elif codes:
            semestre_nom = f'Semestres {", ".join(codes)}'
        else:
            semestre_nom = ('Semestres impairs' if p['type_semestre'] == 'I'
                            else 'Semestres pairs')

        bornes = (Semaine.objects
                  .filter(annee_universitaire=p['annee_universitaire'],
                          type_semestre=p['type_semestre'],
                          numero_semaine=p['numero_semaine'])
                  .aggregate(debut=Min('date'), fin=Max('date')))

        # L'en-tête institutionnel — logo, noms français et arabe — vient du
        # même utilitaire que tous les autres documents : un PDF qui ne le
        # porterait pas ne serait pas un document de l'établissement.
        html = render_to_string('emploi_filiere_pdf.html', {
            **get_institution_context(),
            'annee_universitaire': p['annee_universitaire'],
            'axe_label':           axe_label,
            'departement':         titre,
            'semestre_nom':        semestre_nom,
            'semaine':             p['numero_semaine'],
            'debut':               bornes['debut'],
            'fin':                 bornes['fin'],
            'creneaux':            creneaux,
            'rows': [{'jour': j.jour,
                      'cells': [list(grille[j.jour][c.id].values()) for c in creneaux]}
                     for j in jours],
        })

        try:
            octets = pdfkit.from_string(
                html, False,
                configuration=pdfkit.configuration(
                    wkhtmltopdf=_wkhtmltopdf()),
                options={'margin-top': '0.50in', 'margin-right': '0.50in',
                         'margin-bottom': '0.50in', 'margin-left': '0.50in',
                         'orientation': 'Landscape',
                         'enable-local-file-access': ''})
        except Exception as exc:                       # noqa: BLE001
            logger.error('PDF EDT : %s', exc)
            return Response({'detail': 'Erreur lors de la génération du PDF.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        nom = f'emploi_{titre}_S{p["numero_semaine"]}.pdf'.replace(' ', '_')
        reponse = HttpResponse(octets, content_type='application/pdf')
        reponse['Content-Disposition'] = f'attachment; filename="{nom}"'
        return reponse

    @action(detail=False, methods=['get'], url_path='coherence')
    def coherence(self, request):
        """
        L'emploi du temps et le suivi disent-ils la même chose ?

        Sans ce témoin, une séance corrigée après la génération faisait diverger
        l'emploi du temps et le pointage en silence : l'écran montrait une
        version, la paie en suivait une autre, et rien ne disait laquelle
        faisait foi.

        Le périmètre est celui de l'appelant : l'état que voit un chef de
        département est celui de SES groupes. Lui montrer la divergence d'un
        collègue serait un reproche qu'il ne peut ni comprendre ni corriger.
        """
        p = request.query_params
        annee = p.get('annee_universitaire')
        ts    = p.get('type_semestre')
        if not (annee and ts):
            return Response(
                {'detail': 'annee_universitaire et type_semestre requis.'},
                status=status.HTTP_400_BAD_REQUEST)

        demandee = p.get('numero_semaine')
        if demandee:
            numeros = [int(demandee)]
        else:
            # Toutes les semaines qui portent quelque chose, d'un côté ou de
            # l'autre : une semaine dont le suivi existe mais dont l'emploi du
            # temps a été entièrement vidé est précisément un cas à signaler.
            from apps.suivi.models import Suivie
            numeros = sorted(
                set(SeanceReelle.objects
                    .filter(semaine__annee_universitaire=annee,
                            semaine__type_semestre=ts)
                    .values_list('semaine__numero_semaine', flat=True))
                | set(Suivie.objects
                      .filter(annee_universitaire=annee, type_semestre=ts)
                      .values_list('numero_semaine', flat=True))
                - {None})

        # Le périmètre de l'appelant : ses groupes délégués. `None` pour un
        # superutilisateur, qui n'a pas de borne.
        perimetre = self.user_dept_ids()
        etats = etats_en_lot(annee, ts, numeros, departements=perimetre)
        return Response({
            'etats': {str(n): {'etat': e, 'libelle': LIBELLES[e]}
                      for n, e in etats.items()},
            'divergentes': sorted(n for n, e in etats.items()
                                  if e == ETAT_DIVERGENT),
        })

    @action(detail=False, methods=['post'], url_path='projeter')
    def projeter(self, request):
        """
        Alimente `emplois.Emplois` avec les séances réelles d'une semaine.

        À lancer JUSTE AVANT « Générer le suivi » : le socle lit `Emplois` et ne
        sait rien de la planification hebdomadaire. Sans cette étape, le Suivi
        travaillerait sur une grille périmée — remplacements et annulations
        compris, donc avec les mauvaises heures payées.

        Le périmètre est celui de l'utilisateur : projeter sa semaine n'efface
        jamais la grille d'un collègue.
        """
        d = request.data
        manquants = [c for c in ('annee_universitaire', 'type_semestre', 'numero_semaine')
                     if not d.get(c)]
        if manquants:
            return Response({'detail': f'Paramètres requis : {", ".join(manquants)}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # `user_dept_ids()` rend None pour un superutilisateur : pas de borne.
        perimetre = self.user_dept_ids()
        demandes  = d.get('departements')
        if demandes:
            demandes = [int(x) for x in demandes]
            if perimetre is not None:
                hors = [x for x in demandes if x not in set(perimetre)]
                if hors:
                    return Response(
                        {'detail': f'Groupes hors de votre périmètre : {hors}.'},
                        status=status.HTTP_403_FORBIDDEN)
            cibles = demandes
        else:
            cibles = perimetre          # None = tous, pour un superutilisateur

        # Un périmètre VIDE veut dire « rien », jamais « tout ». Sans ce refus,
        # `projeter_semaine` ne filtrait pas — il écrivait dans `Emplois` les
        # séances de TOUS les groupes, et n'en supprimait aucune, donc des
        # doublons à chaque appel. Un utilisateur sans groupe délégué ne doit
        # pas pouvoir transmettre la semaine d'autrui.
        if cibles is not None and not cibles:
            return Response(
                {'detail': "Aucun groupe ne vous est attribué : il n'y a rien à "
                           "transmettre. Demandez qu'un groupe vous soit délégué "
                           "dans Paramètres → Permissions EDT."},
                status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            resultat = projeter_semaine(
                d['annee_universitaire'], d['type_semestre'],
                int(d['numero_semaine']), departements=cibles)
            # La version transmise est celle sur laquelle le suivi sera généré,
            # donc celle qui sera pointée et payée. On la fige ici : après, la
            # séance peut encore bouger et plus rien ne dirait ce qui avait
            # servi.
            resultat['archivees'] = archiver_semaine(
                d['annee_universitaire'], d['type_semestre'],
                int(d['numero_semaine']), departements=cibles)
        return Response(resultat, status=status.HTTP_200_OK)


class EmploiArchiveViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Emplois du temps figés au moment de leur transmission au suivi — lecture
    seule.

    On ne modifie pas une archive : la seule écriture possible est la prise de
    vue, faite par `services.archive` quand la semaine est projetée.
    """
    queryset           = EmploiArchive.objects.all()
    serializer_class   = EmploiArchiveSerializer
    permission_classes = [RBACPermission]
    required_module    = 'emplois'
    pagination_class   = None

    def _semaine_demandee(self, request):
        p = request.query_params
        manquants = [c for c in ('annee_universitaire', 'type_semestre')
                     if not p.get(c)]
        if manquants:
            raise ValidationError(
                {'detail': f'Paramètres requis : {", ".join(manquants)}.'})
        return p['annee_universitaire'], p['type_semestre']

    @action(detail=False, methods=['get'], url_path='versions')
    def versions(self, request):
        """
        Prises de vue disponibles pour un groupe, dans l'ordre du calendrier.

        Une semaine re-transmise en compte plusieurs : c'est précisément ce
        qu'on veut pouvoir comparer.
        """
        annee, type_semestre = self._semaine_demandee(request)
        groupe = request.query_params.get('departement')
        if not groupe:
            raise ValidationError({'departement': 'Groupe requis.'})

        lignes = (EmploiArchive.objects
                  .filter(annee_universitaire=annee, type_semestre=type_semestre,
                          departement_id=groupe)
                  .values('numero_semaine', 'version', 'genere_le')
                  .annotate(nb=Count('id'))
                  .order_by('numero_semaine', '-version'))

        # Les bornes de chaque semaine viennent du calendrier : l'archive fige
        # la date de SA séance, pas celles du lundi et du samedi.
        from apps.parametres.models import Semaine
        bornes = {}
        for b in (Semaine.objects
                  .filter(annee_universitaire=annee, type_semestre=type_semestre)
                  .values('numero_semaine')
                  .annotate(debut=Min('date'), fin=Max('date'))):
            bornes[b['numero_semaine']] = (b['debut'], b['fin'])

        return Response(VersionArchiveSerializer([{
            'numero_semaine': l['numero_semaine'],
            'version':        l['version'],
            'genere_le':      l['genere_le'],
            'nb_seances':     l['nb'],
            'date_debut':     bornes.get(l['numero_semaine'], (None, None))[0],
            'date_fin':       bornes.get(l['numero_semaine'], (None, None))[1],
        } for l in lignes], many=True).data)

    @action(detail=False, methods=['get'], url_path='grille')
    def grille(self, request):
        """Séances d'une prise de vue, au format de l'emploi du temps vivant."""
        annee, type_semestre = self._semaine_demandee(request)
        p = request.query_params
        for c in ('numero_semaine', 'departement', 'version'):
            if not p.get(c):
                raise ValidationError({c: 'Paramètre requis.'})

        qs = (EmploiArchive.objects
              .filter(annee_universitaire=annee, type_semestre=type_semestre,
                      numero_semaine=p['numero_semaine'],
                      departement_id=p['departement'],
                      version=p['version'])
              .order_by('jour_ref', 'creneau_ordre'))
        return Response(self.get_serializer(qs, many=True).data)
