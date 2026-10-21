# Storage Layout

## Physical layout

```
Raspberry Pi 5
├── Boot media (microSD / USB)
│   └── NixOS system
│
├── Drive A  /dev/disk/by-id/DRIVE_A  →  LUKS  →  /dev/mapper/storage-a  →  XFS  →  /mnt/storage-a
│   ├── /mnt/storage-a/media/              ← qBittorrent saves here, Jellyfin reads
│   │   ├── movies/
│   │   ├── tv/
│   │   └── music-videos/
│   ├── /mnt/storage-a/photos/             ← Immich originals / uploads
│   └── /mnt/storage-a/surveillance/       ← Frigate recordings
│       ├── clips/
│       └── exports/
│
└── Drive B  /dev/disk/by-id/DRIVE_B  →  LUKS  →  /dev/mapper/storage-b  →  XFS  →  /mnt/storage-b
    ├── /mnt/storage-b/media/              ← the rest of the media, as on drive A
    │   ├── music/  documentaries/  documentary-series/  adult/  roms/
    │   ├── audiobooks/  books/  gym/  games/  misc/
    │   ├── music/albums/                      ← qBittorrent's music torrents, seeded in place; music/
    │   │                                        can be a Syncthing folder (settings.folders, ignorePerms)
    │   └── roms-browser/mame/                 ← zip copies of the arcade sets, for RomM's browser player
    ├── /mnt/storage-b/nextcloud/          ← Nextcloud external storage
    ├── /mnt/storage-b/users/              ← per-user SMB home dirs
    │   ├── admin/
    │   │   └── sync/<folder>/                 ← one directory per Syncthing folder
    │   └── ...
    ├── /mnt/storage-b/shared/             ← shared SMB space
    └── /mnt/storage-b/backups/            ← server backup target
        └── server/
            └── YYYYMMDD-HHMMSS/

Main Server
└── /srv/storage/  (NFS mounts from Pi)
    ├── a/   →  NFS  →  pi5:/mnt/storage-a
    └── b/   →  NFS  →  pi5:/mnt/storage-b
```

### Drives are named, not counted

The drives are the keys of `hosts.<key>.storage.drives` in `deploy.nix`. Two drives
named `a` and `b` are the layout above, but a storage host may have one drive or more
than two. Everything is derived from each key:

| Derived from key `<d>` | Where |
|---|---|
| `storage-<d>-unlock.service` and its retry timer | `modules/storage/clevis-unlock.nix` |
| LUKS mapper `/dev/mapper/storage-<d>`, mount point `/mnt/storage-<d>` | `modules/storage/clevis-unlock.nix` |
| `storage-<d>-init.service` (directory tree, then `exportfs -ra`) | `modules/storage/storage.nix` |
| NFS export of `/mnt/storage-<d>` | `modules/storage/nfs-exports.nix` |
| Server mount `/srv/storage/<d>` (`srv-storage-<d>.mount`) | `modules/wiring/nfs.nix` |
| Disk usage and SMART metrics | `modules/pi/telegraf.nix` |

Keys must be lowercase letters and digits (`lib/validate-deploy.nix`), because systemd
escapes a `-` in a mount path and the mount unit would no longer be
`srv-storage-<d>.mount`. A service names the drives it uses in
`lanbat.services.<name>.nfs.drives`; evaluation rejects a name the storage host does not
have.

What lives on which drive is a layout choice kept by name: the directory trees in
`modules/storage/storage.nix` belong to `a` and `b`, the per-user directories and their quotas
to `lanbat.userStorage.drive` (`b` by default), and the services' paths
(`/srv/storage/b/...`) name their drive. A drive without a tree is still unlocked,
initialised and exported, and starts empty. A profile with different drive names moves
the services' paths and `lanbat.userStorage.drive` to match.

## Server disk

The server's disk is laid out by `hosts/server/disk.nix` (disko) during installation:

```
ESP        1 GiB     vfat → /boot
LVM volume group "lanbat"
├── root      150 GiB    ext4 → /                          host layer
├── control   1 GiB      LUKS2 → ext4 → /mnt/control       Tang keys
├── workload  80% free   LUKS2 → ext4 → /mnt/workload      workload-gated service state
└── (free)    ~20%       unallocated
```

The host root holds the Nix store, rootless container images and the state of every
always-on service, so it grows over time. The unallocated space lets root or workload
grow online without reinstalling (`docs/operations.md` § Disk space). Nix collects
garbage weekly and during builds when free space drops below 2 GiB, and each container
account prunes its dangling images weekly.

## Server-local state — host root (always available, unencrypted)

