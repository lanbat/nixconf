# Operational Runbook

Procedures for the encrypted layers and for recovery: unlocking after a reboot, locking
for maintenance, Tang and Clevis, LUKS headers, backups and restores. Day-to-day work
(deploying, updating, health checks, per-service tasks) is in the
[operations guide](operations.md); what happens when a host or the Pi goes away is in
[failure modes](failure-modes.md). The design behind the layers is in
[secure layers](secure-layers.md).

---

## Server: After a reboot

Services are split into two tiers with different startup behaviour:

**Always-on (start automatically at boot — no action needed)**:
Caddy, PostgreSQL (always-on instance), Redis, Authentik, Home Assistant, Zigbee2MQTT, Grafana, InfluxDB,
Mosquitto, Frigate, Snapcast, Wyoming pipeline, SearXNG, Redlib, Invidious, Telegraf, Homepage.

**Workload-gated (locked until you run `unlock-workload`)**:
Nextcloud, Immich, Jellyfin, Vaultwarden, Syncthing, Samba, qBittorrent, Bitmagnet,
and the PostgreSQL workload instance that holds their databases.

After a reboot: SSH works immediately, always-on services are running, both
LUKS layers are locked. Unlock in order:

### Step 1 — Unlock control (Tang)

```bash
ssh admin@server
sudo unlock-control
```

This opens `/dev/lanbat/control`, mounts `/mnt/control`, and starts Tang.
After this step: Tang is serving keys on port 7500. The Raspberry Pi will
auto-unlock its NVMe drives on the next retry (up to 5 minutes).

Verify:
```bash
curl http://127.0.0.1:7500/adv | jq -r '.keys[].alg'
# Should print key algorithm names (e.g. "ECMR")
```

### Step 2 — Unlock workload (gated services)

```bash
sudo unlock-workload
```

This opens `/dev/lanbat/workload`, mounts `/mnt/workload`, activates all bind mounts,
and starts `workload-online.target` — bringing up Nextcloud, Immich, Jellyfin,
Vaultwarden, Syncthing, Samba, qBittorrent and Bitmagnet.

Verify:
```bash
sudo server-health
systemctl status phpfpm-nextcloud podman-immich-server jellyfin
```

### Combined (if you want both at once)

```bash
sudo unlock-all
```

---

## Server: Lock workload (maintenance)

Stop all services and lock the workload layer. Tang stays available (Pi drives
remain unlocked if already open).

```bash
sudo lock-workload
```

---

## Server: Lock control (full shutdown)

Lock Tang. Do this **after** lock-workload.
After this step, the Pi cannot auto-unlock its drives on the next reboot.

```bash
sudo lock-control   # prompts for confirmation
```

---

## Server: Lock everything

```bash
sudo lock-all       # lock-workload first, then lock-control
```

---

## Server: Health check

```bash
sudo server-health
```

Shows LUKS mapper state, mount status, Tang health, disk usage of the host root, free
space in the LVM volume group, and key service states. To grow a full volume, see
`docs/operations.md` § Disk space.

---

## Nextcloud major version upgrade

Nextcloud is pinned in `services/nextcloud.nix` (`services.nextcloud.package`).
**Do not skip majors** — upgrade one major at a time, deploy, verify, then repeat.
The pin stops nixpkgs from jumping two majors in one deploy; it does not run the
upgrade for you.

### Before you change the pin

On the server, with the workload layer unlocked:

```bash
sudo unlock-workload   # if not already unlocked
sudo -u nextcloud nextcloud-occ status
```

Note the `version:` line. It must match the major you are upgrading **from**
(e.g. `32.x.x` before changing the pin to `pkgs.nextcloud33`). If the running
version is unknown or behind the pin, stop and reconcile before deploying a newer
major.

### Back up first

See `docs/backup.md`. At minimum before a major upgrade:

