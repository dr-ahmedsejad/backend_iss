"""
Sérialisation de la planification hebdomadaire.

La règle de non-double-affectation est la même que sur `emplois.Emplois`, et
pour la même raison : rien, jusqu'ici, ne vérifiait qu'un enseignant, une salle
ou des étudiants ne soient pas attendus à deux endroits — `check_dispo` existe
côté serveur mais aucun écran ne l'appelle. Elle tolère la séance PARTAGÉE — un
même cours réunissant plusieurs groupes — et n'interdit que les vraies
collisions.

Elle est appliquée ici, à la source, plutôt que sur la projection : une erreur
signalée au moment où l'on pose la séance se corrige ; découverte à la
projection, elle bloque toute une semaine sans dire clairement pourquoi.
"""
from rest_framework import serializers

from .models import (DemandeLiberation, EmploiArchive, GrilleType,
                     SeanceReelle, SeanceType)


# Les quatre axes qui définissent l'identité d'une séance. Deux lignes qui
# coïncident sur les quatre sont le même cours, servi à un groupe de plus.
AXES_IDENTITE = ('prof_id', 'em_id', 'type_seance_fk_id', 'salle_id')


def _meme_seance(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in AXES_IDENTITE)


def _refuser_collision(voisines, candidat, *, ou):
    """Lève une erreur si l'enseignant ou la salle est déjà pris ailleurs.

    `voisines` : les séances des AUTRES groupes sur la même case.
    `ou` : fonction qui nomme le groupe d'une voisine — signaler un conflit
    sans dire où il se trouve oblige à chercher à l'aveugle.
    """
    erreurs = {}
    for v in voisines:
        if candidat.get('prof_id') and v['prof_id'] == candidat['prof_id'] \
                and not _meme_seance(v, candidat):
            erreurs['prof'] = (f"Cet enseignant assure déjà une autre séance sur "
                               f"ce créneau ({ou(v)}).")
        if candidat.get('salle_id') and v['salle_id'] == candidat['salle_id'] \
                and not _meme_seance(v, candidat):
            erreurs['salle'] = (f"Cette salle est déjà occupée sur ce créneau "
                                f"({ou(v)}).")
    if erreurs:
        raise serializers.ValidationError(erreurs)


def _refuser_semestre_hors_parite(em_id, type_semestre):
    """Un élément du semestre pair n'a rien à faire dans une période impaire.

    Le catalogue est bâti sur la filière et le NIVEAU, or un niveau couvre deux
    semestres : la moitié des éléments proposés relèvent de l'autre période. Un
    élément de S2 posé dans une grille de S1 se planifie, se projette et se
    génère sans un mot — puis disparaît de tout écran filtré par semestre, car
    ces écrans, eux, ont raison de l'écarter. La séance existe, personne ne la
    voit, et elle fausse les charges de la période où elle a échoué.

    On refuse ici plutôt que de se contenter de filtrer la liste : la liste
    guide, elle ne protège pas — un autre client de l'API la contournerait.

    Le semestre se lit sur `EM.semestre`, et non sur celui de l'UE LMD comme à
    l'ESP : à l'ISS, 56 des 207 éléments n'ont pas de `module_lmd`, alors que
    tous portent un `semestre`. Passer par l'UE aurait laissé filer un quart du
    catalogue sans un mot — exactement la panne muette que cette règle
    supprime. L'UE reste consultée en second, pour les référentiels où c'est
    elle qui porte l'information.
    """
    if not (em_id and type_semestre):
        return
    from apps.em.models import EM
    sem = (EM.objects.filter(pk=em_id)
           .values_list('semestre__code_semestre', 'semestre__type_semestre')
           .first())
    if not sem or not sem[1]:
        sem = (EM.objects.filter(pk=em_id)
               .values_list('module_lmd__semestre__code_semestre',
                            'module_lmd__semestre__type_semestre').first())
    if not sem or not sem[1] or sem[1] == type_semestre:
        return
    code, parite = sem
    periode = {'I': 'impairs', 'P': 'pairs'}
    raise serializers.ValidationError({'em': (
        f"Cet enseignement relève du {code}, qui appartient aux semestres "
        f"{periode.get(parite, parite)}. La période en cours est celle des "
        f"semestres {periode.get(type_semestre, type_semestre)} : la séance "
        f"serait planifiée sans jamais apparaître au suivi. Choisissez un "
        f"enseignement du {code[0]}{'1' if type_semestre == 'I' else '2'}, ou "
        f"corrigez le semestre de cet élément dans le référentiel.")})