These paths live on `/dev/lanbat/root` and are accessible at boot without any unlock.

```
/etc/caddy/
└── ca-root.crt        Persisted internal root CA (public; also secrets/caddy-ca-root.crt)

/run/agenix/
└── caddy-ca-root-key  Root CA private key (agenix; survives host-root reinstall)

/var/lib/
├── caddy/             Caddy TLS state (intermediate + leaf certs; rotates)
├── hass/              Home Assistant config (history is in PostgreSQL)
├── authentik/         Authentik media, certs
├── postgresql-always-on/  PostgreSQL always-on instance: Authentik, Home Assistant, Grafana
├── frigate/           (config.yml is rendered into the Nix store from the settings)
│   ├── db/            Frigate SQLite event DB
│   ├── clips/         Review snapshots and clips
│   └── recordings/    Motion-only recordings
├── grafana/           Grafana dashboards, users, alert state
├── influxdb2/         InfluxDB data + WAL (BACK THIS UP)
├── mosquitto/         Mosquitto broker state
├── homepage/          Homepage config (stateless, managed in repo)
├── private/assistant-router/  Assistant router request log, a file a day, logDays
│                      kept: what the microphones heard (personal data; with
│                      logText = false only tier, route and timing); people
│                      added at runtime (people.json; how each was met in
│                      people-kinds.json), phones learned from taps on a
│                      robot (phones.json: Wi-Fi address -> person), people's
│                      cards (cards.json: UID -> person) and the key the who
│                      page signs its cookies with (who-session.key)
└── containers/<account>/  rootless Podman image storage, one per container account

/var/lib/private/tang/ ← bind mount from /mnt/control/tang (control LUKS);
                         /var/lib/tang links to it
                         Tang key pairs (BACK THIS UP — only available when
                         control is unlocked)
```

## Server-local state — workload LUKS (available after `unlock-workload`)

These paths are mode-0000 stubs on host root. When workload is unlocked they
are overlaid by bind mounts from `/mnt/workload/`.

```
/mnt/workload/
├── postgresql/        PostgreSQL workload instance: Nextcloud, Immich, Bitmagnet, RomM
├── nextcloud/         Nextcloud app + config (bulk data is on Pi)
├── immich/
│   ├── thumbs/        Generated thumbnails
│   ├── encoded-video/ Re-encoded video previews
│   ├── profile/       User profile photos
│   └── model-cache/   CLIP / face detection ML models (~4 GB)
├── jellyfin/          Jellyfin metadata and configuration
├── audiobookshelf/    Audiobookshelf SQLite DB (accounts, progress), book details and covers
├── qbittorrent/       qBittorrent config + fast-resume data
├── jackett/           Jackett indexers, their credentials and cookies, and the API key (BACK THIS UP)
├── bitmagnet/         Bitmagnet config
├── romm/              RomM config, artwork, saves and states
├── vaultwarden/       Vaultwarden SQLite DB + attachments (BACK THIS UP)
├── syncthing/         Syncthing config + SQLite index (BACK THIS UP)
│                      (actual synced files are on Pi/b/users/<user>/sync)
├── samba/             Samba configuration and state
└── assistant-router/  Indoor sightings' frames, a week at most (the router is
                       always-on: without the layer it keeps sightings, no pictures)

/var/cache/
├── jellyfin/          Jellyfin transcodes + metadata cache (safe to delete)
└── frigate/           Frigate frame buffer (tmpfs equivalent)
```

## Which service reads/writes where

