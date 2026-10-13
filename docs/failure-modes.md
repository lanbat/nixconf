# Failure Modes and Recovery

## Normal boot (server then Pi)

1. Server boots. Host layer comes up immediately — SSH reachable, both LUKS layers locked.
2. Always-on services start automatically: Caddy, PostgreSQL (always-on instance),
   Authentik, HA, Grafana, InfluxDB, Mosquitto, Frigate, Music Assistant, Snapcast, Wyoming, SearXNG, Telegraf.
3. Admin SSHes in and runs `sudo unlock-control` → Tang starts on port 7500.
4. Admin runs `sudo unlock-workload` → the PostgreSQL workload instance, Nextcloud, Immich,
   Jellyfin, Audiobookshelf, Vaultwarden, Syncthing, Samba, qBittorrent, Jackett, Bitmagnet, RomM come up.
5. Pi boots from SD card. After network is up, `storage-a-unlock` and `storage-b-unlock`
   contact Tang, unlock both NVMe drives (retries every 5 min until Tang is reachable).
6. `/mnt/storage-a` and `/mnt/storage-b` mount on the Pi. NFS server starts.
7. Server automounts `/srv/storage/a` and `/srv/storage/b` on first access.
8. NFS-dependent services (Jellyfin, Audiobookshelf, qBittorrent, Frigate, Samba) fully operational.

Steps 1–2 are automatic. Steps 3–4 require a single SSH session after reboot.
Use `sudo unlock-all` to run both in sequence.

---

## Server boots before Pi

Server comes up. NFS mounts have `noauto` + automount — they mount lazily on first access.

If Jellyfin/qBittorrent/Frigate/Samba try to start before the Pi is available:
- The automount unit is triggered.
- It waits for the NFS connection (up to `x-systemd.mount-timeout=30`).
- If Pi is not up yet, the mount fails.
- Services that `bindsTo` the mount are **stopped** (not left in a broken state).
- Systemd will retry the mount unit when NFS automount is triggered again.

Once the Pi comes up and NFS becomes available:
- The automount unit succeeds.
- systemd restarts `jellyfin`, `podman-qbittorrent`, `podman-frigate`, `samba` automatically (Restart=on-failure + RestartSec=15s).

**You do not need to do anything.** This is handled entirely by systemd.

---

## Pi boots before server

1. Pi boots from SD card. Always-on Pi services come up normally.
2. `storage-a-unlock` and `storage-b-unlock` attempt Clevis unlock — Tang is not
   reachable → both services fail and schedule a retry in 5 minutes.
3. Drives stay **encrypted and locked**. NFS server starts but exports empty paths.
4. Server eventually comes up. Admin runs `unlock-control` → Tang starts.
5. On the next retry (within 5 minutes), the Pi's unlock services succeed automatically.
6. Drives mount. NFS becomes available. Server NFS-dependent services restart.

**No manual intervention needed** — the Pi retries automatically until Tang is reachable.

If you want to trigger unlock immediately without waiting for the retry:
```bash
ssh admin@pi
sudo systemctl restart storage-a-unlock.service storage-b-unlock.service
```

Fallback passphrase (if Clevis binding is lost or Tang is permanently unavailable):
```bash
ssh admin@pi
sudo cryptsetup luksOpen /dev/disk/by-id/DRIVE_A_ID storage-a
sudo mount /dev/mapper/storage-a /mnt/storage-a
sudo cryptsetup luksOpen /dev/disk/by-id/DRIVE_B_ID storage-b
sudo mount /dev/mapper/storage-b /mnt/storage-b
```

---

## Pi reboots while server services are running

1. Pi reboots → NFS connection drops.
2. Server's NFS mounts stall → become unavailable.
3. systemd detects mount units failed (soft NFS timeout ~90s).
4. Services bound to those mounts (`bindsTo`) are **stopped** by systemd.
5. Pi reboots, unlocks drives via Clevis, NFS comes back.
6. Server automount unit remounts `/srv/storage/a` and `/srv/storage/b`.
7. Bound services restart automatically (Restart=on-failure).

**Expected total outage for NFS-dependent services: Pi reboot time + ~30s.**
Usually 2-3 minutes total.

