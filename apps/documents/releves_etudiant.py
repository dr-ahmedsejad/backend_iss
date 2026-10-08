"""
Les relevés de notes d'un ou de plusieurs étudiants, tous leurs semestres,
en un seul PDF.

La génération groupée (`generer_documents_groupe`) fait un relevé par étudiant
pour UN semestre. Ici c'est l'inverse : un étudiant, chacun de ses semestres,
dans l'ordre (année puis semestre), fusionnés en un document à imprimer d'un
coup — demande du 08/10/2026.

Chaque relevé reste un document officiel (numéro de série + QR), le même que
celui qu'on obtiendrait semestre par semestre : un relevé déjà émis pour
(étudiant, année, semestre) est RÉUTILISÉ, pas renuméroté. Un semestre sans
résultat (en cours, jamais noté) est écarté, et l'aperçu le dit.

« Sans résultat » se lit sur les NOTES de l'inscription, pas sur la moyenne :
le calcul du relevé rend 0,00 pour un semestre en cours (mesuré le 08/10/2026,
S5 et S6 d'un étudiant en 2026-2027) — imprimé, il passerait pour un échec.
"""
from io import BytesIO
from types import SimpleNamespace

TYPE = 'releve_semestre'


def a_des_notes(ip):
    """Au moins une note saisie sur les éléments de CETTE inscription."""
    from apps.evaluations.models import Note
    return Note.objects.filter(inscription_element__inscription_ped=ip,
                               valeur__isnull=False).exists()


def semestres_de(etudiant):
    """Ses inscriptions pédagogiques, de la plus ancienne à la plus récente,
    avec ce que dirait le relevé (moyenne, décision). Lecture seule."""
    from apps.inscriptions.models import InscriptionPedagogique
    from .services import _build_context_releve, _get_institution

    institution = _get_institution(etudiant)
    ips = (InscriptionPedagogique.objects
           .filter(inscription_admin__etudiant=etudiant)
           .select_related('semestre', 'inscription_admin__annee_univ', 'inscription_admin__filiere'))
    lignes, vus = [], set()
    for ip in ips:
        annee = ip.inscription_admin.annee_univ.annee if ip.inscription_admin.annee_univ_id else ''
        cle = (annee, ip.semestre_id)
        if cle in vus:
            continue
        vus.add(cle)
        ctx = _build_context_releve(
            SimpleNamespace(semestre_id=ip.semestre_id, annee_universitaire=annee),
            etudiant, institution, {'annee_universitaire': annee, 'semestre': ip.semestre_id})
        moyenne = ctx.get('moyenne_semestre')
        resultats = moyenne is not None and a_des_notes(ip)
        lignes.append({
            'annee_universitaire': annee,
            'semestre': ip.semestre_id,
            'semestre_code': ip.semestre.code_semestre if ip.semestre_id else '',
            'filiere': (ip.inscription_admin.filiere.code
                        if ip.inscription_admin.filiere_id else ''),
            'moyenne': float(moyenne) if resultats else None,
            'decision': ctx.get('decision_semestre') if resultats else None,
            'a_des_resultats': resultats,
        })
    lignes.sort(key=lambda l: (l['annee_universitaire'], l['semestre_code']))
    return lignes


# Au-delà, un seul PDF devient lourd à produire et à imprimer : on scinde.
MAX_ETUDIANTS = 60


def _ajouter_releves(writer, etudiant, user):
    """Ajoute au PDF les relevés de l'étudiant qui ont des résultats ;
    rend ses lignes (avec `imprime` et `numero_serie`) et le nombre ajouté."""
    from .models import DocumentOfficiel
    from .services import _creer_document_officiel, _generer_pdf

    lignes, nb = semestres_de(etudiant), 0
    for l in lignes:
        if not l['a_des_resultats']:
            continue
        annee, sem = l['annee_universitaire'], l['semestre']
        doc = (DocumentOfficiel.objects
               .filter(etudiant=etudiant, type_document=TYPE,
                       annee_universitaire=annee, semestre_id=sem)
               .order_by('-id').first()
               or _creer_document_officiel(etudiant, TYPE, annee, sem, user))
        pdf = _generer_pdf(doc, etudiant, {'annee_universitaire': annee, 'semestre': sem},
                           is_duplicata=False)
        l['imprime'] = bool(pdf)
        if pdf:
            writer.append(BytesIO(pdf))
            l['numero_serie'] = doc.numero_serie
            nb += 1
    return lignes, nb


def generer_releves_etudiants(etudiants, user):
    """(pdf_bytes, bilan) : les relevés de PLUSIEURS étudiants, l'un après
    l'autre dans l'ordre donné, en un seul PDF. Un étudiant sans aucun
    résultat est sauté et signalé dans le bilan, sans bloquer les autres.
    Lève ValueError si rien n'est imprimable."""
    from pypdf import PdfWriter

    if len(etudiants) > MAX_ETUDIANTS:
        raise ValueError(f'{len(etudiants)} étudiants : {MAX_ETUDIANTS} au plus par impression.')
    writer, bilan, total = PdfWriter(), [], 0
    for etudiant in etudiants:
        lignes, nb = _ajouter_releves(writer, etudiant, user)
        bilan.append({'etudiant': etudiant.pk, 'matricule': etudiant.matricule,
                      'releves': nb, 'semestres': lignes})
        total += nb
    if total == 0:
        noms = ', '.join(e.matricule for e in etudiants)
        raise ValueError(f'Aucun semestre avec des résultats ({noms}) : aucun relevé à imprimer.')
    out = BytesIO()
    writer.write(out)
    writer.close()
    return out.getvalue(), bilan


def generer_releves_etudiant(etudiant, user):
    """(pdf_bytes, lignes) pour un seul étudiant."""
    pdf, bilan = generer_releves_etudiants([etudiant], user)
    return pdf, bilan[0]['semestres']