| Service | Config | Database | Bulk content / originals |
|---|---|---|---|
| Caddy | server-local | — | — |
| Authentik | server-local | always-on PostgreSQL | — |
| Home Assistant | server-local | always-on PostgreSQL | — |
| Assistant router | server-local (request log) | server-local SQLite (sightings) | workload (sightings' pictures) |
| Nextcloud | server-local | workload PostgreSQL | Pi/b (external storage) |
| Immich | server-local | workload PostgreSQL | Pi/a/photos |
| Jellyfin | server-local | server-local | Pi/a/media + Pi/b/media |
| Audiobookshelf | server-local | server-local (SQLite) | Pi/b/media/audiobooks (read in place) |
| qBittorrent | server-local | — | Pi/a/media + Pi/b/media (by category) |
| Jackett | server-local | — | — (searches remote indexers; keeps only its configuration) |
| Frigate | server-local | server-local (SQLite) | Pi/a/surveillance |
| Bitmagnet | server-local | workload PostgreSQL | — |
| RomM | server-local | workload PostgreSQL | Pi/b/media/roms (ROM library), Pi/b/media/roms-browser (arcade zips) |
| SearXNG | server-local | — | — |
| Homepage | server-local | — | — |
| Samba | (via nss) | — | Pi/a + Pi/b (the drives of its shares, `lanbat.services.samba.settings`) |
| MQTT | server-local | — | — |
| Vaultwarden | server-local | server-local (SQLite) | — |
| Grafana | server-local | always-on PostgreSQL | — |
| InfluxDB | server-local | server-local | — |
| Syncthing | server-local | server-local (SQLite index) | Pi/b/users/<user>/sync/<folder>, and any folder a profile declares (e.g. Pi/b/media/music) |
| Music Assistant | server-local | server-local (embedded) | Pi/b/media/music (NFS, read-only) |
| Snapcast | — | — | — (streams created dynamically by MA) |
| Wyoming (server) | — | — | — (models re-downloaded on first start) |
| llama.cpp (server) | — | — | — (the model is pinned in the Nix store) |
| Wyoming satellite (Pi) | — | — | — (stateless) |
| Telegraf (server + Pi) | — | → InfluxDB | — |

## XFS project quotas

Project quotas enforce per-directory space limits on the Pi drives.

### Setup

Run `quota-setup.sh` on the Pi after first format (see docs/deployment-checklist.md).

### Project ID assignments

| Project name | ID | Path | Drive | Suggested limit |
|---|---|---|---|---|
| media | 100 | /mnt/storage-a/media | A | no limit (fill the drive) |
| media-b | 101 | /mnt/storage-b/media | B | no limit |
| photos | 102 | /mnt/storage-a/photos | A | no limit |
| surveillance | 103 | /mnt/storage-a/surveillance | A | 500 GB soft, 550 GB hard |
| nextcloud | 200 | /mnt/storage-b/nextcloud | B | 500 GB soft, 550 GB hard |
| shared | 202 | /mnt/storage-b/shared | B | 200 GB soft, 220 GB hard |
| user-* | 300+ | /mnt/storage-b/users/<user> | B | per-user (see human-users.nix) |
| backups | 203 | /mnt/storage-b/backups | B | 300 GB soft, 350 GB hard |

### Per-user unified quotas

Human user storage is declared in `lanbat.humanUsers` (see `modules/core/human-users.nix`).
Each user gets one XFS **project quota** on their entire directory tree under
`/mnt/storage-b/users/<username>/`, covering Samba, Nextcloud, Syncthing and Immich data.

Default quota: `lanbat.userStorage.defaultQuota` (100 GB soft / 110 GB hard).
Override per user with `lanbat.humanUsers.<name>.quota`.

```nix
lanbat.humanUsers.alice = {
  uid = 1002;
  groups = [ "media" ];
  quota = { soft = "200G"; hard = "220G"; };
};
```

Quotas are applied automatically on the Pi by `user-storage-quotas.service` after
storage-b unlocks.

Authentik handles identity only — it does not manage storage quotas.

NFS stores numeric IDs, so a user's UID must be the same on the Pi and the server. Service
accounts pin theirs in `lanbat.services.<name>.account.uid`.

### Reporting

```bash
# Project quotas
sudo xfs_quota -x -c "report -pb -h" /mnt/storage-a
sudo xfs_quota -x -c "report -pb -h" /mnt/storage-b

# User quotas
sudo xfs_quota -x -c "report -ub -h" /mnt/storage-a
```

## Drive allocation rationale

**Drive A** holds performance-sensitive or large-reads-required content:
- Media (Jellyfin sequential reads)
- Downloads (qBittorrent writes)
- Photos (Immich uploads and reads)
- Surveillance (Frigate continuous writes)

This drive sees the most write I/O (downloads + surveillance).
If it fills, remove old recordings first.

**Drive B** holds user data and backups:
- Nextcloud external storage (mixed read/write)
- SMB user homes (mixed)
- Shared space
- Server backups (periodic writes)

This drive is more backup/sync oriented.

## mergerfs (future option)

If you eventually want a single merged view of both drives:

```nix
# Add to Pi storage config
fileSystems."/mnt/storage-merged" = {
  device  = "/mnt/storage-a:/mnt/storage-b";
  fsType  = "fuse.mergerfs";
  options = [ "defaults" "allow_other" "minfreespace=20G" "fsname=storage-merged" ];
  depends = [ "/mnt/storage-a" "/mnt/storage-b" ];
};
```

This is not in the base config — keep drives separate for operational clarity.
Add mergerfs only if you have a specific reason (e.g. a single large Plex library
that spans both drives).
