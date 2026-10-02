"""
La saisie de notes EN LIGNE — un brouillon, jamais une note officielle.

  POST /api/v1/saisie-en-ligne/          l'enseignant enregistre son brouillon,
                                         au format de la saisie officielle :
                                         {session, rows: [{inscription_element, cc, tp, exam}]}
  GET  /api/v1/saisie-en-ligne/          l'enseignant : SES brouillons (?session=&em=) ;
                                         admin et IT : tous
  GET  /api/v1/saisie-en-ligne/export/   admin et IT : le tableur à ressaisir
                                         sur le serveur de travail (?session=&em=)

Mêmes contrôles que la saisie officielle (session ouverte, élément enseigné),
et AUCUNE écriture dans `evaluations_note` — ni ici, ni ailleurs à partir
d'ici. Voir le modèle.
"""
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.db import transaction
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SaisieNoteEnLigne

CHAMPS = ('cc', 'tp', 'exam')


def _valeur(brute):
    """'' ou None : pas de note. Hors [0, 20] ou illisible : refusé (None, False)."""
    if brute is None or brute == '':
        return None, True
    try:
        v = Decimal(str(brute).replace(',', '.'))
    except InvalidOperation:
        return None, False
    if not (Decimal('0') <= v <= Decimal('20')):
        return None, False
    return v, True


def _ligne(s: SaisieNoteEnLigne) -> dict:
    return {
        'id': s.id, 'session_id': s.session_id, 'session_libelle': s.session_libelle,
        'inscription_element': s.inscription_element_id, 'em_id': s.em_id,
        'em_code': s.em_code, 'em_intitule': s.em_intitule,
        'etudiant_id': s.etudiant_id, 'etudiant_matricule': s.etudiant_matricule,
        'etudiant_nom': s.etudiant_nom,
        'cc': s.cc, 'tp': s.tp, 'exam': s.exam,
        'saisi_par_nom': s.saisi_par_nom or None, 'modifie_le': s.modifie_le,
    }


class SaisieEnLigneView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role = getattr(request.user, 'role', None)
        qs = SaisieNoteEnLigne.objects.all()
        if role == 'enseignant':
            qs = qs.filter(saisi_par_id=request.user.pk)
        elif role not in ('admin', 'IT'):
            return Response({'detail': 'Accès refusé.'}, status=403)
        for champ, param in (('session_id', 'session'), ('em_id', 'em')):
            v = request.query_params.get(param)
            if v:
                qs = qs.filter(**{champ: v})
        return Response([_ligne(s) for s in qs])

    def post(self, request):
        from apps.evaluations.models import SessionEvaluation
        from apps.evaluations.services.note_access import peut_acceder_em
        from apps.inscriptions.models import InscriptionElement

        session_id = request.data.get('session')
        rows = request.data.get('rows') or []
        if not session_id:
            return Response({'detail': 'session requis.'}, status=status.HTTP_400_BAD_REQUEST)
        session = SessionEvaluation.objects.filter(pk=session_id).first()
        if session is None:
            return Response({'detail': 'Session introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        if session.est_close:
            return Response({'detail': 'Session clôturée — saisie impossible.'},
                            status=status.HTTP_400_BAD_REQUEST)

        ie_ids = [r.get('inscription_element') for r in rows if r.get('inscription_element')]
        ies = {ie.pk: ie for ie in InscriptionElement.objects
               .select_related('em', 'inscription_ped__inscription_admin__etudiant')
               .filter(pk__in=ie_ids)}
        inconnus = sorted(set(int(i) for i in ie_ids) - set(ies))
        if inconnus:
            return Response({'detail': f'Inscription introuvable : {inconnus[:5]}'},
                            status=status.HTTP_400_BAD_REQUEST)
        for em_id in {ie.em_id for ie in ies.values()}:
            if not peut_acceder_em(request.user, em_id):
                return Response({'detail': "Accès non autorisé : saisie sur un EM que vous n'enseignez pas."},
                                status=status.HTTP_403_FORBIDDEN)

        enregistres = supprimes = 0
        refuses = []
        nom = (getattr(request.user, 'name', '') or request.user.username or '')[:150]
        with transaction.atomic():
            for row in rows:
                ie = ies.get(int(row.get('inscription_element') or 0))
                if ie is None:
                    continue
                valeurs = {}
                for champ in CHAMPS:
                    v, ok = _valeur(row.get(champ))
                    if not ok:
                        refuses.append({'inscription_element': ie.pk, 'champ': champ})
                        continue
                    valeurs[champ] = v
                if all(valeurs.get(c) is None for c in CHAMPS):
                    n, _ = SaisieNoteEnLigne.objects.filter(
                        session_id=session.pk, inscription_element_id=ie.pk).delete()
                    supprimes += n
                    continue
                etu = ie.inscription_ped.inscription_admin.etudiant
                SaisieNoteEnLigne.objects.update_or_create(
                    session_id=session.pk, inscription_element_id=ie.pk,
                    defaults={
                        **valeurs,
                        'em_id': ie.em_id,
                        'em_code': (ie.em.code_em if ie.em_id else '')[:50],
                        'em_intitule': (ie.em.intitule if ie.em_id else '')[:200],
                        'session_libelle': str(session)[:200],
                        'etudiant_id': etu.pk,
                        'etudiant_matricule': (etu.matricule or '')[:50],
                        'etudiant_nom': (etu.nom or '')[:200],
                        'saisi_par_id': request.user.pk,
                        'saisi_par_nom': nom,
                    },
                )
                enregistres += 1
        return Response({
            'enregistres': enregistres, 'supprimes': supprimes, 'refuses': refuses,
            'detail': ("Brouillon enregistré. Ces notes ne sont PAS officielles : la "
                       "scolarité les ressaisit sur le serveur de travail."),
        })


class ExportSaisieEnLigneView(APIView):
    """Le tableur à ressaisir — admin et IT."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', None) not in ('admin', 'IT'):
            return Response({'detail': 'Accès refusé.'}, status=403)
        from openpyxl import Workbook

        qs = SaisieNoteEnLigne.objects.all()
        for champ, param in (('session_id', 'session'), ('em_id', 'em')):
            v = request.query_params.get(param)
            if v:
                qs = qs.filter(**{champ: v})

        wb = Workbook()
        ws = wb.active
        ws.title = 'Brouillons'
        ws.append(['Session', 'EM', 'Intitulé', 'Matricule', 'Nom', 'CC', 'TP', 'Examen',
                   'Saisi par', 'Modifié le', 'inscription_element'])
        for s in qs:
            ws.append([s.session_libelle, s.em_code, s.em_intitule, s.etudiant_matricule,
                       s.etudiant_nom,
                       float(s.cc) if s.cc is not None else None,
                       float(s.tp) if s.tp is not None else None,
                       float(s.exam) if s.exam is not None else None,
                       s.saisi_par_nom, s.modifie_le.strftime('%d/%m/%Y %H:%M'),
                       s.inscription_element_id])
        tampon = BytesIO()
        wb.save(tampon)
        from core.telechargement import entete_piece_jointe
        r = HttpResponse(tampon.getvalue(),
                         content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = entete_piece_jointe('notes_en_ligne_brouillon.xlsx')
        return r
