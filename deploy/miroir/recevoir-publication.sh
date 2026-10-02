#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Reçoit une publication du serveur de TRAVAIL, sur le MIROIR.
#
# C'est la COMMANDE FORCÉE de la clé de publication (authorized_keys du compte
# dédié sur le VPS miroir) — la clé ne peut rien faire d'autre :
#
#   command="/opt/iss/miroir/recevoir-publication.sh",no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding ssh-ed25519 AAAA… publication@travail
#
# Le dump arrive sur l'entrée standard ; l'empreinte attendue, en argument de
# la commande d'origine (« publier <sha256> », lue dans SSH_ORIGINAL_COMMAND).
#
# L'ORDRE N'EST PAS NÉGOCIABLE :
#   1. vérifier l'empreinte — AVANT de toucher à quoi que ce soit ;
#   2. SAUVEGARDER la base du miroir ;
#   3. restaurer, en UNE transaction, avec ON_ERROR_STOP ;
#   4. en cas d'échec, revenir à la sauvegarde ;
#   5. dater la publication reçue (publication_recue) — elle fait refuser les
#      jetons émis avant, la publication ayant vidé la liste des révoqués ;
#   6. redémarrer le service, DANS TOUS LES CAS.
#
# Première ligne de la sortie : « OK … » ou « ECHEC … » — le serveur de
# travail la journalise telle quelle.
#
# Les commandes sont réglables par l'environnement (MIROIR_*) : c'est ce qui
# permet d'éprouver ce script sur une base JETABLE, sans VPS.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

COMPOSE="${MIROIR_COMPOSE:-docker compose -f /opt/iss/backend_iss/deploy/docker-compose.yml}"
PSQL="${MIROIR_PSQL:-$COMPOSE exec -T db psql -U siga -d gesafped26}"
PG_DUMP="${MIROIR_PG_DUMP:-$COMPOSE exec -T db pg_dump -U siga -d gesafped26}"
PG_RESTORE="${MIROIR_PG_RESTORE:-$COMPOSE exec -T db pg_restore -U siga -d gesafped26}"
REDEMARRER="${MIROIR_REDEMARRER:-$COMPOSE restart backend}"
DOSSIER="${MIROIR_DOSSIER:-/var/lib/iss-miroir}"
GARDER="${MIROIR_GARDER_SAUVEGARDES:-10}"

STAMP="$(date +%Y%m%d-%H%M%S)"
TRAVAIL="$(mktemp -d)"
SAUVEGARDE="$DOSSIER/sauvegardes/avant-publication-$STAMP.dump"

fin() {
  # 6. Redémarrer dans tous les cas — même si rien n'a été touché.
  $REDEMARRER >/dev/null 2>&1 || echo "AVERTISSEMENT redemarrage du service en echec" >&2
  rm -rf "$TRAVAIL"
}
trap fin EXIT

echec() { echo "ECHEC $1"; exit "${2:-1}"; }

# ── L'empreinte attendue ──────────────────────────────────────────────────────
ATTENDUE="${1:-}"
if [ -z "$ATTENDUE" ]; then
  # shellcheck disable=SC2086
  set -- ${SSH_ORIGINAL_COMMAND:-}
  [ "${1:-}" = "publier" ] && ATTENDUE="${2:-}"
fi
[[ "$ATTENDUE" =~ ^[0-9a-f]{64}$ ]] || echec "empreinte absente ou illisible : rien n'a ete touche" 2

# ── 1. Recevoir, puis vérifier l'empreinte AVANT tout ─────────────────────────
cat > "$TRAVAIL/publication.sql"
RECUE="$(sha256sum "$TRAVAIL/publication.sql" | cut -d' ' -f1)"
[ "$RECUE" = "$ATTENDUE" ] || echec "empreinte differente (recue $RECUE) : rien n'a ete touche" 3

# ── 2. Sauvegarder ────────────────────────────────────────────────────────────
mkdir -p "$DOSSIER/sauvegardes"
$PG_DUMP -Fc > "$SAUVEGARDE" 2> "$TRAVAIL/sauvegarde.err" && [ -s "$SAUVEGARDE" ] \
  || echec "sauvegarde impossible ($(tail -n1 "$TRAVAIL/sauvegarde.err")) : rien n'a ete touche" 4

# ── 3. Restaurer, en une transaction ──────────────────────────────────────────
if ! $PSQL -v ON_ERROR_STOP=1 --single-transaction -q -o /dev/null \
      < "$TRAVAIL/publication.sql" 2> "$TRAVAIL/restauration.err"; then
  # ── 4. Revenir à la sauvegarde ──────────────────────────────────────────────
  # La transaction unique a déjà tout annulé ; on réapplique quand même la
  # sauvegarde, au cas où une étape aurait échappé à la transaction.
  if $PG_RESTORE --clean --if-exists --single-transaction < "$SAUVEGARDE" \
        > /dev/null 2> "$TRAVAIL/retour.err"; then
    RETOUR="sauvegarde reappliquee"
  else
    RETOUR="RETOUR A LA SAUVEGARDE EN ECHEC — intervenir : $SAUVEGARDE"
  fi
  echec "restauration : $(grep -m1 -iE 'erreur|error' "$TRAVAIL/restauration.err") — $RETOUR" 5
fi

# ── 5. Dater la publication reçue ─────────────────────────────────────────────
MARQUE="publication datee"
$PSQL -v ON_ERROR_STOP=1 -q -o /dev/null \
  -c "INSERT INTO publication_recue (recue_le, sha256) VALUES (now(), '$ATTENDUE')" \
  2>/dev/null || MARQUE="AVERTISSEMENT publication non datee : les anciens jetons restent valables"

# Garder les N dernières sauvegardes.
ls -1t "$DOSSIER"/sauvegardes/avant-publication-*.dump 2>/dev/null \
  | tail -n +"$((GARDER + 1))" | xargs -r rm -f

echo "OK publication appliquee $STAMP (sauvegarde $SAUVEGARDE ; $MARQUE)"
exit 0