def _memes_etudiants(a, b) -> bool:
    """Ces deux groupes contiennent-ils les mêmes étudiants ?

    C'est la règle de l'ESP, portée sur la structure réelle de l'ISS. Le fond
    ne change pas : un étudiant ne peut pas être à deux endroits, donc deux
    séances au même créneau sur deux groupes qui se recoupent sont une erreur
    de planification, quoi qu'en dise le reste.

    Ce qui change, c'est la lecture de la structure. À l'ESP le sous-groupe est
    déclaré dans `Departement.groupe` ; à l'ISS ce champ est vide partout et le
    sous-groupe n'est écrit que dans le nom — voir `apps/edt/groupes.py`. Porter
    la règle telle quelle aurait tenu « G1 » et « G2 » pour les mêmes élèves et
    refusé 1 107 couples parfaitement légitimes déjà présents dans le suivi.

    Quatre cas, dans l'ordre où on les tranche :

      * un groupe TRANSVERSAL (HE, ST) réunit toute une promotion, filières
        mélangées : on tient pour certain qu'il croise tous les groupes de son
        année. Supposer l'inverse laisserait passer de vraies collisions ;
      * deux niveaux différents ne se croisent jamais ;
      * deux filières différentes non plus ;
      * même filière : deux sous-groupes DISTINCTS ne partagent aucun étudiant
        — c'est ce pour quoi on les a formés — mais un groupe entier contient
        ses propres sous-groupes.
    """
    from .groupes import est_transversal, sous_groupe, souche

    if a.pk is not None and a.pk == b.pk:
        return True

    ga, gb = sous_groupe(a), sous_groupe(b)

    # Un enseignement transversal réunit la promotion entière : il croise tout
    # ce qui se planifie la même année, sauf un sous-groupe explicitement
    # distinct du sien.
    if est_transversal(a) or est_transversal(b):
        if a.annee_universitaire != b.annee_universitaire:
            return False
        return not (ga and gb and ga != gb)

    if a.niveau_id is None or a.niveau_id != b.niveau_id:
        return False

    if a.filiere_id is not None and b.filiere_id is not None:
        if a.filiere_id != b.filiere_id:
            return False
        # Même filière, même niveau : deux sous-groupes nommés se partagent la
        # promotion ; sans sous-groupe, c'est le groupe entier, qui les
        # contient tous les deux.
        return ga == gb if (ga and gb) else True

    # Au moins un des deux n'a pas de filière — treize groupes de l'ISS sont
    # dans ce cas par héritage. On se rabat sur le nom : « SDID L2 » et
    # « SDID L2 G1 » ont la même souche, donc les mêmes étudiants.
    sa, sb = souche(a), souche(b)
    if sa and sa == sb:
        return not (ga and gb and ga != gb)
    return False


def _refuser_chevauchement(mien, voisins):
    """Refuse une séance qui attendrait les mêmes étudiants ailleurs.

    `voisins` : couples (departement, libellé de ce qui l'occupe).
    """
    for autre, quoi in voisins:
        if _memes_etudiants(mien, autre):
            raise serializers.ValidationError({'creneau_fk': (
                f"Les étudiants de « {mien.nom} » ont déjà cours sur ce "
                f"créneau avec « {autre.nom} » ({quoi}). Un étudiant ne peut "
                f"pas être à deux endroits : le tronc commun et la spécialité "
                f"doivent se partager les créneaux.")})


def _pk(v):
    return getattr(v, 'pk', v)