| What | Location |
|---|---|
| Nextcloud config + app state | `/var/lib/nextcloud/` (workload layer) |
| PostgreSQL workload databases | `pg_dumpall` on the workload instance (port 5432, socket `/run/postgresql`) |
| Bulk user data (External Storage) | `/srv/storage/b/nextcloud/` (Pi LUKS — usually already durable) |

Run a manual workload backup (`docs/runbook.md` § Running backups manually) or
ensure the nightly `backup-server` timer has a recent snapshot. If the upgrade
fails, restore `/var/lib/nextcloud/` and the workload PostgreSQL dump from
`docs/backup.md` § Restore procedure before retrying.

### Prerequisites (workload gate)

Nextcloud and its database live on the **workload** tier. These must be up:

```bash
sudo unlock-workload
systemctl is-active postgresql phpfpm-nextcloud nextcloud-setup
```

Pi NFS (`srv-storage-b.mount`) is optional for the upgrade itself; external
storage folders show errors while the Pi is down but the core app migrates
without them.

### Upgrade one major

1. Edit `services/nextcloud.nix`: bump `package = pkgs.nextcloudNN` by one major
   (currently `pkgs.nextcloud32`; next step is `pkgs.nextcloud33`).
2. From the workstation: `deploy --skip-checks path:.#homelab-server`.
3. On the server, watch migration units:
   ```bash
   journalctl -u nextcloud-setup -u nextcloud-update-db -f
   ```
4. Verify:
   ```bash
   sudo -u nextcloud nextcloud-occ status
   sudo -u nextcloud nextcloud-occ app:list
   ```
   Open `https://cloud.<domain>`, log in (Authentik OIDC or break-glass admin),
   and spot-check file sync.

### After upgrading

- Re-run `nix flake check` — the nixpkgs evaluation warning for Nextcloud clears
  only when the pin reaches the latest packaged major (currently 33).
- Third-party and community apps often lag a major release; Nextcloud may
  **auto-disable** incompatible apps during migration. Re-enable or update them
  in the Apps UI or with `nextcloud-occ app:update --all`.
- `user_oidc` is configured by `nextcloud-oidc-setup`; confirm OIDC login still
  works after the upgrade.

---

## Verify Tang health

```bash
# Local health check:
curl -s http://127.0.0.1:7500/adv | jq -r '.keys[].alg'

# From the Raspberry Pi (verify network reachability):
curl -s http://SERVER_IP:7500/adv | jq -r '.keys[].alg'

# Check Tang socket state:
systemctl status tangd.socket
```

---

## Raspberry Pi: NVMe unlock status

### Check current state

```bash
ssh admin@pi
lsblk                                          # see mapped devices
systemctl status storage-a-unlock storage-b-unlock
df -h /mnt/storage-a /mnt/storage-b
```

### Trigger unlock immediately (without waiting 5 minutes)

If Tang just became available and you don't want to wait for the auto-retry:

```bash
# On the Pi:
sudo systemctl start storage-a-unlock.service
sudo systemctl start storage-b-unlock.service
```

Or restart failed units to reset the restart counter:
```bash
sudo systemctl restart storage-a-unlock.service
sudo systemctl restart storage-b-unlock.service
```

### Manual unlock (if Clevis bind is lost or Tang is permanently unavailable)

Use the LUKS passphrase directly:
```bash
# On the Pi:
sudo cryptsetup luksOpen /dev/disk/by-id/<drive-a-id> storage-a
sudo mount /dev/mapper/storage-a /mnt/storage-a

sudo cryptsetup luksOpen /dev/disk/by-id/<drive-b-id> storage-b
sudo mount /dev/mapper/storage-b /mnt/storage-b
```

### Manually lock Pi drives (before rebooting Pi or server)

```bash
# On the Pi:
sudo systemctl stop storage-a-unlock.service storage-b-unlock.service
# ExecStop in the service handles umount and luksClose automatically.
```

---

## Known limitation: rebooting the server does NOT lock Pi drives

> **Important**: if the Pi's NVMe drives are already unlocked and mounted,
> rebooting the server or locking the control LUKS layer does **not**
> retroactively lock the Pi's drives. They remain mounted until the Pi is
> rebooted or the drives are explicitly closed on the Pi.

