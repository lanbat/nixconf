# Secure Layers Design

## Overview

The server uses a three-layer design that separates the host OS from sensitive
data. The Raspberry Pi uses the server's Tang service to auto-unlock its NVMe
drives after boot.

```
SERVER — one disk (hosts/server/disk.nix)

  ESP        1 GiB     /boot      vfat                  always available
  LVM volume group "lanbat":
    root     150 GiB   /          ext4, no encryption   always available
    control  1 GiB     control    LUKS2 → ext4          locked at boot
    workload 80% free  workload   LUKS2 → ext4          locked at boot
    (free)   ~20%      unallocated, for growing root or workload online
```

## Layer 1 — Host (always available)

- **Device**: `/dev/lanbat/root`
- **Filesystem**: ext4, no encryption
- **Mount**: `/` (root)
- **Contains**: NixOS, SSH, networking, firewall, admin scripts, systemd units,
  always-on service data (always-on PostgreSQL, Authentik, HA, Grafana, InfluxDB, Mosquitto,
  Frigate, Caddy TLS certs, container images)
- **Does NOT contain**: Tang keys, workload-gated service data (Nextcloud, Immich,
  Jellyfin, Audiobookshelf, Vaultwarden, Syncthing, Samba, qBittorrent, Jackett, Bitmagnet, RomM)

After a reboot, this layer is immediately accessible. SSH works. Admin tools
work. Nothing sensitive is exposed.

## Layer 2 — Control LUKS

- **Device**: `/dev/lanbat/control`
- **Filesystem**: LUKS2 → ext4
- **Mount**: `/mnt/control` (manual, not at boot)
- **Unlocked by**: admin passphrase (`unlock-control`)
- **Contains**: `/mnt/control/tang/` — Tang key material only

A bind mount makes `/mnt/control/tang` available as `/var/lib/private/tang`, where
systemd keeps the state of Tang (a DynamicUser service); `/var/lib/tang` links to it.
Tang's socket unit (`tangd.socket`) is `WantedBy=control-online.target` and
will not start until that target is active.

## Layer 3 — Workload LUKS

- **Device**: `/dev/lanbat/workload`
- **Filesystem**: LUKS2 → ext4
- **Mount**: `/mnt/workload` (manual, not at boot)
- **Unlocked by**: admin passphrase (`unlock-workload`)
- **Contains**: workload-gated service data (Nextcloud, Immich, Jellyfin,
  Audiobookshelf, Vaultwarden, Syncthing, Samba, qBittorrent, Jackett, Bitmagnet, RomM), including the workload
  PostgreSQL instance that holds the Nextcloud, Immich, Bitmagnet and RomM databases

Bind mounts overlay `/var/lib/<service>` paths with subdirectories of
`/mnt/workload`. The `workload-online.target` is activated once all bind mounts
are in place. Workload-gated services have `WantedBy=workload-online.target`
and `BindsTo=workload-online.target`. Always-on services are unaffected.

## Service tiers

Services are split into two tiers based on data sensitivity. Each service file declares
its tier in `lanbat.services.<name>.tier`; for workload-gated services it also lists its
`/var/lib` directories (`state`) and systemd units (`units`), and
`modules/wiring/workload-gate.nix` generates the stubs, bind mounts and unit gating.

**Always-on (host root, start at boot, no unlock needed)**

| Service | Data path | Rationale |
|---|---|---|
| Caddy | `/var/lib/caddy` | Reverse proxy and TLS — must be up to serve all services, both tiers |
| PostgreSQL (always-on instance) | `/var/lib/postgresql-always-on` | Databases of Authentik, Home Assistant and Grafana — needed at boot |
| Authentik | `/var/lib/authentik` | Identity provider — SSO must be available for all auth flows |
| Home Assistant | `/var/lib/hass` | Automation and sensor history — availability over confidentiality |
| Grafana | `/var/lib/grafana` | Dashboards and metrics config — monitoring must be up at boot |
| InfluxDB | `/var/lib/influxdb2` | Time-series data — metrics collection starts immediately after boot |
| Mosquitto | `/var/lib/mosquitto` | MQTT broker — IoT devices reconnect at boot |
| Frigate | `/var/lib/frigate` | NVR event database — surveillance must not wait for unlock |
| Music Assistant | `/var/lib/music-assistant` | Music controller — provider config, playlists, player state |
| Snapcast | — | Audio distribution — streams created dynamically by MA |
| Media feed bridge | `/var/lib/media-feed-bridge` | Video channels as audio podcast feeds for MA — loopback only; state is just the downloaded yt-dlp release (updated daily by a timer after a trial on the channels), safe to delete |
| Wyoming pipeline | — | STT/TTS/wake word — model files managed by NixOS module |
| Speaker identification (`voice-id`) | `/mnt/workload/voice-id` (voiceprints, a `workloadDirs` entry) | Speech-to-text proxy, in the voice path, so always-on; while the layer is locked it knows nobody and stores nothing, and it restarts when the layer comes online |
| Assistant router (`assistant-router`) | — | Between Home Assistant's agent and the models, in the voice path; its only state is the request log in `/var/lib/assistant-router` (`logDays` days, what the microphones heard: personal data on the host root — set `logText = false` to keep only tier, route and timing) |
| Who is home (`wifi-presence`) | — | Listens for the people's phones on the LAN for the assistant router; no state |
| LLM gateway (`llm-gateway`) | — | LiteLLM in front of cloud LLM APIs; no state; keys from `llm-gateway-env` |
| llama.cpp (`llama-cpp`) | — | Local conversation model — pinned in the Nix store, no state; runs only when `haLlm` is on the loopback |
| SearXNG | — | Search proxy — stateless |
| Telegraf | — | Metrics collector — stateless |
| CoreDNS (`lanbat-dns` plugin) | — | LAN DNS — must answer at boot; records are generated, stateless |
| Redis | — | Ephemeral cache for Authentik and Immich — no persistent state |
| Zigbee2MQTT | `/var/lib/zigbee2mqtt` | Zigbee bridge — automations must work at boot |
| Homepage | — | Dashboard — stateless, useful before workload is up |