class SeanceTypeSerializer(serializers.ModelSerializer):
    jour_libelle    = serializers.CharField(source='jour_fk.jour', read_only=True)
    creneau_libelle = serializers.CharField(source='creneau_fk.creneau', read_only=True)
    em_code         = serializers.CharField(source='em.code_em', read_only=True, default=None)
    em_intitule     = serializers.CharField(source='em.intitule', read_only=True, default=None)
    prof_nom        = serializers.CharField(source='prof.nom', read_only=True, default=None)
    salle_nom       = serializers.CharField(source='salle.nom', read_only=True, default=None)
    type_libelle    = serializers.CharField(source='type_seance_fk.type_seance', read_only=True)
    type_special    = serializers.BooleanField(source='type_seance_fk.is_special',
                                               read_only=True, default=False)
    departement     = serializers.IntegerField(source='grille.departement_id', read_only=True)
    modifiable      = serializers.SerializerMethodField()

    def get_modifiable(self, obj) -> bool:
        """Ce lecteur a-t-il le droit de toucher à cette case ?

        Une case qu'on ne peut pas écrire doit se donner à lire, pas à
        remplir : sans cela l'écran l'offre à la saisie et le refus n'arrive
        qu'à l'enregistrement, après le travail.

        À l'ESP la réponse dépendait de deux axes — mon groupe, ou mon pôle
        pour cet enseignement. L'ISS n'a qu'un axe : le groupe délégué.
        """
        demandeur = getattr(self.context.get('request'), 'user', None)
        if demandeur is None or not demandeur.is_authenticated:
            return False
        if demandeur.is_superuser:
            return True
        return demandeur.managed_departements.filter(
            pk=obj.grille.departement_id).exists()

    class Meta:
        model  = SeanceType
        fields = ['id', 'grille', 'departement', 'jour_fk', 'jour_libelle',
                  'creneau_fk', 'creneau_libelle', 'em', 'em_code', 'em_intitule',
                  'prof', 'prof_nom', 'salle', 'salle_nom',
                  'type_seance_fk', 'type_libelle', 'type_special',
                  'modifiable']

    def validate(self, attrs):
        courant = self.instance

        def champ(nom):
            if nom in attrs:
                return _pk(attrs[nom])
            return getattr(courant, f'{nom}_id', None) if courant else None

        grille = attrs.get('grille') or (courant.grille if courant else None)
        if grille is None:
            return attrs

        _refuser_semestre_hors_parite(champ('em'), grille.type_semestre)

        candidat = {f'{n}_id': champ(n) for n in ('prof', 'em', 'salle', 'type_seance_fk')}
        candidat['type_seance_fk_id'] = champ('type_seance_fk')

        autres = (SeanceType.objects
                  .filter(grille__annee_universitaire=grille.annee_universitaire,
                          grille__type_semestre=grille.type_semestre,
                          jour_fk_id=champ('jour_fk'),
                          creneau_fk_id=champ('creneau_fk'))
                  .exclude(grille__departement_id=grille.departement_id)
                  .select_related('grille__departement', 'em'))
        if courant:
            autres = autres.exclude(pk=courant.pk)
        autres = list(autres)

        _refuser_chevauchement(grille.departement, [
            (a.grille.departement, a.em.code_em if a.em_id else 'séance')
            for a in autres])

        _refuser_collision(
            [{'prof_id': a.prof_id, 'em_id': a.em_id, 'salle_id': a.salle_id,
              'type_seance_fk_id': a.type_seance_fk_id,
              'nom': a.grille.departement.nom} for a in autres],
            candidat, ou=lambda v: v['nom'] or 'un autre groupe')
        return attrs


