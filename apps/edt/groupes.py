"""
Lire la structure des groupes de l'ISS.

À l'ESP, un groupe déclare son sous-groupe dans `Departement.groupe` : « G1 »,
« G2 ». Les règles de conflit s'appuient dessus pour savoir que deux groupes
d'une même filière ne partagent aucun étudiant — c'est précisément ce pour quoi
on les a scindés.

À l'ISS le champ existe mais il est **vide sur les 26 groupes planifiables**
(mesuré le 02/09/2026). Le sous-groupe n'est écrit que dans le `nom` :

    « G1 »              « G2 »                 sous-groupe seul
    « SEA L2 - G1 »     « SEA L2 - G2 »        souche + sous-groupe
    « SDID L2 G1 »      « SDID L2 G2 »         idem, sans tiret
    « SDID L2 »         « HE »   « SEA L3 »    aucun sous-groupe

On lit donc le nom. Remplir `Departement.groupe` aurait été plus fidèle à
l'ESP, mais trois écrans du socle affichent `groupe or nom`
(`apps/avancement/views.py`, `apps/suivi/views.py`, `apps/documents/services.py`) :
le remplir ferait passer « SEA L2 - G1 » à « G1 » sur le suivi et sur les
documents officiels. On ne touche pas au socle.

Le champ garde malgré tout la priorité : le jour où il sera renseigné, il fera
foi, et ce module deviendra une simple compatibilité.
"""
import re

# « …G1 », « …- G2 », « … G 3 », ou le nom entier réduit à « G1 ».
#
# Deux exigences, et chacune écarte une erreur précise :
#
#   * la lettre G suivie de chiffres, en FIN de nom. « LPSEA L2 » et « STATL1 »
#     se terminent aussi par un chiffre : les prendre pour des sous-groupes
#     aurait scindé des groupes qui n'existent qu'en un exemplaire ;
#   * G doit ouvrir le mot — début du nom, ou précédé d'un séparateur. Sans
#     cela « SEAG5 » passerait pour un sous-groupe. Le séparateur est capturé
#     par le motif pour que `souche()` le retire aussi : « SEA L2 - G1 » doit
#     rendre « SEA L2 », qui est le nom du groupe entier, et non « SEA L2 - ».
#
# Un `\b` ne suffisait pas : dans « SEA L2_G5 », le souligné et le G sont tous
# deux des caractères de mot, il n'y a donc aucune frontière entre eux.
_SOUS_GROUPE = re.compile(r'(?:^|[\s\-–_])[\s\-–_]*G\s*(\d+)\s*$', re.IGNORECASE)

# Le niveau qui désigne un enseignement suivi par toute une promotion, filières
# mélangées — à l'ISS : HE, ST, STAGES. On le reconnaît au libellé du niveau
# plutôt qu'à son identifiant : un identifiant en dur se périme au premier
# rechargement de référentiel.
LIBELLE_NIVEAU_TRANSVERSAL = 'transversal'


def sous_groupe(departement) -> str:
    """« G1 », « G2 »… ou '' si ce groupe n'est pas subdivisé.

    Le champ déclaré l'emporte ; à défaut on lit la fin du nom.
    """
    if departement is None:
        return ''
    declare = (departement.groupe or '').strip()
    if declare:
        return declare.upper().replace(' ', '')
    trouve = _SOUS_GROUPE.search(departement.nom or '')
    return f'G{trouve.group(1)}' if trouve else ''


def souche(departement) -> str:
    """Ce qui reste du nom une fois le sous-groupe retiré.

    « SEA L2 - G1 » → « SEA L2 », qui est aussi le nom du groupe entier. C'est
    ce rapprochement qui permet de savoir qu'un groupe contient ses propres
    sous-groupes.
    """
    if departement is None:
        return ''
    nom = (departement.nom or '').strip()
    if (departement.groupe or '').strip():
        # Le sous-groupe est déclaré à part : le nom EST la souche.
        return nom.casefold()
    return _SOUS_GROUPE.sub('', nom).strip().casefold()


def est_transversal(departement) -> bool:
    """Ce groupe réunit-il toute une promotion, filières mélangées ?

    À l'ESP c'était le « groupe de promotion », reconnaissable à l'absence de
    filière. À l'ISS l'absence de filière ne suffit pas : treize groupes
    planifiables ont `filiere IS NULL` par simple héritage historique — « SEA
    L3 », « SDID L2 » — sans rien avoir de transversal. Ce qui distingue HE et
    ST, c'est leur NIVEAU.
    """
    if departement is None or departement.is_container:
        return False
    niveau = getattr(departement, 'niveau', None)
    libelle = getattr(niveau, 'niveau', '') if niveau is not None else ''
    return libelle.strip().casefold() == LIBELLE_NIVEAU_TRANSVERSAL
