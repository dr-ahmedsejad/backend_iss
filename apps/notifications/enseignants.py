"""Notifications destinées aux ENSEIGNANTS (cloche, et téléphone via
`envoyer_push` avec la clé de l'app « ISS Enseignant »).

  * un étudiant dépose une réclamation sur un élément de l'enseignant ;
  * sa contestation d'une séance « Non fait » est acceptée ou rejetée ;
  * une session de saisie des notes s'ouvre pour ses éléments.

Une notification ne doit JAMAIS faire échouer l'action qui la déclenche :
chaque fonction attrape tout et journalise.

Liens : chemins du portail enseignant ; l'app ouvre l'onglet correspondant
(le dernier segment : « reclamations », « emploi », « notes »).
"""
import functools
import logging

logger = logging.getLogger('siga')

LIEN_RECLAMATIONS = '/dashboard/enseignant/reclamations'
LIEN_EMPLOI = '/dashboard/enseignant/emploi'
LIEN_NOTES = '/dashboard/enseignant/notes'

TYPES_RECLAMATION = {'note': 'sur une note', 'absence': 'sur une absence'}


def _sans_echec(f):
    @functools.wraps(f)
    def enveloppe(*a, **k):
        try:
            return f(*a, **k)
        except Exception:
            logger.exception('Notification enseignant en échec : %s', f.__name__)
            return 0
    return enveloppe


def _comptes(prof_ids):
    """Comptes actifs (rôle enseignant) de ces fiches Prof."""
    from apps.authentication.models import CustomUser
    return CustomUser.objects.filter(prof_profile__id__in=list(prof_ids), is_active=True)


def _annee_active():
    from apps.parametres.models import Year
    y = Year.objects.filter(est_active=True).order_by('-annee').first()
    return y.annee if y else None


@_sans_echec
def reclamation_deposee(reclamation):
    """Réclamation d'un étudiant → les enseignants de l'élément (année active)."""
    if not reclamation.em_id:
        return 0
    from apps.edt.notifier import notifier
    from apps.suivi.models import SuiviePointage
    qs = SuiviePointage.objects.filter(em_id=reclamation.em_id, prof__isnull=False)
    annee = _annee_active()
    if annee:
        qs = qs.filter(annee_universitaire=annee)
    profs = set(qs.values_list('prof_id', flat=True).distinct())
    if not profs:
        return 0
    element = reclamation.em_intitule or reclamation.em_code or "l'élément"
    quoi = TYPES_RECLAMATION.get(reclamation.type_reclamation, '')
    return notifier(
        _comptes(profs),
        'Nouvelle réclamation — %s' % element,
        '%s a déposé une réclamation%s. Répondez-lui dans l\'onglet Réclamations.'
        % (reclamation.etudiant_nom or 'Un étudiant', (' ' + quoi) if quoi else ''),
        type='info', lien=LIEN_RECLAMATIONS,
    )


@_sans_echec
def contestation_traitee(rs):
    """Décision sur la contestation d'une séance « Non fait » → son enseignant."""
    from apps.edt.notifier import notifier
    acceptee = rs.statut == 'acceptee'
    seance = ' '.join(x for x in (rs.em_code, rs.type_seance) if x) or 'votre séance'
    quand = ('semaine %s' % rs.numero_semaine) if rs.numero_semaine else ''
    message = 'Votre contestation (%s%s) a été %s.' % (
        seance, (', ' + quand) if quand else '', 'acceptée' if acceptee else 'rejetée')
    if rs.reponse:
        message += ' Réponse : %s' % rs.reponse
    return notifier(
        _comptes([rs.prof_id]),
        'Contestation %s' % ('acceptée' if acceptee else 'rejetée'),
        message,
        type='succes' if acceptee else 'avertissement', lien=LIEN_EMPLOI,
    )


@_sans_echec
def session_ouverte(session):
    """Saisie des notes ouverte → les enseignants de l'année et des semestres
    de la session (ceux qui ont des séances pointées)."""
    from apps.edt.notifier import notifier
    from apps.suivi.models import SuiviePointage
    annee = getattr(session.annee_univ, 'annee', None) if session.annee_univ_id else None
    if not annee:
        return 0
    qs = SuiviePointage.objects.filter(annee_universitaire=annee, prof__isnull=False)
    ts = (session.type_semestre or '')[:1].upper()       # « Impairs » → I
    if ts in ('I', 'P'):
        qs = qs.filter(type_semestre=ts)
    profs = set(qs.values_list('prof_id', flat=True).distinct())
    if not profs:
        return 0
    libelle = session.intitule or session.code or 'session d\'examens'
    return notifier(
        _comptes(profs),
        'Saisie des notes ouverte',
        'La saisie des notes est ouverte : %s. Saisissez vos notes dans l\'onglet Notes.' % libelle,
        type='info', lien=LIEN_NOTES,
    )
