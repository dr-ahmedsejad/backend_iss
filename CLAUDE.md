# SIGA — Backend (Système Intégré de Gestion Académique)

API REST Django 4.2 + DRF consommée par le frontend Next.js `c:/SIR/ISS_SIGA/frontend_iss` (port 3001).
Toutes les routes sont préfixées `/api/v1/`. Auth par cookies httpOnly + JWT (SimpleJWT).

## Stack

- **Framework** : Django 4.2.16, DRF, `drf-spectacular` (OpenAPI), `django-filters`
- **DB** : **PostgreSQL, base `iss`** (`DB_ENGINE=postgresql`, `DB_NAME=iss` dans `.env`).
  Rebasculable sur MySQL via `DB_ENGINE=mysql` — aucune variable n'est *requise* pour un clone frais
  (le défaut du code est `siga_pg`, donc `DB_NAME=iss` doit rester présent dans `.env`).

  ⚠️ **Autres bases sur le même serveur PG — ne pas s'y connecter par erreur** :
  | Base | État |
  |---|---|
  | **`iss`** | **BD de travail** — 93 tables, 194 migrations, 87 users |
  | `siga_pg` | ancienne copie de `iss` (schéma et volume identiques) — figée, ne pas y écrire |
  | `siga_prive` | **autre branche de code** : 103 tables, 211 migrations, apps `cours` + `core.SyncLog` qui n'existent pas ici. Incompatible avec ce dépôt. |
- **Settings** : `siga/settings/{base,development,production,docker,test,test_pg,pg_load}.py`
  Dev = `siga.settings.development` (DEBUG, SQL loggé en console, BrowsableAPIRenderer).
- **Auth** : `core.authentication.CookieJWTAuthentication` — access 60 min / refresh 7 j,
  rotation + blacklist. Cookies `access_token` / `refresh_token`, httpOnly, SameSite=Lax.
- **Sécurité** : `django-axes`, seuls les ÉCHECS comptent (`apps/authentication/tentatives.py`) :
  5 échecs pour un compte depuis une adresse → ce couple bloqué 15 min ; `LOGIN_ECHECS_PAR_IP`
  (20) échecs depuis une adresse, tous comptes confondus → l'adresse bloquée ; 429 JSON avec le
  temps restant. Adresse réelle = `X-Real-IP` posé par nginx (`core/ip_client.py`).
  Throttles par vue (`login` 5/15min sur refresh et premier accès, `sensitive_endpoint` 5/h…).
- **Cache** : Redis si `REDIS_URL`, sinon LocMemCache (non partagé entre process — attention en prod).
- **User model** : `authentication.CustomUser` (`AUTH_USER_MODEL`).

## Règles non-négociables

### 1. Phase 5 — FK uniquement, plus de CharField legacy
Sur `Suivie` / `SuiviePointage` / `Emplois` / `EmploisArchive` :
- ❌ `id_prof`, `id_em`, `id_salle`, `id_semestre`, `id_departement` (colonnes supprimées)
- ❌ `jour`, `creneau`, `type_seance` en CharField (supprimés)
- ✅ `prof`, `em`, `salle`, `semestre` (FK), `creneau_fk`, `jour_fk`, `type_seance_fk`
- ✅ multi-département : `SuiviePointage.departements` (M2M, table `suivi_pointage_departements`).
  `Suivie` garde un `departement` FK **singulier**.

Avant de toucher un endpoint EDT/suivi/vacation :
```bash
grep -rE "[a-z]+\.(creneau|type_seance|jour)([[:space:],\)\.]|$)" apps
```
Note : sur `Vacation`, `v.type.type_seance` reste valide (FK vers `parametres.Seance`).

Les commandes de migration Phase 5 sont conservées dans `apps/emplois/management/commands/`
(`audit_legacy_charfields`, `backfill_legacy_fks`, `drop_legacy_charfields`…) — **ne pas les relancer**,
elles sont idempotentes mais historiques.

### 2. Multi-institution scoping
`parametres.Institution` + `annee_universitaire` sont les axes de cloisonnement.
Tout queryset sur emplois / suivi / vacations / documents / inscriptions **doit** filtrer sur au moins
l'un des deux — sinon chevauchement multi-institution silencieux (bug déjà rencontré).

### 3. RBAC : 11 rôles, pas de hardcode dispersé
`ROLE_CHOICES` dans `apps/authentication/models.py` :
`admin`, `DG`, `DA`, `DE`, `AA`, `IT`, `scolarite`, `responsable_filiere`, `jury_president`,
`etudiant`, `enseignant`.
- Les permissions vivent dans `core/permissions.py` + la matrice `ModuleAction` / `RoleDefault`.
- Délégation EDT hors rôle : `CustomUser.managed_departements` (M2M). Admin/superuser passent outre.
- Un nouveau contrôle d'accès = une classe dans `core/permissions.py`, pas un `if user.role ==` inline.