class SeanceReelleSerializer(serializers.ModelSerializer):
    # Tout vient de la ligne `Semaine` : date, jour et numéro, sans calcul.
    date            = serializers.DateField(source='semaine.date', read_only=True)
    jour_fk         = serializers.IntegerField(source='semaine.jour_fk_id', read_only=True)
    jour_libelle    = serializers.CharField(source='semaine.jour_fk.jour', read_only=True)
    numero_semaine  = serializers.IntegerField(source='semaine.numero_semaine', read_only=True)
    creneau_libelle = serializers.CharField(source='creneau_fk.creneau', read_only=True)
    em_code         = serializers.CharField(source='em.code_em', read_only=True, default=None)
    em_intitule     = serializers.CharField(source='em.intitule', read_only=True, default=None)
    prof_nom        = serializers.CharField(source='prof.nom', read_only=True, default=None)
    prof_initial_nom = serializers.CharField(source='prof_initial.nom', read_only=True, default=None)
    salle_nom       = serializers.CharField(source='salle.nom', read_only=True, default=None)
    type_libelle    = serializers.CharField(source='type_seance_fk.type_seance', read_only=True)
    type_special    = serializers.BooleanField(source='type_seance_fk.is_special',
                                               read_only=True, default=False)
    departement_nom = serializers.CharField(source='departement.nom', read_only=True)
    # Le sous-groupe de TD/TP, quand il y en a un : la carte l'ecrit entre
    # parentheses derriere la matiere, comme sur le PDF.
    departement_groupe = serializers.CharField(source='departement.groupe',
                                               read_only=True, default='')
    # Les autres groupes qui suivent le MEME cours. L'ecran doit le montrer :
    # modifier une seance partagee modifie toutes les autres.
    groupes_partages = serializers.SerializerMethodField()

    def get_groupes_partages(self, obj):
        if not obj.cle_partage:
            return []
        return [{'departement': s.departement_id, 'nom': s.departement.nom}
                for s in obj.soeurs()]

    class Meta:
        model  = SeanceReelle
        fields = ['id', 'departement', 'departement_nom', 'departement_groupe',
                  'semaine', 'numero_semaine',
                  'date', 'jour_fk', 'jour_libelle', 'creneau_fk', 'creneau_libelle',
                  'em', 'em_code', 'em_intitule', 'prof', 'prof_nom',
                  'prof_initial', 'prof_initial_nom', 'salle', 'salle_nom',
                  'type_seance_fk', 'type_libelle', 'type_special',
                  'origine', 'seance_type', 'annulee', 'observations', 'modifiee_le',
                  'cle_partage', 'groupes_partages']
        read_only_fields = ['modifiee_le', 'cle_partage']

    def validate(self, attrs):
        courant = self.instance

        def champ(nom):
            if nom in attrs:
                return _pk(attrs[nom])
            return getattr(courant, f'{nom}_id', None) if courant else None

        dept = attrs.get('departement') or (courant.departement if courant else None)

        semaine = attrs.get('semaine') or (courant.semaine if courant else None)
        if semaine is None:
            return attrs
        _refuser_semestre_hors_parite(champ('em'), semaine.type_semestre)

        # Une séance annulée ne dispute sa case à personne.
        annulee = attrs.get('annulee', getattr(courant, 'annulee', False))
        if annulee:
            return attrs

        candidat = {f'{n}_id': champ(n) for n in ('prof', 'em', 'salle')}
        candidat['type_seance_fk_id'] = champ('type_seance_fk')

        autres = (SeanceReelle.objects
                  .filter(semaine=semaine, creneau_fk_id=champ('creneau_fk'),
                          annulee=False)
                  .exclude(departement_id=champ('departement'))
                  .select_related('departement', 'em'))
        if courant:
            autres = autres.exclude(pk=courant.pk)
            # Ses propres sœurs ne sont jamais un conflit : elles SONT le même
            # cours. Sans cette exception, changer la salle d'un cours partagé
            # était refusé — les sœurs portaient encore l'ancienne salle avec le
            # même enseignant, ce que la règle lisait comme une collision. La
            # propagation les alignera juste après.
            if courant.cle_partage:
                autres = autres.exclude(cle_partage=courant.cle_partage)
        autres = list(autres)

        if dept is not None:
            _refuser_chevauchement(dept, [
                (a.departement, a.em.code_em if a.em_id else 'séance')
                for a in autres])

        _refuser_collision(
            [{'prof_id': a.prof_id, 'em_id': a.em_id, 'salle_id': a.salle_id,
              'type_seance_fk_id': a.type_seance_fk_id,
              'nom': a.departement.nom} for a in autres],
            candidat, ou=lambda v: v['nom'] or 'un autre groupe')
        return attrs


class GrilleTypeSerializer(serializers.ModelSerializer):
    departement_nom = serializers.CharField(source='departement.nom', read_only=True)
    nb_seances      = serializers.IntegerField(source='seances.count', read_only=True)
    seances         = SeanceTypeSerializer(many=True, read_only=True)

    class Meta:
        model  = GrilleType
        fields = ['id', 'departement', 'departement_nom', 'type_semestre',
                  'annee_universitaire', 'libelle', 'actif', 'date_creation',
                  'nb_seances', 'seances']
        read_only_fields = ['date_creation']


