#!/usr/bin/env bash
# backup-server.sh
#
# Backup critical server-local state to Pi storage (Drive B, /backups).
#
# Backs up:
#   - PostgreSQL, both instances (pg_dumpall + per-database dumps of the
#     databases the profile declares on each, BACKUP_DATABASES_* from the unit);
#     the workload instance only while unlocked
#   - /var/lib/hass  (Home Assistant)
#   - /var/lib/caddy (Caddy config + CA keys — required)
#   - /var/lib/tang  (Tang private keys — required; control layer only)
#   - /var/lib/authentik
#   - /var/lib/invidious (hmac key; its database comes from the PostgreSQL dump)
#   - /var/lib/nextcloud
#   - /var/lib/immich/profile
#   - /var/lib/music-assistant
#   - /var/lib/qbittorrent
#   - /var/lib/jackett (indexers with their credentials, and the API key)
#   - /var/lib/bitmagnet
#   - /var/lib/audiobookshelf/config (accounts and listening progress)
#   - /var/lib/vaultwarden (the database through SQLite's online backup)
#   - /var/lib/forgejo (repositories and config, and the database through
#     SQLite's online backup)
#   - Syncthing's config and identity (/var/lib/syncthing/.config/syncthing,
#     without the index, which Syncthing rebuilds)
# A directory the host does not have is skipped, except a required one: its
# absence is reported and fails the run once everything else is copied.
#
# NOT backed up by this script:
#   - /srv/storage/a (Pi storage — backs up in its own right)
#   - /srv/storage/b (Pi storage)
#   - /var/cache     (regenerable)
#   - Container images (re-pull from registry)
#
# The backup destination is /srv/storage/b/backups/server on the server,
# which maps to /mnt/storage-b/backups/server on the Pi.
#
# Encrypt the backup archive if the destination is untrusted.
# For this homelab, the Pi storage is LUKS-encrypted so the backup
# is protected at rest without additional encryption.
#
# Run it as root. A nightly timer is defined in modules/server/backups.nix.

set -euo pipefail

BACKUP_DIR=/srv/storage/b/backups/server
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DEST=$BACKUP_DIR/$TIMESTAMP

control_online() { systemctl is-active --quiet control-online.target; }
workload_online() { systemctl is-active --quiet workload-online.target; }

echo "[$TIMESTAMP] Starting server backup to $DEST..."

if [ ! -d "$BACKUP_DIR" ]; then
  echo "ERROR: backup destination $BACKUP_DIR not available (Pi NFS down?)." >&2
  exit 1
fi

install -d -m 0700 "$DEST"

# copy_state <dir> <name> [rsync options...]: mirror a service's state into
# $DEST/<name>. A directory this host does not have is skipped, not fatal: a
# profile need not run every service, and one failed copy must not cost the
# rest of the backup.
copy_state() {
  local src=$1 name=$2
  shift 2
  if [ -d "$src" ]; then
    rsync -a --delete "$@" "$src/" "$DEST/$name/"
  else
    echo "  Skipping $src: not on this host."
  fi
}

# copy_required_state <dir> <name> [rsync options...]: as copy_state, for state
# whose loss is not recoverable. A missing one is reported and recorded, and the
# run fails at the end, after the rest has been copied.
missing_required=()
copy_required_state() {
  local src=$1
  if [ -d "$src" ]; then
    copy_state "$@"
  else
    echo "  ERROR: $src is missing; it should be backed up."
    missing_required+=("$src")
  fi
}

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
# dump_instance <label> <connection args> <databases...>
dump_instance() {
  local label=$1 conn=$2
  shift 2
  echo "  Dumping PostgreSQL ($label)..."
  install -d "$DEST/postgres-$label"
  # shellcheck disable=SC2086
  runuser -u postgres -- pg_dumpall $conn --clean --if-exists | \
    gzip > "$DEST/postgres-$label/pg_dumpall.sql.gz"
  for db in "$@"; do
    # shellcheck disable=SC2086
    runuser -u postgres -- pg_dump $conn --clean --if-exists "$db" | \
      gzip > "$DEST/postgres-$label/${db}.sql.gz"
  done
}

# The databases come from the unit (lanbat.postgresql.databases in the profile).
# shellcheck disable=SC2086
dump_instance always-on "-h /run/postgresql-always-on -p 5433" ${BACKUP_DATABASES_ALWAYS_ON:-}

if workload_online; then
  # shellcheck disable=SC2086
  dump_instance workload "-h /run/postgresql -p 5432" ${BACKUP_DATABASES_WORKLOAD:-}
else
  echo "  Skipping the PostgreSQL workload instance: the workload layer is locked."
fi

# ---------------------------------------------------------------------------
# Service state
# ---------------------------------------------------------------------------
echo "  Backing up always-on service state..."
# Frigate's config is rendered from the profile (lanbat.services.frigate.settings).
copy_state /var/lib/hass            hass
copy_required_state /var/lib/caddy  caddy
copy_state /var/lib/authentik       authentik
copy_state /var/lib/invidious       invidious
copy_state /var/lib/music-assistant music-assistant

if control_online; then
  echo "  Backing up control-layer state..."
  copy_required_state /var/lib/tang tang
else
  echo "  Skipping /var/lib/tang: the control layer is locked."
fi

if workload_online; then
  echo "  Backing up workload-layer state..."
  copy_state /var/lib/nextcloud             nextcloud
  copy_state /var/lib/qbittorrent           qbittorrent
  copy_state /var/lib/jackett               jackett
  copy_state /var/lib/bitmagnet             bitmagnet
  copy_state /var/lib/immich/profile        immich-profile
  copy_state /var/lib/audiobookshelf/config audiobookshelf-config

  # The vault: attachments, sends and keys by rsync, and the live database
  # through SQLite's online backup, so a write during the copy can't tear it.
  if [ -d /var/lib/vaultwarden ]; then
    copy_state /var/lib/vaultwarden vaultwarden --exclude 'db.sqlite3*' --exclude 'icon_cache/'
    sqlite3 /var/lib/vaultwarden/db.sqlite3 ".backup '$DEST/vaultwarden/db.sqlite3'"
  fi

  # Git repositories and config by rsync, and the live database through
  # SQLite's online backup, so a push during the copy can't tear it.
  if [ -d /var/lib/forgejo ]; then
    install -d "$DEST/forgejo/data"
    copy_state /var/lib/forgejo forgejo --exclude 'data/forgejo.db*' --exclude 'log/'
    sqlite3 /var/lib/forgejo/data/forgejo.db ".backup '$DEST/forgejo/data/forgejo.db'"
  fi

  # Syncthing's device identity (cert.pem, key.pem) and folder config; the
  # index is rebuilt by rescanning.
  copy_state /var/lib/syncthing/.config/syncthing syncthing --exclude 'index-*'
else
  echo "  Skipping workload service state: the workload layer is locked."
fi

# Immich thumbs/encoded-video can be regenerated — skip them to save space.

# ---------------------------------------------------------------------------
# Rotate old backups — keep last 7 daily backups.
# ---------------------------------------------------------------------------
echo "  Rotating old backups (keeping 7)..."
ls -1d "$BACKUP_DIR"/[0-9]* 2>/dev/null | sort | head -n -7 | while read old; do
  echo "  Removing old backup: $old"
  rm -rf "$old"
done

if [ ${#missing_required[@]} -gt 0 ]; then
  echo "[$TIMESTAMP] Backup INCOMPLETE: missing ${missing_required[*]}" >&2
  exit 1
fi

echo "[$TIMESTAMP] Backup complete: $DEST"