### 4. Réponses d'erreur normalisées
`core.exceptions.custom_exception_handler` est l'`EXCEPTION_HANDLER` global. Le frontend s'appuie sur
sa forme — ne pas renvoyer de dict d'erreur ad hoc depuis une vue.

### 5. Pagination et filtres
`core.pagination.StandardPagination`, `PAGE_SIZE = 10`. Les listes exposées au frontend gardent
`DjangoFilterBackend` + `SearchFilter` + `OrderingFilter` (le frontend envoie `?search=`, `?ordering=`).

### 6. Uploads bornés
`DATA_UPLOAD_MAX_MEMORY_SIZE` / `FILE_UPLOAD_MAX_MEMORY_SIZE` = 5 Mo, `DATA_UPLOAD_MAX_NUMBER_FIELDS` = 2000.
Les imports xlsx volumineux doivent borner taille **et** nombre de lignes dans leur propre vue.
Validators par champ dans `core/validators.py`.

## Structure

```
manage.py
siga/
  settings/base.py       → tout le socle (DB, DRF, JWT, CORS, axes, backups, PDF signing)
  settings/development.py→ DEBUG + SQL console + BrowsableAPI
  settings/test.py       → sqlite :memory:, MD5 hasher, axes off, SEEDS_DESACTIVES_POUR_TESTS
  urls.py                → mapping /api/v1/<domaine>/ → apps.<domaine>.urls
core/                    → transverse, à privilégier avant de dupliquer
  authentication.py      → CookieJWTAuthentication (NE PAS toucher sans test auth de bout en bout)
  permissions.py         → classes RBAC
  middleware.py          → AuditMiddleware (après auth + axes)
  cache_middleware.py    → Cache-Control sur les lookups
  pagination.py exceptions.py throttles.py validators.py mixins.py
  media_auth.py          → /internal/media-auth/ pour Nginx auth_request sur /media/
  pdf_renderer.py pdf_utils.py image_utils.py arabe.py
  audit_context.py audit_helpers.py signals.py
apps/
  authentication absence avancement banque departement em emplois parametres prof salle
  suivi vacation                              → socle EDT / charges / pointage
  modules scolarite inscriptions evaluations stages documents notifications → scolarité LMD
  portail reclamations                        → portail étudiant
  audit                                       → journal lecture seule
  backup                                      → sauvegardes BD (matrice grants + download)
tests/                   → pytest (54 fichiers test_*.py)
templates/               → 28 gabarits HTML des PDF officiels — livrables de PRODUCTION,
                           rendus par 15 modules Python. Pas de la documentation.
docs/                    → HORS GIT depuis le 17/08/2026 (`.gitignore`). Rangé en `actifs/`
                           (5 docs valables), `archives/<domaine>/` (13) et `donnees/` (3).
                           Index dans `docs/README.md`. Ce CLAUDE.md est le seul document versionné.
```

## Points sensibles

- **Zones à ne pas modifier sans scénario de test explicite** : `core/authentication.py`,
  `core/permissions.py`, `apps/evaluations/services/calcul_notes.py`,
  `apps/evaluations/services/deliberation_annuelle.py`, `apps/avancement/`.
  Ce sont les calculs de notes / NFE / délibérations et le RBAC — une régression y est silencieuse.
- **God-modules** (ne pas refactorer pour le plaisir, mais un nouveau `@action` mérite son module) :
  `apps/suivi/views.py` 2044, `apps/vacation/views.py` 1987, `apps/avancement/views.py` 1922,
  `apps/documents/services.py` 1810, `apps/absence/views.py` 1190, `apps/inscriptions/views.py` 1184,
  `apps/portail/views.py` 1017. `apps/evaluations` est le bon exemple : découpé en
  `views_notes / views_sessions / views_deliberation / views_reports` + `services/`.
- **Signature PDF (PAdES)** : `PDF_SIGNING_ENABLED` + certificat PKCS#12 dans `secrets/doc_signing.p12`
  (hors git). **Non bloquant** : certificat absent → PDF non signé, la génération ne doit jamais échouer.
- **QR de vérification** : `DOCUMENTS_BASE_URL` (défaut `https://ent.iss-gp.mr`) est le domaine **fixe**
  encodé dans les QR des documents officiels. Indépendant de `DOMAIN`/CORS. Doit rester absolu avec hôte.
- **Audit** : rétention 90 j HOT + 365 j ARCHIVE (`AUDIT_RETENTION`), export `jsonl.gz` via
  `archive_audit_logs` / `purge_audit_logs` / `restore_audit_logs` (cron).
