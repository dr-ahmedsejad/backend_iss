"""
Qui doit figurer sur la fiche d'appel d'une séance.

L'écran et le PDF prenaient tous deux le GROUPE entier —
`Etudiant.objects.filter(departement_id=…)`. Deux conséquences, mesurées sur
`iss` le 02/10/2026 :

  * des noms EN TROP. Sur l'élément `ST41` du groupe #44, dix noms apparaissaient
    alors que trois étudiants seulement le suivent : les sept autres l'avaient
    validé une année précédente. Une absence notée pour eux est fausse ;
  * des noms MANQUANTS. Quatorze inscriptions de l'année portent sur un élément
    que le groupe de l'étudiant n'enseigne pas — des DETTES. Ces étudiants ne
    figuraient sur aucune fiche : ils ne pouvaient donc jamais être pointés.

La source juste existe déjà : `InscriptionElement`, l'inscription pédagogique à
l'élément pour l'année. C'est elle qui porte les dettes et qui sait qui suit
quoi.

MAIS elle n'est pas toujours remplie. Sur `SDID171`, aucune inscription : filtrer
strictement rendrait une fiche VIDE, ce qui serait pire que trop de noms. D'où
la règle de repli, et le champ `source` qui la DIT — une liste non vérifiée ne
doit pas se faire passer pour une liste vérifiée.
"""
SOURCE_INSCRIPTIONS = 'inscriptions'
SOURCE_GROUPE = 'groupe'


def _etudiants_inscrits(em_id, annee):
    """Les étudiants inscrits à cet élément CETTE année, où qu'ils soient."""
    from apps.absence.models import Etudiant
    return (Etudiant.objects
            .filter(inscriptions_admin__inscriptions_ped__inscriptions_elements__em_id=em_id,
                    inscriptions_admin__annee_univ__annee=annee)
            .distinct())


def _groupes_qui_enseignent(em_id, annee):
    """Les groupes qui ont au moins une séance de cet élément cette année.

    Sert à n'attacher une dette qu'aux fiches où l'étudiant peut réellement se
    présenter — et à ne pas la dupliquer sur le groupe dont il fait partie,
    puisqu'il y figurerait déjà.
    """
    from apps.edt.models import SeanceReelle
    return set(SeanceReelle.objects
               .filter(em_id=em_id, semaine__annee_universitaire=annee)
               .values_list('departement_id', flat=True))


def liste_appel(departement_id, em_id, annee) -> dict:
    """La liste d'appel d'une séance : qui suit CET élément dans CE groupe.

    Rend :
      {'etudiants': [...], 'dettes': [...], 'source': 'inscriptions'|'groupe'}

    `etudiants` sont ceux du groupe ; `dettes` ceux d'un AUTRE groupe inscrits à
    l'élément, dont le groupe ne l'enseigne pas — ils n'ont pas de fiche à eux.
    Ils sont rendus à part pour que l'écran et le PDF les signalent : un nom
    venu d'ailleurs qu'on prendrait pour un camarade de promotion ferait douter
    de toute la liste.
    """
    from apps.absence.models import Etudiant

    du_groupe = Etudiant.objects.filter(departement_id=departement_id)

    if not em_id:
        # Une séance sans élément — sport, instruction militaire — n'a pas
        # d'inscription pédagogique à consulter.
        #
        # Ce raccourci ne change RIEN au résultat : sans élément, le chemin
        # général ne trouverait aucun inscrit et retomberait de toute façon sur
        # le groupe. Vérifié par sabotage — le retirer ne fait tomber aucun
        # test. Il épargne deux requêtes et dit l'intention.
        return {'etudiants': list(du_groupe.order_by('matricule')),
                'dettes': [], 'source': SOURCE_GROUPE}

    inscrits = _etudiants_inscrits(em_id, annee)
    inscrits_du_groupe = inscrits.filter(departement_id=departement_id)

    if not inscrits_du_groupe.exists():
        # Inscriptions pédagogiques non saisies pour cet élément : on garde le
        # groupe entier plutôt que de rendre une fiche vide, et `source` le dit.
        return {'etudiants': list(du_groupe.order_by('matricule')),
                'dettes': [], 'source': SOURCE_GROUPE}

    enseignants = _groupes_qui_enseignent(em_id, annee)
    dettes = [e for e in inscrits.exclude(departement_id=departement_id)
                                 .order_by('matricule')
              if e.departement_id not in enseignants]

    return {'etudiants': list(inscrits_du_groupe.order_by('matricule')),
            'dettes': dettes, 'source': SOURCE_INSCRIPTIONS}
