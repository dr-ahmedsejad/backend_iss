"""
Génération du suivi, précédée de sa projection.

Le socle lit `emplois.Emplois` pour bâtir le suivi. Cette table ne porte NI
numéro de semaine, ni rien qui permette de savoir de quelle semaine elle parle
(`apps/emplois/models.py`) : elle dit « lundi 08h00 », jamais quel lundi. Elle
n'est donc pas un stock, mais une boîte de dépôt sans étiquette — que le socle
vide d'ailleurs derrière lui après génération (`apps/suivi/views.py`).

Tant que le remplissage de cette boîte était un geste humain distinct, deux
pannes silencieuses restaient possibles, et aucune n'était détectable :

  - transmettre la semaine 2 puis générer la semaine 3 : l'emploi du temps de
    la 2 était enregistré, pointé et payé comme celui de la 3 ;
  - corriger une séance après l'avoir transmise : la correction n'atteignait
    jamais le suivi.

On ne peut pas vérifier une concordance dont un des deux côtés ne porte pas
l'information. La seule issue est de supprimer l'écart : la projection est
faite ICI, dans la requête même qui génère, sur le numéro de semaine réclamé
par l'appelant. Il n'y a plus deux sources, donc plus de désaccord possible.

`apps/suivi/` n'est pas modifié — on compose autour. La vue du socle est
héritée et appelée telle quelle ; seule l'URL est captée en amont, dans
`siga/urls.py`.
"""
import logging

from django.db import transaction

from apps.suivi.views import SuivieViewSet

from .services.archive import archiver_semaine
from .services.planification import projeter_semaine

logger = logging.getLogger(__name__)


class SuivieAvecProjectionViewSet(SuivieViewSet):
    """`SuivieViewSet` du socle, dont la génération projette d'abord.

    La méthode s'appelle `ajouter_suivie` : c'est son `url_path` qui vaut
    « ajouter ». On surcharge donc le nom Python, pas celui de l'URL.
    """

    def ajouter_suivie(self, request, *args, **kwargs):
        d     = request.data
        annee = d.get('annee_universitaire')
        ts    = d.get('type_semestre')
        try:
            numero = int(d.get('numero_semaine'))
        except (TypeError, ValueError):
            numero = None

        # Paramètres incomplets : on ne devine rien, on laisse le socle répondre
        # son propre 400. Projeter sur une semaine supposée serait exactement la
        # faute que ce module supprime.
        projete, perimetre = False, None
        if annee and ts and numero:
            # Le périmètre est celui que le socle emploiera juste après pour
            # LIRE puis SUPPRIMER `Emplois`. Le prendre ici garantit que l'on
            # projette précisément ce qui sera lu — ni plus, ni moins.
            perimetre = self.user_dept_ids()      # None = aucune borne (admin)
            if perimetre is None or perimetre:
                # Pas de purge préalable ici, contrairement à l'ESP.
                #
                # L'ESP vidait `Emplois` sur tout le périmètre avant de
                # projeter, pour qu'aucune ligne d'une semaine précédente ne
                # survive. À l'ISS, l'ancien emploi du temps reste en service
                # (§7 bis) : cette purge détruirait la saisie manuelle des
                # groupes qui n'ont pas encore basculé — et le socle, lisant
                # ensuite une table vide, ne générerait plus rien pour eux.
                # Sans un mot, et avec la paie au bout.
                #
                # `projeter_semaine` fait désormais le ménage lui-même, borné
                # aux groupes ayant adopté le nouveau moteur. La protection
                # contre les lignes périmées est conservée pour eux, et les
                # autres ne sont pas touchés.
                with transaction.atomic():
                    bilan = projeter_semaine(annee, ts, numero,
                                             departements=perimetre)
                projete = True
                logger.info(
                    'Projection automatique avant generation : %s %s S%s '
                    '(%s lignes purgees, %s projetees) par %s',
                    annee, ts, numero, bilan['supprimees'], bilan['projetees'],
                    request.user.username)

        reponse = super().ajouter_suivie(request, *args, **kwargs)

        # Le message du socle annonce « EDT archive et vide ». C'etait exact,
        # et c'est devenu deroutant : ce qui est vide est `Emplois`, une boite
        # de transfert que personne ne voit — jamais l'emploi du temps que le
        # planificateur vient d'ecrire, lequel n'a pas bouge. Lu par un chef de
        # departement, « EDT vide » annonce une catastrophe qui n'a pas eu
        # lieu. Depuis que la projection est automatique, le remplissage et le
        # vidage de cette boite se font dans la meme requete : c'est un detail
        # d'implementation, plus un evenement a rapporter.
        #
        # `apps/suivi/` n'etant pas modifie, on reecrit ici la phrase, et elle
        # seule : les compteurs de la reponse restent ceux du socle.
        #
        # On ne réécrit la phrase que si la projection a effectivement fourni
        # quelque chose. Pendant la coexistence, un groupe encore saisi à
        # l'ancienne génère un suivi parfaitement valide sans qu'aucune séance
        # n'ait été projetée : annoncer « à partir de 0 séance » ferait croire
        # à un échec là où tout s'est bien passé. Dans ce cas, le message du
        # socle reste le sien.
        if (projete and bilan['projetees']
                and reponse.status_code < 400
                and isinstance(reponse.data, dict)):
            n = bilan['projetees']
            reponse.data['message'] = (
                f"Suivi de la semaine {numero} généré à partir de "
                f"{n} séance{'s' if n > 1 else ''} de l'emploi du temps.")

        # L'archive fige la version qui a servi. On ne la prend qu'en cas de
        # succès : une génération refusée — semaine déjà générée, délai passé —
        # n'a rien fait servir, et une prise de vue de plus brouillerait le
        # sélecteur de versions de l'écran « Historique ».
        if projete and reponse.status_code < 400:
            archiver_semaine(annee, ts, numero, departements=perimetre)

        return reponse
