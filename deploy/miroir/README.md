# Portail en ligne — le miroir

Une seule base de code, déployée deux fois :

| | Serveur de TRAVAIL (VPS actuel) | MIROIR (second VPS) |
|---|---|---|
| `.env` | `MIRROR_MODE=False` (défaut) | `MIRROR_MODE=True` |
| Qui | le personnel | les étudiants et les enseignants |
| Écritures | toutes | refusées, sauf la boîte de réception (`MIRROR_WRITE_ALLOWLIST`) |
| Publication | part d'ici (admin) | reçue par `recevoir-publication.sh` |

## Ce que la publication transporte — et ce qu'elle ne touche pas

Voir `siga/settings/base.py`, section « Portail en ligne ». Deux niveaux, à ne
jamais confondre :

* **exclusion TOTALE** (`SYNC_EXCLUDE_TABLE`) — ni DROP, ni CREATE, ni données :
  la boîte de réception (réclamations, réclamations de séance, brouillons de
  notes, mots de passe changés en ligne, notifications lues) et les tables
  propres à chaque instance (journal d'audit, tentatives de connexion,
  sessions, publications reçues). Elles SURVIVENT à chaque publication ;
* **exclusion des DONNÉES** (`SYNC_EXCLUDE_TABLE_DATA`) — vidées à chaque
  publication : la liste des jetons (d'où le refus des jetons émis avant la
  dernière publication) et le journal de l'administration Django.

Gardé par `tests/test_miroir_invariant.py` et `tests/test_miroir_boite.py`.

## ORDRE DE DÉPLOIEMENT — à chaque changement de schéma de la boîte de réception

Une table de la boîte de réception n'évolue QUE par les migrations du miroir :
la publication ne transporte pas son schéma.

1. **Le MIROIR d'abord** : code + `migrate`.
2. **Le serveur de travail ensuite** : code + `migrate`.
3. **Publier en dernier.**

Publier avant de migrer le miroir fait échouer la restauration : le receveur
revient à sa sauvegarde — rien n'est perdu, mais rien ne passe.

## Installer le receveur sur le miroir

1. Copier `recevoir-publication.sh` en `/opt/iss/miroir/`, `chmod 750`.
2. Créer un compte dédié (`siga-publication`), membre du groupe `docker`.
3. Dans son `~/.ssh/authorized_keys`, la clé publique du serveur de travail,
   **en commande forcée** :

   ```
   command="/opt/iss/miroir/recevoir-publication.sh",no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding ssh-ed25519 AAAA… publication@travail
   ```

4. Sur le serveur de travail, dans `.env` : `SYNC_SSH_HOST`, `SYNC_SSH_PORT`,
   `SYNC_SSH_USER`, `SYNC_SSH_KEY` (chemin de la clé privée dans le conteneur),
   et `SYNC_PG_DUMP_BIN` si `pg_dump` n'est pas dans le PATH.

`SYNC_SSH_HOST` vide : le bouton construit l'export, n'envoie RIEN, et le dit.

Le receveur : empreinte → sauvegarde → restauration en une transaction
(`ON_ERROR_STOP`) → retour à la sauvegarde en cas d'échec → date la
publication → redémarre le service, dans tous les cas. Les 10 dernières
sauvegardes sont gardées dans `/var/lib/iss-miroir/sauvegardes/`.

## À FAIRE avant le premier envoi réel

* **L'image du backend n'a ni `pg_dump` 18 ni client SSH.** Il faut y ajouter
  `postgresql-client-18` (dépôt PGDG — le client 15 de Debian ne lit pas un
  serveur 18) et `openssh-client`, puis monter la clé privée en lecture seule.
  Tant que ce n'est pas fait, « Publier » sur le VPS échoue proprement (une
  ligne d'historique en échec, rien n'est envoyé).
* **Les fichiers (`media/`) ne passent pas par la publication.** Les
  justificatifs déposés en ligne vivent dans le `media/` du miroir : ne jamais
  y recopier le `media/` du serveur de travail avec `--delete`.
* **Fermer le serveur de travail aux étudiants et aux enseignants**, sinon ils
  continueront à s'y connecter.
