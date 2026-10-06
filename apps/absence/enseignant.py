"""
Liste des étudiants d'une séance, pour l'ENSEIGNANT (app « ISS Enseignant »).

Lecture seule : l'appel reste sur la fiche papier, saisie par la scolarité
(décision du 06/10/2026). L'enseignant consulte, pour une séance de SA grille
(`suivi/pointages/grille/`, identifiant = pointage), qui doit être là : la
liste d'appel de chaque groupe de la séance, la même que la fiche de présence
(`liste_appel`), dans l'ordre de la fiche (matricule croissant).
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEnseignant

from .liste_appel import liste_appel, lignes_de_fiche


def _ligne(e, filiere=''):
    return {'id': e.pk, 'matricule': e.matricule or '', 'nom': e.nom or '',
            **({'filiere': filiere} if filiere else {})}


class ListeSeanceEnseignantView(APIView):
    """GET /api/v1/absences/enseignant/liste/?pointage=<id>

    Réponse :
      {seance: {em_code, em_intitule, type_seance, date, numero_semaine},
       groupes: [{id, nom, source, etudiants: [{matricule, nom, statut}]}],
       total}
    `statut` : '' (du groupe), 'rattache' (inscrit dans une autre filière),
    'dette' (d'un autre groupe, inscrit à l'élément).
    """
    permission_classes = [IsEnseignant]

    def get(self, request):
        from apps.suivi.models import SuiviePointage

        prof = getattr(request.user, 'prof_profile', None)
        if prof is None:
            return Response({'error': 'Profil enseignant introuvable.'}, status=403)
        pid = request.query_params.get('pointage', '')
        if not pid.isdigit():
            return Response({'error': 'Paramètre pointage requis.'}, status=400)
        sp = (SuiviePointage.objects.filter(pk=int(pid), prof_id=prof.pk)
              .select_related('em', 'type_seance_fk').prefetch_related('departements').first())
        if sp is None:
            # Pas la sienne, ou inexistante : on ne dit pas laquelle.
            return Response({'error': 'Séance introuvable.'}, status=404)

        groupes, total = [], 0
        for dep in sorted(sp.departements.all(), key=lambda d: d.nom or ''):
            l = liste_appel(dep.pk, sp.em_id, sp.annee_universitaire)
            lignes = lignes_de_fiche(
                [_ligne(e) for e in l['etudiants']],
                [_ligne(e, getattr(e, 'filiere_inscription', '')) for e in l['rattaches']],
                [_ligne(e) for e in l['dettes']],
            )
            total += len(lignes)
            groupes.append({'id': dep.pk, 'nom': dep.nom or '', 'source': l['source'], 'etudiants': lignes})

        return Response({
            'seance': {
                'em_code':        sp.em.code_em if sp.em_id else '',
                'em_intitule':    sp.em.intitule if sp.em_id else '',
                'type_seance':    sp.type_seance_fk.type_seance if sp.type_seance_fk_id else '',
                'date':           sp.date_suivie.isoformat() if sp.date_suivie else None,
                'numero_semaine': sp.numero_semaine,
            },
            'groupes': groupes,
            'total':   total,
        })