**Workload-gated (LUKS-encrypted, start only after `unlock-workload`)**

```
/mnt/workload/
  postgresql/     — PostgreSQL workload instance: Nextcloud, Immich, Bitmagnet, RomM databases
  nextcloud/      — Nextcloud home (config, apps, data)
  immich/         — Immich thumbnails, encoded video, profiles, ML model cache
  jellyfin/       — Jellyfin library metadata
  audiobookshelf/ — Audiobookshelf accounts, listening progress, book details and covers
  vaultwarden/    — Vaultwarden vault database and attachments
  syncthing/      — Syncthing configuration and block index
  qbittorrent/    — qBittorrent config and session state
  jackett/        — Jackett indexers (with their credentials and cookies), its API key, and the
                    synced indexer definitions (xdg/cardigann/definitions)
  bitmagnet/      — Bitmagnet torrent index
  romm/           — RomM config, artwork, saves and states
  samba/          — Samba configuration and state
```

Container images (`/var/lib/containers`) live on the unencrypted host root.
Images are public software; only the application data above is sensitive.

## Stub directories

Each `/var/lib/<service>` path for **workload-gated** services is a mode-`0000`
empty directory on host root (generated by `modules/wiring/workload-gate.nix`). When
workload is locked, these stubs are inaccessible — no service can read or write them.
When workload is unlocked, the bind mounts overlay the stubs with the real data.

The stub rules use tmpfiles' `:` prefixes (`d /var/lib/<dir> :0000 :root :root`), which
apply the mode and owner only when creating the directory. A tmpfiles run on a live system
(every deploy) therefore leaves the mounted directory's permissions alone.
`tests/workload-gate.nix` checks this in a VM.

This prevents accidental data leakage to host root if a service somehow starts
before workload is mounted (it would fail with a permissions error, which is the
correct and visible failure mode).

Always-on services manage their own `/var/lib/*` directories normally and have
no stubs — NixOS creates them with correct ownership at system activation.

## Tang gating

```
boot
 └── host root available
      └── [admin] unlock-control
           └── /mnt/control mounted
                └── /var/lib/private/tang bind-mounted
                     └── control-online.target activated
                          └── tangd.socket started
                               └── Tang serving keys
                                    └── Raspberry Pi Clevis unlock succeeds
                                         └── NVMe drives mounted on Pi
```

## Workload gating

```
boot
 └── host root available → SSH + always-on services start automatically
      │                    (Caddy, always-on PostgreSQL, Authentik, HA, Grafana,
      │                     InfluxDB, Mosquitto, Frigate, Music Assistant,
      │                     Snapcast, Wyoming,
      │                     SearXNG, Telegraf)
      └── [admin] unlock-workload
           └── /mnt/workload mounted
                └── bind mounts activated (/var/lib/nextcloud, etc.)
                     └── workload-online.target activated
                          └── gated services start
                               (Nextcloud, Immich, Jellyfin, Audiobookshelf,
                                Vaultwarden, Syncthing, Samba, qBittorrent,
                                Jackett, Bitmagnet, RomM)
```

## Raspberry Pi unlock model

The Pi boots from its SD card regardless of Tang availability. After network
is up, `storage-a-unlock.service` and `storage-b-unlock.service` attempt
Clevis/Tang unlock. If Tang is unreachable (server locked), the services fail
and retry every 5 minutes automatically. No manual intervention on the Pi
is needed — once the server is unlocked, the next retry succeeds.

## Known limitations

### Already-unlocked Pi volumes are not retroactively re-locked

Tang/Clevis gating controls **unlock-at-boot** behaviour only. It does **not**
retroactively re-lock NVMe volumes that are already mounted on a **running**
Raspberry Pi if the server is subsequently rebooted or the control LUKS layer
is locked.

**Concretely**: if the Pi's drives are unlocked and mounted, then you reboot or
lock the server, the Pi's drives **remain unlocked and mounted** until the Pi
itself is rebooted or an admin manually closes them.

**Operational implication**: to fully revoke Pi storage access, you must:
1. Stop NFS exports on the Pi (or stop relevant services on the server)
2. `umount /mnt/storage-a && cryptsetup luksClose storage-a` on the Pi
3. `umount /mnt/storage-b && cryptsetup luksClose storage-b` on the Pi
4. (Or simply reboot the Pi — the drives will stay locked until Tang is available)

This is a fundamental property of Tang/Clevis: it gates the unlock event, not
ongoing access to already-unlocked volumes.

### Host root is not encrypted

The host root (`/dev/lanbat/root`) is plain ext4 with no LUKS encryption. This is
intentional: the server must be remotely administrable after reboot without
physical presence. An encrypted root would require someone to type the
passphrase at the physical console on every reboot.

Consequence: SSH host keys and the NixOS configuration on disk are visible
to anyone with physical access to the server disk. The secrets managed by
agenix (in the Nix store) are encrypted with age and are not readable
without the host SSH key — but the host SSH key itself is on unencrypted disk.

The confidentiality model relies on LUKS protecting sensitive data volumes,
not the host OS layer.

### Restic backup credentials are needed to restore

If you lose access to the restic repository passwords (stored as agenix secrets
on the server), you cannot access or restore from the encrypted restic
repositories. Keep restic passwords backed up separately (e.g. in a password
manager).