To fully revoke Pi NVMe access:
1. Stop any services using the drives (NFS server on Pi: `sudo systemctl stop nfs-server`)
2. `sudo systemctl stop storage-a-unlock storage-b-unlock`
3. Verify: `lsblk` — `/dev/mapper/storage-a` and `/dev/mapper/storage-b` should be gone

---

## Backing up Tang keys

Tang keys are the most critical backup. If lost, Clevis cannot auto-unlock
Pi drives and you must fall back to LUKS passphrases.

```bash
# On the server, with control layer mounted:
sudo ls /mnt/control/tang/          # verify keys are present

# Back up to offline storage (USB, offline machine):
sudo tar -czf tang-keys-$(date +%Y%m%d).tar.gz -C /mnt/control tang
# Move the archive off the server immediately.

# Verify the backup is intact:
tar -tzf tang-keys-$(date +%Y%m%d).tar.gz
```

Store Tang key backups:
- Offline (USB drive in secure physical storage)
- NOT on the server itself
- NOT on the Raspberry Pi

---

## Backing up LUKS headers

LUKS headers must be backed up separately from the data. A corrupted header
makes the volume unrecoverable even with the correct passphrase.

```bash
# On the server (do this once after installation, and after any re-partition):
sudo cryptsetup luksHeaderBackup /dev/lanbat/control --header-backup-file server-control-luks-header.img
sudo cryptsetup luksHeaderBackup /dev/lanbat/workload --header-backup-file server-workload-luks-header.img

# On the Raspberry Pi (do this after Clevis bind):
sudo cryptsetup luksHeaderBackup /dev/disk/by-id/<drive-a> --header-backup-file pi-storage-a-luks-header.img
sudo cryptsetup luksHeaderBackup /dev/disk/by-id/<drive-b> --header-backup-file pi-storage-b-luks-header.img
```

Store LUKS header backups offline, separate from Tang key backups.

Restoring a LUKS header:
```bash
sudo cryptsetup luksHeaderRestore /dev/<device> --header-backup-file <file>
# After restore, re-bind Clevis if the header was related to a Clevis slot:
sudo clevis luks bind -d /dev/disk/by-id/<drive> tang '{"url":"http://SERVER_IP:7500"}' -y
```

---

## Running backups manually

### Host backup

```bash
# Adjust repo and password to your configuration.
export RESTIC_REPOSITORY="<host-repo>"
export RESTIC_PASSWORD_FILE="/run/agenix/restic-host-password"
restic backup /etc /root /var/lib/nixos
restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
```

### Control backup (requires control-online.target)

```bash
export RESTIC_REPOSITORY="<control-repo>"
export RESTIC_PASSWORD_FILE="/run/agenix/restic-control-password"
restic backup /mnt/control
restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
```

### Workload backup (requires workload-online.target)

```bash
# Dump both PostgreSQL instances first for consistency.
sudo install -d -o postgres -m 0700 /mnt/workload/postgresql-dumps
sudo -u postgres sh -c 'pg_dumpall > /mnt/workload/postgresql-dumps/workload-$(date +%Y%m%d).sql'
sudo -u postgres sh -c 'pg_dumpall -h /run/postgresql-always-on -p 5433 > /mnt/workload/postgresql-dumps/always-on-$(date +%Y%m%d).sql'

export RESTIC_REPOSITORY="<workload-repo>"
export RESTIC_PASSWORD_FILE="/run/agenix/restic-workload-password"
restic backup /mnt/workload
restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
```

### Check backup integrity

```bash
restic -r <repo> check
```

### Test restore (monthly recommended)

```bash
restic -r <repo> restore latest --target /tmp/restore-test
ls /tmp/restore-test/mnt/workload/
rm -rf /tmp/restore-test
```

---

## Raspberry Pi secure-volume backup guidance