class GrilleTypeListSerializer(serializers.ModelSerializer):
    """Sans les séances imbriquées — pour les listes."""
    departement_nom = serializers.CharField(source='departement.nom', read_only=True)
    nb_seances      = serializers.IntegerField(source='seances.count', read_only=True)

    class Meta:
        model  = GrilleType
        fields = ['id', 'departement', 'departement_nom', 'type_semestre',
                  'annee_universitaire', 'libelle', 'actif', 'nb_seances']


class DemandeLiberationSerializer(serializers.ModelSerializer):
    demandeur_nom   = serializers.SerializerMethodField()
    decidee_par_nom = serializers.SerializerMethodField()
    salle_nom       = serializers.CharField(source='salle.nom', read_only=True)
    statut_libelle  = serializers.CharField(source='get_statut_display', read_only=True)
    # Ce que le détenteur doit voir pour décider : quel cours, quand, pour qui.
    seance_resume   = serializers.SerializerMethodField()

    def get_demandeur_nom(self, obj):
        return obj.demandeur.name or obj.demandeur.username

    def get_decidee_par_nom(self, obj):
        if not obj.decidee_par_id:
            return None
        return obj.decidee_par.name or obj.decidee_par.username

    def get_seance_resume(self, obj):
        s = obj.seance
        return {
            'id': s.pk,
            'groupe': s.departement.nom,
            'element': s.em.code_em if s.em_id else s.type_seance_fk.type_seance,
            'enseignant': s.prof.nom if s.prof_id else None,
            'date': s.semaine.date, 'jour': s.semaine.jour_fk.jour,
            'creneau': s.creneau_fk.creneau,
        }

    class Meta:
        model  = DemandeLiberation
        fields = ['id', 'seance', 'seance_resume', 'salle', 'salle_nom',
                  'demandeur', 'demandeur_nom', 'motif', 'statut', 'statut_libelle',
                  'decidee_par', 'decidee_par_nom', 'date_decision', 'reponse',
                  'date_demande']
        # Le circuit ne se pilote que par les actions dédiées : exposer ces
        # champs en écriture permettrait de s'accorder soi-même la salle.
        read_only_fields = ['salle', 'demandeur', 'statut', 'decidee_par',
                            'date_decision', 'reponse', 'date_demande']


class EmploiArchiveSerializer(serializers.ModelSerializer):
    """
    L'archive rendue AU FORMAT de l'emploi du temps vivant.

    L'écran d'historique réutilise la grille de consultation telle quelle : une
    archive qui s'afficherait autrement que l'original ne permettrait pas la
    comparaison, qui est sa seule raison d'être.
    """
    jour_fk        = serializers.IntegerField(source='jour_ref', read_only=True)
    creneau_fk     = serializers.IntegerField(source='creneau_ref', read_only=True)
    em             = serializers.IntegerField(source='em_ref', read_only=True)
    type_seance_fk = serializers.IntegerField(source='type_seance_ref', read_only=True)
    prof           = serializers.IntegerField(source='prof_ref', read_only=True)
    salle          = serializers.IntegerField(source='salle_ref', read_only=True)
    type_libelle   = serializers.CharField(source='type_seance_libelle', read_only=True)
    type_special   = serializers.BooleanField(source='type_seance_special', read_only=True)
    date           = serializers.DateField(source='date_seance', read_only=True)

    class Meta:
        model  = EmploiArchive
        fields = ['id', 'departement', 'departement_nom', 'departement_groupe',
                  'numero_semaine', 'date', 'jour_fk', 'jour_libelle',
                  'creneau_fk', 'creneau_libelle',
                  'em', 'em_code', 'em_intitule',
                  'prof', 'prof_nom', 'prof_initial_nom',
                  'salle', 'salle_nom',
                  'type_seance_fk', 'type_libelle', 'type_special',
                  'origine', 'annulee', 'version', 'genere_le']


class VersionArchiveSerializer(serializers.Serializer):
    """Une prise de vue : quelle semaine, quelle version, quand, combien."""
    numero_semaine = serializers.IntegerField()
    version        = serializers.IntegerField()
    genere_le      = serializers.DateTimeField()
    nb_seances     = serializers.IntegerField()
    date_debut     = serializers.DateField(allow_null=True)
    date_fin       = serializers.DateField(allow_null=True)
