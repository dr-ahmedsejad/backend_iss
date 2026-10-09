"""
Écran « Groupes d'anglais » (Étudiants) — règles dans apps/edt/anglais.py.

GET    /api/v1/edt/anglais/?annee=            niveaux, leurs groupes, effectifs
POST   /api/v1/edt/anglais/groupes/            {annee, niveau, nom?} : crée le suivant
PATCH  /api/v1/edt/anglais/groupes/<id>/       {nom} : renomme
DELETE /api/v1/edt/anglais/groupes/<id>/       seulement s'il n'a encore rien
GET    /api/v1/edt/anglais/etudiants/?annee=&niveau=
POST   /api/v1/edt/anglais/affecter/           {annee, affectations: [{etudiant, groupe|null}], apercu?}
POST   /api/v1/edt/anglais/importer/           multipart : fichier, annee, apercu

Même droit que les fiches étudiants : voir pour lire, modifier pour écrire.
"""
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.audit_helpers import write_audit
from core.models import ACTION_BULK_UPDATE, ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE

from . import anglais
from .models import AffectationAnglais, GroupeAnglais

MODULE = 'scolarite_etudiants'
TAILLE_MAX_IMPORT = 5 * 1024 * 1024


def _droit(request, action):
    from apps.absence.views import _check_abs_module
    _check_abs_module(request.user, MODULE, action=action)


def _vrai(valeur):
    return str(valeur or '').strip().lower() in ('1', 'true', 'oui', 'on')


def _refus(message):
    return Response({'detail': str(message)}, status=status.HTTP_400_BAD_REQUEST)


def _groupe(g):
    return {'id': g.pk, 'departement': g.departement_id, 'nom': g.departement.nom,
            'rang': g.rang, 'niveau': g.niveau_id,
            'effectif': g.effectif if hasattr(g, 'effectif') else g.affectations.count()}


class AnglaisView(APIView):
    """Les niveaux de l'année, leurs groupes d'anglais et où en est l'affectation."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _droit(request, 'voir')
        annee = (request.query_params.get('annee') or '').strip()
        if not annee:
            return _refus('Année obligatoire.')
        les_groupes = list(anglais.groupes(annee))
        reponse = []
        for n in anglais.niveaux(annee):
            etudiants = anglais.etudiants_du_niveau(annee, n.pk)
            reponse.append({
                'id': n.pk, 'niveau': n.niveau,
                'groupes': [_groupe(g) for g in les_groupes if g.niveau_id == n.pk],
                'etudiants': etudiants.count(),
                'affectes': AffectationAnglais.objects.filter(
                    annee_universitaire=annee, etudiant__in=etudiants).count(),
            })
        return Response({
            'annee': annee, 'max_groupes': anglais.MAX_GROUPES, 'niveaux': reponse,
            'groupes_sans_niveau': [{'id': d.pk, 'nom': d.nom, 'etudiants': d.nb}
                                    for d in anglais.groupes_sans_niveau(annee)],
        })


class GroupesAnglaisView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _droit(request, 'modifier')
        d = request.data or {}
        try:
            g = anglais.creer_groupe(d.get('annee'), d.get('niveau'), d.get('nom') or '')
        except anglais.RegleAnglais as e:
            return _refus(e)
        write_audit(action=ACTION_CREATE, model_name='GroupeAnglais', object_id=str(g.pk),
                    changes={'nom': g.departement.nom, 'annee': g.annee_universitaire,
                             'niveau': g.niveau.niveau, 'rang': g.rang},
                    label=f'Groupe d\'anglais créé : {g.departement.nom}', user=request.user)
        return Response(_groupe(g), status=status.HTTP_201_CREATED)


class GroupeAnglaisView(APIView):
    permission_classes = [IsAuthenticated]

    def _trouver(self, pk):
        return GroupeAnglais.objects.select_related('departement', 'niveau').filter(pk=pk).first()

    def patch(self, request, pk):
        _droit(request, 'modifier')
        g = self._trouver(pk)
        if g is None:
            return Response({'detail': 'Groupe d\'anglais introuvable.'}, status=404)
        avant = g.departement.nom
        try:
            anglais.renommer(g, (request.data or {}).get('nom'))
        except anglais.RegleAnglais as e:
            return _refus(e)
        write_audit(action=ACTION_UPDATE, model_name='GroupeAnglais', object_id=str(g.pk),
                    changes={'nom': [avant, g.departement.nom]},
                    label=f'Groupe d\'anglais renommé : {g.departement.nom}', user=request.user)
        return Response(_groupe(g))

    def delete(self, request, pk):
        _droit(request, 'modifier')
        g = self._trouver(pk)
        if g is None:
            return Response({'detail': 'Groupe d\'anglais introuvable.'}, status=404)
        nom, nb = g.departement.nom, g.affectations.count()
        try:
            anglais.supprimer(g)
        except anglais.RegleAnglais as e:
            return _refus(e)
        write_audit(action=ACTION_DELETE, model_name='GroupeAnglais', object_id=str(pk),
                    changes={'nom': nom, 'affectations_retirees': nb},
                    label=f'Groupe d\'anglais supprimé : {nom}', user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EtudiantsAnglaisView(APIView):
    """Les étudiants d'un niveau, avec leur groupe d'anglais de l'année."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _droit(request, 'voir')
        annee = (request.query_params.get('annee') or '').strip()
        niveau = request.query_params.get('niveau')
        if not annee or not niveau:
            return _refus('Année et niveau obligatoires.')
        affectations = dict(AffectationAnglais.objects.filter(annee_universitaire=annee)
                            .values_list('etudiant_id', 'groupe_id'))
        etudiants = (anglais.etudiants_du_niveau(annee, niveau)
                     .select_related('departement', 'departement__filiere', 'filiere')
                     .order_by('matricule'))
        # La filière du GROUPE d'abord : `Etudiant.filiere` reste celle de la
        # première année (STAT, LPSTAT) et les groupes de L3 s'appellent tous
        # « G1 » ou « G2 » — c'est la filière du groupe qui les distingue.
        return Response({'etudiants': [{
            'id': e.pk, 'matricule': e.matricule, 'nom': e.nom, 'statut': e.statut,
            'groupe_habituel': e.departement.nom,
            'filiere': (e.departement.filiere.code if e.departement.filiere_id
                        else e.filiere.code if e.filiere_id else ''),
            'groupe_anglais': affectations.get(e.pk),
        } for e in etudiants]})


