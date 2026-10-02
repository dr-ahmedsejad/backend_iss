"""
Comment nommer un groupe sur une fiche de présence.

La fiche titrait « Fiche de Présence — Statistique — G1 ». En 2026-2027, trois
groupes s'appellent « G1 » : en L1 de Statistique, en L2 et en L3 de SEA. Deux
fiches d'une même filière se distinguaient donc par… rien. Un surveillant qui
prend la mauvaise feuille fait pointer une promotion sur la liste d'une autre.

Le niveau tranche : « Statistique — L1 G1 ».

Deux exceptions, toutes deux relevées dans les données :

  * le nom porte DÉJÀ son niveau — « SEA L2 - G1 », « LPSEA L2 », « SDID L2 ».
    Préfixer donnerait « L2 SEA L2 - G1 » ;
  * le niveau n'en est pas un — « Transversal », pour HE, ST, STAGES. Il n'a
    pas de chiffre, et « Transversal HE » ne dirait rien de plus que « HE ».
"""
import re


def libelle_groupe(nom, niveau) -> str:
    """« G1 » + « L1 » → « L1 G1 » ; le reste inchangé."""
    nom = (nom or '').strip()
    niveau = (niveau or '').strip()
    if not nom or not niveau:
        return nom

    # Un niveau d'étude porte un rang : L1, M2, E3. « Transversal » n'en a pas.
    if not re.search(r'\d', niveau):
        return nom

    # Déjà présent dans le nom, comme MOT : « SEA L2 - G1 » le porte, mais un
    # nom qui contiendrait « L21 » ne porte pas « L2 ».
    if re.search(r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(niveau), nom, re.IGNORECASE):
        return nom

    return '%s %s' % (niveau, nom)