- **Backups** : `BACKUP_BASE_DIR` = `local_backups/` en dev Windows, `/home/backups` en prod Linux.
  Le générateur choisit l'outil au **runtime** via `connection.vendor` ([generator.py:132](apps/backup/services/generator.py#L132)) :
  - sur PG → seul `BACKUP_PGDUMP_BIN` compte. `pg_dump` n'étant **pas dans le PATH** sous Windows,
    le chemin absolu est obligatoire dans `.env` (`C:/Program Files/PostgreSQL/18/bin/pg_dump.exe`,
    version alignée sur le serveur 18.4). Le mot de passe passe par `PGPASSWORD` dans l'env du
    subprocess — jamais en argv.
  - `BACKUP_DB_CONFIG_PATH` (db.cnf) et `BACKUP_MYSQLDUMP_BIN` ne sont lus **que** si `DB_ENGINE=mysql`.
  - `BACKUP_OPENSSL_BIN` est un **setting mort** : déclaré dans `base.py`, lu par aucun code Python
    (`deploy/backup-scripts/decrypt-backup.py` a sa propre liste de candidats).
- **`payement` (typo)** : le frontend expose 6 routes avec cette orthographe. Ne pas l'aggraver côté API ;
  renommage en `paiement` dans un sprint dédié (coordonné frontend + backend).
- **CORS** : `CORS_ALLOWED_ORIGINS` défaut `http://localhost:3001,http://localhost:3002`,
  `CORS_ALLOW_CREDENTIALS = True` (obligatoire pour les cookies JWT).

## Workflow

```bash
# Dev server → http://127.0.0.1:8000
.venv/Scripts/python.exe manage.py runserver

# Migrations
.venv/Scripts/python.exe manage.py makemigrations
.venv/Scripts/python.exe manage.py migrate

# Tests (sqlite :memory:, sans migrations — LA validation). Deps : requirements-dev.txt
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m pytest -m "not slow"        # markers : slow, integration, regression, unit
.venv/Scripts/python.exe -m pytest --migrations         # vérifie le schéma vendor-neutre

# Docs OpenAPI (DEBUG uniquement)
#   /api/docs/  (Swagger)   /api/redoc/   /api/schema/

# Frontend dans un autre terminal
cd c:/SIR/ISS_SIGA/frontend_iss && npm run dev
```

Les tests tournent sur **sqlite en mémoire** : ils ne touchent jamais `iss`. En contrepartie,
tout SQL brut spécifique à un vendor doit avoir un test sous `--migrations` ou `settings.test_pg`.

**Baseline au 2026-10-08 : `992 passed, 2 failed, 1 xfailed` en ~45 s.** Toute exécution qui dépasse
2 échecs = régression introduite par ton edit. Les 2 échecs connus (antérieurs, pas des régressions) :
| Test | Symptôme |
|---|---|
| `apps/documents/test_verification_publique.py::test_aucune_donnee_technique_exposee` | le champ `semestre` fuit dans la réponse publique `/verifier/` |
| `tests/test_case_collision_scan.py::test_collision_composite_em` | `assert None is not None` — scan de collision de casse |

`BlocageQuatriemeElementTest` (3 tests) passe depuis le 08/10/2026 : il échouait sur
« filiere : Ce champ est obligatoire » — la filière d'un EM est désormais déduite du module LMD
(`EMSerializer.to_internal_value`).

Le `xfail` est documenté : seuil DNI codé en dur à 65 % (`test_calcul_notes.py`), à paramétrer par
`type_diplome`.

## Conventions de commit

Format historique : `<jj-mm-aaaa>.<n>` (ex. `02-05-2026.3`).
Pour un changement significatif, préférer `feat(domaine): ...` / `fix(domaine): ...`.

## Pour les agents IA

- **Working directories multiples** : backend (root) + `c:/SIR/ISS_SIGA/frontend_iss` (Next.js,
  a son propre `CLAUDE.md`). Une modif d'API impacte presque toujours `lib/api/<domaine>.ts` côté frontend.
- **Toujours utiliser `.venv/Scripts/python.exe`**, jamais `python` global.
- **Avant d'ajouter un champ/endpoint** : vérifier si `core/` fournit déjà le mixin, le validator
  ou la permission — 23 apps, la duplication est le risque principal.
- **Après une modif de modèle** : `makemigrations` + `pytest` + vérifier qu'aucun serializer frontend
  ne référence un champ renommé.
- **MEMORY.md** dans `~/.claude/projects/.../memory/` contient l'historique des décisions
  (Phase 5, isolation institution, MySQL `gesafped26` → PostgreSQL `iss`).
  Le lire avant un changement structurel.
