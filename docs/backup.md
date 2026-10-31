# Backup Strategy

## What needs to be backed up

### Critical (must back up — cannot be regenerated)

| Data | Location | Method |
|---|---|---|
| Tang private keys | `/var/lib/tang/` | `backup-server.sh` → Pi/b/backups |
| PostgreSQL databases (always-on and workload instances) | server | `pg_dumpall` of each instance via `backup-server.sh` |
| Authentik state | `/var/lib/authentik/` | `backup-server.sh` |
| Home Assistant config | `/var/lib/hass/` | `backup-server.sh` |
| Caddy root CA key | `secrets/caddy-ca-root-key.age` (agenix) + `secrets/caddy-ca-root.crt` (git) | git + agenix |
| Caddy intermediate/leaf state | `/var/lib/caddy/` | `backup-server.sh` (regenerates from root if lost) |
| agenix secrets | `secrets/*.age` | git repository |
| Frigate config | Rendered from `lanbat.services.frigate.settings` in your `deploy.nix` | Back up `deploy.nix` and the modules it lists (gitignored) |
| Nextcloud config | `/var/lib/nextcloud/` | `backup-server.sh` (back up before any major upgrade — see `docs/runbook.md` § Nextcloud major version upgrade) |
| Vaultwarden data | `/var/lib/vaultwarden/` | `backup-server.sh` (the database through SQLite's online backup) |
| Grafana state | always-on PostgreSQL (`grafana`) | `pg_dump` via `backup-server.sh`; dashboards are provisioned from this repo |
| Invidious state | always-on PostgreSQL (`invidious`) and `/var/lib/invidious/` (hmac key) | `pg_dump` and `backup-server.sh`; losing the hmac key only invalidates sessions |
| Syncthing config + identity | `/var/lib/syncthing/.config/syncthing/` | `backup-server.sh` (without the index, which Syncthing rebuilds) |
| Audiobookshelf accounts + listening progress | `/var/lib/audiobookshelf/config/` | `backup-server.sh` |
| Forgejo repositories + accounts (SQLite) | `/var/lib/forgejo/` | `backup-server.sh` |

### Important (back up — slow to regenerate)

| Data | Location | Method |
|---|---|---|
| Immich originals | `/srv/storage/a/photos/` | Already on Pi LUKS storage |
| Bulk data on the Pi (Immich originals, `users/`, `media/`) | `/srv/storage/{a,b}/` | **No second copy**: each drive is a single LUKS disk, so keep another copy elsewhere (an off-site copy, or the devices that sync it) |
| Immich DB | workload PostgreSQL (`immich`) | `pg_dump` via `backup-server.sh` |
| InfluxDB data (metrics history) | `/var/lib/influxdb2/` | Not backed up: copying the live database isn't safe, and losing it only loses history |
| Kodi library | `/var/lib/kodi/.kodi/` | manual rsync |
| Nextcloud user data | `/srv/storage/b/nextcloud/` | Already on Pi LUKS storage |
| qBittorrent config | `/var/lib/qbittorrent/` | `backup-server.sh` |
| Jackett indexers, credentials and API key | `/var/lib/jackett/` | `backup-server.sh` (it holds private-indexer logins: treat the backup as a secret). qBittorrent's `jackett.json` is rewritten from this key on every start, so it needs no backup of its own |
| Music Assistant state | `/var/lib/music-assistant/` | `backup-server.sh` |

### Deliberately not backed up

| Data | Reason |
|---|---|
| Voiceprints (`/mnt/workload/voice-id/`) | Biometric data kept to a minimum: re-enrolling ("remember me") takes a minute. The directory carries a `CACHEDIR.TAG`, so a backup with `--exclude-caches` skips it too |
| Sightings' pictures (`/mnt/workload/assistant-router/`) | Faces from the robots' and screens' cameras, kept a week at most for the admins' search; nothing needs them back |

### Regenerable (do not need to back up)

| Data | Reason |
|---|---|
| Container images | Re-pull from registry |
| Jellyfin metadata/posters | Re-scan from media files |
| Audiobookshelf book details and covers (`/var/lib/audiobookshelf/metadata/`) | Re-scan and "Match books" |
| Immich thumbnails | Re-generated from originals |
| Transcode cache | Temporary by definition |
| Frigate ML models | Re-download from source |
| SearXNG config | Checked into this repo |
| Wyoming STT/TTS/wake word models | Re-downloaded on first service start |
| Local conversation model (`llama-cpp`) | Pinned by hash in the Nix store; fetched again by a rebuild |

## Backup schedule

### Server → Pi backup (nightly)

`backup-server.sh` writes to `/srv/storage/b/backups/server/` (Pi Drive B) and
keeps 7 daily backups. A nightly timer is defined in `modules/server/backups.nix`
(03:00, after `srv-storage-b.mount` is available). The script skips control- and
workload-layer paths when the matching LUKS layer is locked, so a backup taken
while locked is incomplete but never silently copies empty stubs as if they were
real data.

### Frigate clips → cloud (real-time)

`frigate-rclone-sync.service` (planned for `services/frigate.nix`)
watches the clips directory and uploads each clip as it closes.

This keeps a cloud copy of all detected-event clips.
Full 24h recordings stay on Pi storage only (they are too large for typical cloud plans).

Retention recommendation (indoor cameras, household):
- Local recordings: 7 days (`lanbat.services.frigate.settings.retention.motionDays`)
- Event clips (local): 14 days (`retention.detectionDays` and `retention.alertDays`)
- Snapshots (local): 30 days (`retention.snapshotDays`)
- Event clips (cloud): indefinite until you clean them up

### Pi storage (no backup by default)

The Pi drives are LUKS-encrypted. They are the "primary" bulk storage.
The server backup above puts a copy of the most critical parts (Immich DB, etc.) on Pi/b.

For full off-site backup of media/photos, set up a second rclone job:
```bash
# Example: sync Immich originals to Backblaze B2
rclone sync /srv/storage/a/photos/ b2:your-bucket/photos/ --fast-list
```

Add this as a weekly systemd timer.

## Restore procedure

### Worst case: server disk failure

1. Install new disk, boot NixOS installer.
2. Re-run the disk setup from `deployment-checklist.md`.
3. After install, restore from Pi backup:
   ```bash
   rsync -a /srv/storage/b/backups/server/LATEST/ /restore/
   # Restore PostgreSQL:
   sudo -u postgres psql < /restore/postgres/pg_dumpall.sql
   # Restore service state:
   rsync -a /restore/hass/ /var/lib/hass/
   rsync -a /restore/tang/ /var/lib/tang/
   # etc.
   ```
4. The Pi drives are intact and still encrypted to the same Tang key (which you restored).
5. Deploy from the workstation: `deploy --skip-checks path:.#homelab-server`

### Tang key loss

If Tang keys are lost and Clevis binding is broken:
1. Use the LUKS fallback passphrase to open the drives manually.
2. Re-install Tang (new keys auto-generated).
3. Re-bind the Pi drives: `clevis luks bind -d /dev/... tang '{"url":"http://server:7500"}'`
4. Test unlock.

This is why backing up `/var/lib/tang/` is **critical**.

### Pi drive failure

If a Pi drive dies:
- All data on that drive is lost (no RAID).
- Media can be re-added from original sources.
- Immich originals can be restored from cloud backup (if set up).
- Surveillance recordings are gone (acceptable for a homelab).
- Replace drive, re-format with LUKS + XFS, re-bind to Tang.

This is an intentional simplicity tradeoff. RAID adds complexity; two separate drives
with clear ownership is simpler to operate and understand.