The Pi's NVMe volumes (`/mnt/storage-a`, `/mnt/storage-b`) hold bulk media,
downloads, and nextcloud data. Back up from **inside the unlocked filesystem**
using restic or rsync:

```bash
# From the Pi (drives must be mounted):
restic -r <pi-backup-repo> backup /mnt/storage-a /mnt/storage-b
```

**Why not back up the raw encrypted block device?**
- The LUKS-encrypted raw device (`/dev/nvme0n1`) changes every byte on every
  write due to encryption. Incremental block-level backups are therefore
  ineffective — every snapshot would be nearly full-size.
- Filesystem-level backups (restic, rsync) understand which files changed and
  transfer only deltas. This is far more efficient for routine backup.
- Keep LUKS header backups separately (see above) for disaster recovery.

**Separate LUKS header backup (on the Pi)**:
```bash
sudo cryptsetup luksHeaderBackup /dev/disk/by-id/<drive-a> \
  --header-backup-file /mnt/storage-b/backups/pi-storage-a-header-$(date +%Y%m%d).img
sudo cryptsetup luksHeaderBackup /dev/disk/by-id/<drive-b> \
  --header-backup-file /mnt/storage-b/backups/pi-storage-b-header-$(date +%Y%m%d).img
```

Note: keep a copy of the headers *off* the Pi as well (e.g. scp to server
backup target or offline storage). Losing both the drive and the header backup
stored on that drive leaves you with only the passphrase for recovery.

---

## Re-binding Clevis after Tang key rotation

If Tang keys are rotated (new key pair generated), existing Clevis bindings
become invalid. Re-bind each Pi NVMe drive:

```bash
# On the Pi (drives should be open but can be unlocked manually for this step):
sudo clevis luks regen -d /dev/disk/by-id/<drive-a> -s <slot>
sudo clevis luks regen -d /dev/disk/by-id/<drive-b> -s <slot>
# Where <slot> is the Clevis keyslot number (usually 1 — check with: clevis luks list -d <drive>)
```

Then run a LUKS header backup again to capture the new Clevis metadata.

---

## Full server restore order

**New or wiped disk:** install as in `docs/deployment-checklist.md` Phase 1 (disko creates
fresh volumes), then restore the data:

1. `sudo cryptsetup luksOpen /dev/lanbat/control control-luks && sudo mount /dev/mapper/control-luks /mnt/control`
2. Restore Tang keys from control backup:
   `restic -r <control-repo> restore latest --target /`
3. `sudo systemctl start control-online.target` — Tang starts
4. Verify Tang: `curl http://127.0.0.1:7500/adv`
5. Same for workload: open LUKS, mount, restore from workload backup
6. `sudo systemctl start workload-online.target` — workload services start

**Host root only, LUKS volumes intact:** don't let disko format the disk. From the
installer, recreate the root filesystem and install onto the existing layout:

```bash
ssh nixos@<installer-ip> sudo mkfs.ext4 -F /dev/lanbat/root
nixos-anywhere --flake path:.#homelab-server --target-host nixos@<installer-ip> \
  --disko-mode mount --extra-files /tmp/server-root
```

Pass the old SSH host key in `/tmp/server-root/etc/ssh/` if you still have it, so the
secrets decrypt without re-keying. Then run `unlock-control` and `unlock-workload` as
usual. If a LUKS header was lost, restore it from the header backup first (see above).

---

## Clevis bind reference (initial Pi setup)

Run once per NVMe drive after formatting and while Tang is reachable:

```bash
# On the Pi:
sudo clevis luks bind -d /dev/disk/by-id/<drive-a> tang \
  '{"url":"http://SERVER_IP:7500"}' -y

sudo clevis luks bind -d /dev/disk/by-id/<drive-b> tang \
  '{"url":"http://SERVER_IP:7500"}' -y

# Verify binding:
sudo clevis luks list -d /dev/disk/by-id/<drive-a>
sudo clevis luks list -d /dev/disk/by-id/<drive-b>
```

Back up LUKS headers immediately after binding.
