"""
Planification hebdomadaire de l'emploi du temps.

Ce module s'installe **À CÔTÉ** de `apps.emplois`, jamais à sa place. Les cinq
écrans historiques — gérer, importer, filière, salle, professeur — restent en
service, et leurs entrées de menu aussi. Le retrait de l'ancien système fera
l'objet d'une décision explicite, plus tard.

`apps/suivi/` et `apps/vacation/` ne sont pas modifiés : on compose autour.
Voir `generation.py` pour la technique, et `siga/urls.py` pour la capture d'URL
qui la rend possible.

RETOUR ARRIÈRE — la procédure complète est dans
`backend_iss/docs/RETOUR_ARRIERE_EDT.md` (le dossier `docs/` est hors dépôt,
voir `.gitignore`). En résumé, trois gestes indépendants et réversibles :

  1. retirer le groupe de menu `key: 'edt'` de `frontend_iss/lib/nav-config.ts`
     → le moteur devient inaccessible, les données restent ;
  2. retirer de `siga/urls.py` la capture de `suivi/suivies/ajouter/` et le
     `path('api/v1/edt/', ...)` → la génération redevient celle du socle ;
  3. `manage.py migrate edt zero` → les cinq tables `edt_*` disparaissent.

Aucune migration de ce module ne touche une table du socle : `0001_initial` ne
contient que des `CREATE TABLE`. Aucune clé étrangère ne pointe *vers* ces
tables depuis l'extérieur.
"""