Services that stay up during Pi reboot (always-on tier):
- Caddy ✓
- PostgreSQL, always-on instance ✓ (the workload instance keeps running too; it doesn't use NFS)
- Authentik ✓
- Home Assistant ✓
- Grafana ✓
- InfluxDB ✓
- Mosquitto ✓
- Frigate ✓ (local DB; live stream from cameras unaffected)
- SearXNG ✓
- Music Assistant ✓ (library scans fail while Pi NFS is down; service stays up). Radio, podcasts and the video-channel bridge (`media-feed-bridge`) need only the internet (if GitHub is unreachable, or a new yt-dlp is no improvement on the channels, the bridge keeps the yt-dlp it has); its Audiobookshelf provider works only while Audiobookshelf does
- Snapserver ✓
- Wyoming pipeline (STT/TTS/wake word), the speaker-identification proxy (`voice-id`, which Home Assistant's speech-to-text goes through: if it is down, voice commands get no transcript until it restarts; switching the Voice pipeline's speech-to-text to faster-whisper in Home Assistant bypasses it; while the workload layer is locked it transcribes as usual but recognises nobody), the local conversation model (`llama-cpp`, when `haLlm` is on the loopback) and the server's voice satellite ✓
- Who is home (`wifi-presence`) ✓ (if it stops, the router forgets who is home after 3 minutes; recognition carries on without it)
- Telegraf (server) ✓
- Redis (Immich) ✓
- Homepage ✓

Workload-gated services that pause and restart (NFS-dependent):
- Jellyfin ⏸→▶
- Audiobookshelf ⏸→▶ (Music Assistant's Audiobookshelf provider is reconnected by `music-assistant-audiobookshelf` at each unlock)
- qBittorrent ⏸→▶
- Frigate ⏸→▶
- Samba ⏸→▶
- Immich server ⏸→▶ (only the storage-facing parts)
- Syncthing ⏸→▶ (synced folder is on Drive B)

Note: the Wyoming satellite on the Pi also goes down during a Pi reboot, so
voice commands to it will be unavailable until the Pi is back up.
The server-side Wyoming pipeline (STT/TTS/wake word) and the server's own
satellite stay running throughout.

With the assistant router (`haLlm.baseUrl = "http://127.0.0.1:8092/v1"`), noise is
dropped and clear device commands are acted on by the local model even when the
internet, the gateway or the cloud provider is down, or the monthly budget is spent;
requests that need the cloud get "I can't reach the online assistant right now."
instead of a guess. If the router itself is down, the agent has no LLM: Home
Assistant's local intents still answer what they understand.

The conversation agent's LLM (`lanbat.haLlm`) is the `llama-cpp` service on the
server when its address is on the loopback, and an external API otherwise. The
local model needs neither the Pi nor the internet, but it is slow right after a
start, or after the exposed entities change, because it has to read Home
Assistant's whole prompt once (about 20 seconds for `qwen3-1.7b`). While the
model is unavailable, or an external endpoint is unreachable or scaling up from
zero, commands that Home Assistant's local intents understand ("turn on the
kitchen light") still work; anything else fails or waits for the endpoint. With
`voiceCompute.profile = "apple-silicon"` the LLM is on a Mac: when the Mac sleeps
or is off, only the local intents answer; speech-to-text, TTS and the
satellites are on the server and are unaffected.

Pi Telegraf also goes down during a Pi reboot, causing a gap in Pi metrics.
Server metrics continue uninterrupted.

---

## NFS mount timeout and "soft" behaviour

We use `soft` NFS mounts with `timeo=30,retrans=3` (~90 second total timeout).
After the timeout, the kernel returns `EIO` to any process reading from the mount.

Without our `bindsTo` dependency, processes would receive I/O errors and possibly
write corrupt state. With `bindsTo`, the service is stopped cleanly before that
happens — this is the safe failure model.

**Do not change soft mounts to hard mounts** without also removing the `bindsTo`
dependencies. Hard mounts will block forever and prevent services from stopping.

---

## Overlay down

This applies only when the profile runs an overlay
(`deployment.overlay.provider` is not `"none"`). With `"none"` there is no
overlay to lose.

**Unaffected, by design:**
- **Tang**, so the storage Pi still unlocks its drives. Tang publishes no
  endpoint and is reached over the LAN.
- **NFS**, so Pi storage and the services that depend on it stay up.
- **Boot.** The overlay interface is not required for `network-online.target`,
  so no host waits for it.
- **Clients.** They reach Caddy over the LAN.

**Breaks:** cross-host service edges whose endpoint transport is `"overlay"`.
In the example layout these are the Pi's Telegraf writing to InfluxDB, and Home
Assistant talking to the storage Pi's voice satellite. Each fails on its own,
and the services themselves keep running. A service with a subdomain placed on
another host than Caddy is one of these edges too: its vhost serves the offline
page until the overlay returns.

**Diagnose:** `networkctl status lanbat0`, and `wg show lanbat0` for the latest
handshakes. The usual causes are a stale `publicKey` in `deploy.nix`, a missing
`secrets/overlay-<host>.age` or one encrypted to the wrong host, and a host with
an `endpoint` that nobody can reach.

**Break-glass:** set `deployment.overlay.provider = "none"` and redeploy. Every
edge goes back to the LAN, together with its generated rules and the addresses
consumers dial. The per-host `overlay` blocks can stay where they are.

---

## DNS host down

With the `lanbat-dns` plugin on two hosts, both serve the same names. When the
router forwards the short suffix to both, it moves on to the other; when DHCP
hands both out, clients do. With both down, full names still resolve through
the router's `*.<domain>` record; short names stop.
Hosts list their own CoreDNS first, then the gateway. Service-to-service
traffic is unaffected: it uses addresses fixed at evaluation time.

---

## What needs manual intervention

| Situation | Manual action needed? |
|---|---|
| Host-root reinstall (disk intact) | No CA redistribution — root is in git/agenix; clients keep trusting the same root |
| Browser TLS warning on homelab sites (e.g. "invalid signature") | Usually yes — **client** stale root in trust store (most common); verify with `openssl verify -CAfile secrets/caddy-ca-root.crt` on the served chain — if OK, fix client trust (`docs/security.md`); if not, server intermediate may be stale (same doc, TLS chain troubleshooting) |
| Server LUKS at boot | Yes — SSH in, run `unlock-control` then `unlock-workload` |
| Pi boots before server | No — Pi retries every 5 min until Tang is reachable |
| Normal Pi reboot | No |
| Normal server reboot | Yes — SSH in, run `unlock-all` |
| Server NIC failure | No (Pi retries) |
| Tang key rotation | Yes — re-bind Clevis on Pi |
| Drives fill up | Yes — cleanup or expand |
| NixOS package upgrades | Yes — `nix flake update`, then deploy each host (hosts never rebuild on their own) |
| Server new kernel | Yes — manual reboot required, then unlock both layers |
| Pi new kernel | Yes — reboot the Pi when convenient; Clevis/Tang unlocks its drives |
| Container image updates | Yes — bump the tag and deploy |
| DNS host down | No — the other CoreDNS host answers; with both down, full names resolve through the router (see [DNS host down](#dns-host-down)) |
| Overlay down | No for Tang, NFS and boot; overlay edges pause until it returns, or set `overlay.provider = "none"` and redeploy (see [Overlay down](#overlay-down)) |
| Server root or workload fills up | Yes — grow the volume from LVM free space (`docs/operations.md`) |

---

## qBittorrent and active torrents on Pi reboot

qBittorrent is stopped by systemd when the NFS mount disappears.
qBittorrent saves resume data to disk periodically (default: every 30-60 seconds).
When it restarts, it reads resume data from `/var/lib/qbittorrent` (server-local).
Torrents resume from where they were; partial downloads on NFS are intact.

**Worst case**: up to 60 seconds of torrent data may need to be re-downloaded.
No torrent data is permanently lost because the files are on NFS (Pi drives),
which never had a write error — NFS just became unavailable.

## Jackett and qBittorrent search

Jackett is workload-gated with no NFS dependency: it pauses while the workload layer is
locked and starts with it, and a Pi reboot does not touch it.

qBittorrent `Requires=` `jackett-qbittorrent-plugin`, which `Requires=` Jackett and
waits up to a minute for Jackett's first `ServerConfig.json`:
- If Jackett never writes an API key, the initializer fails and qBittorrent does not
  start. Read `journalctl -u jackett -u jackett-qbittorrent-plugin`.
- If Jackett restarts (deploy or crash), qBittorrent keeps running and searches fail only
  while Jackett is down. The initializer is not rerun, so it learns a changed key at its
  next start.
- `jackett-definitions` (at unlock and every six hours) needs GitHub. If that is
  unreachable it fails and Jackett starts or keeps running with the definitions it has;
  an indexer whose site has since changed fails until the next successful run. A run
  that finds new definitions restarts Jackett, so a search in progress fails.
- If Jackett's state is lost, restore `/var/lib/jackett` from the backup, or add the
  indexers again, then `sudo systemctl restart podman-qbittorrent`: the new API key is
  written on that start, with nothing to edit.