class AffecterAnglaisView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _droit(request, 'modifier')
        d = request.data or {}
        annee = (d.get('annee') or '').strip()
        brutes = d.get('affectations')
        if not annee or not isinstance(brutes, list):
            return _refus('Année et liste d\'affectations obligatoires.')
        try:
            demandes = [(int(a['etudiant']), int(a['groupe']) if a.get('groupe') else None)
                        for a in brutes]
        except (KeyError, TypeError, ValueError):
            return _refus('Chaque affectation porte un étudiant et un groupe (ou rien pour retirer).')
        apercu = _vrai(d.get('apercu'))
        resultat = anglais.affecter(annee, demandes, apercu=apercu)
        _journaliser(request, annee, resultat, apercu, 'écran')
        return Response({'apercu': apercu, **resultat})


class ImporterAnglaisView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        _droit(request, 'modifier')
        annee = (request.data.get('annee') or '').strip()
        fichier = request.FILES.get('fichier')
        if not annee or fichier is None:
            return _refus('Année et fichier obligatoires.')
        if fichier.size > TAILLE_MAX_IMPORT:
            return _refus('Fichier trop lourd : 5 Mo au plus.')
        apercu = _vrai(request.data.get('apercu'))
        try:
            resultat = anglais.importer(annee, anglais.lire_classeur(fichier), apercu=apercu)
        except anglais.RegleAnglais as e:
            return _refus(e)
        _journaliser(request, annee, resultat, apercu, f'Excel {fichier.name}')
        return Response({'apercu': apercu, **resultat})


def _journaliser(request, annee, resultat, apercu, source):
    ecrites = [l for l in resultat['lignes'] if l['statut'] in anglais.ECRITS]
    if apercu or not ecrites:
        return
    write_audit(
        action=ACTION_BULK_UPDATE, model_name='AffectationAnglais', object_id='0',
        changes={'annee': annee, 'source': source,
                 'lignes': [{'etudiant': l['etudiant'].get('matricule'),
                             'groupe': (l['groupe'] or {}).get('nom'),
                             'statut': l['statut']} for l in ecrites]},
        label=f'Groupes d\'anglais {annee} : {len(ecrites)} affectation(s) ({source})',
        user=request.user)
