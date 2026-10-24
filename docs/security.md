# Security Model

## Encryption at rest

### Server — three-layer design

The server uses a three-layer design. See `docs/secure-layers.md` for full detail.

- **`/dev/lanbat/root` (host root)** — plain ext4, **not encrypted**. Contains NixOS, SSH,
  networking, admin tools, and always-on service data (always-on PostgreSQL, Authentik, HA,
  Grafana, InfluxDB, Mosquitto, Frigate, Caddy TLS certs). Always available after boot.
  This is intentional: the server must be remotely administrable after reboot without
  physical presence. An encrypted root would require console access for every reboot.

- **`/dev/lanbat/control` (control LUKS)** — LUKS2-encrypted. Contains only Tang key material.
  Unlocked manually by admin after each reboot (`unlock-control`). Until unlocked, Tang
  is unavailable and the Pi cannot auto-unlock its NVMe drives.

- **`/dev/lanbat/workload` (workload LUKS)** — LUKS2-encrypted. Contains workload-gated service
  data: Nextcloud, Immich, Jellyfin, Vaultwarden, Syncthing, Samba, qBittorrent,
  Bitmagnet, including the PostgreSQL instance with the Nextcloud, Immich and Bitmagnet
  databases. Unlocked manually by admin after each reboot (`unlock-workload`).

**Threat model**: if the server is stolen while both LUKS layers are locked, the
attacker gets SSH access to an empty host but cannot reach Tang (Pi drives stay locked)
and cannot access workload data. Host-root data (Authentik, HA history, Grafana, etc.) is
visible, so it is treated as lower-sensitivity data.

### Pi
- Both NVMe drives are LUKS2-encrypted.
- After boot, post-boot services (`storage-a-unlock`, `storage-b-unlock`) contact Tang
  and unlock the drives via Clevis. They retry every 5 minutes until Tang is reachable.
- If the Pi is stolen without the server, drives cannot be decrypted (Tang is unreachable).
- If both server and Pi are stolen while LUKS layers are locked, drives cannot be
  decrypted — Tang keys are on the server's control LUKS, which is locked.
- Fallback LUKS passphrase exists for recovery (set during initial formatting).
- **Back up the Tang key directory** (`/mnt/control/tang/`) — if lost, the LUKS slots
  bound to Tang cannot be opened without the fallback passphrase.

## Network trust

### LAN trust model
- The internal network is treated as **partially trusted** — not zero-trust.
- All web services are HTTPS via Caddy's internal CA. The root CA key is pinned
  in agenix (`secrets/caddy-ca-root-key.age`) and the public cert in the repo
  (`secrets/caddy-ca-root.crt`), so a host-root reinstall does not mint a new
  root. Client devices must still install that root once (see `ca.<domain>`).
- Services that handle sensitive data (Authentik, Nextcloud, Immich) use OIDC.

### Internal CA trust on client devices

Clients trust the homelab by installing the root certificate from
`https://ca.<domain>/root.crt` (same file as `secrets/caddy-ca-root.crt` in the
repo). The CA landing page at `ca.<domain>` has OS-specific install steps.

**Debian/Ubuntu system store:** if the root was regenerated before pinning, an
old `Lanbat Root CA` may still be in `/usr/local/share/ca-certificates/`.
Because every generation shares the same CN, OpenSSL assigns the same subject
hash (`6fbdfd37`) — `update-ca-certificates` can keep trusting the stale key
unless you **remove** the old file first, then install the current cert:

```bash
sudo rm -f /usr/local/share/ca-certificates/lanbat-ca.crt   # or any prior Lanbat root
curl -k https://ca.<domain>/root.crt -o /usr/local/share/ca-certificates/lanbat-ca.crt
sudo update-ca-certificates
```

**Browsers (Firefox, Chrome/Chromium):** these use NSS databases, not only the
system store. Installing via `update-ca-certificates` is not enough for them.
Remove old entries and add the current root with `certutil`, for example:

```bash
# Firefox — repeat per profile under ~/.mozilla/firefox/*/
certutil -d sql:~/.mozilla/firefox/<profile> -D -n "Lanbat Root CA" 2>/dev/null || true
certutil -d sql:~/.mozilla/firefox/<profile> -A -n "Lanbat Root CA" -t "C,," \
  -i /path/to/lanbat-ca.crt

# Chrome/Chromium on Linux
certutil -d sql:~/.pki/nssdb -D -n "Lanbat Root CA" 2>/dev/null || true
certutil -d sql:~/.pki/nssdb -A -n "Lanbat Root CA" -t "C,," -i /path/to/lanbat-ca.crt
```

After a **deliberate root rotation**, every client must have the **old** root
removed from system and NSS stores — not just the new one added. Leftover roots
with the same CN but a different key cause errors such as "Peer's certificate
has an invalid signature".

### TLS chain troubleshooting

**Symptom:** a browser or app reports an untrusted certificate or "invalid
signature" on a homelab HTTPS URL, while `curl` from a correctly configured
workstation succeeds.

**Check whether the server chain is actually wrong** (from a machine with the
repo, substituting a real service hostname for `<host>`):

```bash
openssl s_client -connect <host>:443 -servername <host> -showcerts </dev/null 2>/dev/null \
  | openssl verify -CAfile secrets/caddy-ca-root.crt
```

If this prints `OK`, the served intermediate already chains to the pinned root
and **no server-side cleanup is needed** — fix the client's trust stores (see
above). This is the usual cause after earlier root regenerations left multiple
`Lanbat Root CA` entries on the device.

**If verify fails**, a cached intermediate under `/var/lib/caddy/` may have been
signed by a previous root. On the server:

```bash
sudo systemctl stop caddy
sudo rm -f \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/intermediate.crt \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/intermediate.key \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.key
sudo systemctl start caddy
```

Re-run the `openssl verify` check; once it succeeds, clients that already trust
the pinned root need no further change.
- Services that are admin-only (Frigate, qBittorrent, Bitmagnet) are behind Authentik forward auth.
- SearXNG and Redlib are intentionally unauthenticated (they are search/front-end proxies, not private services). Invidious is open for browsing, watching and the API, but its personal pages (preferences, account, subscriptions, history, playlists) go through Authentik forward auth.

### Firewall
- Server allows: 22 (SSH), 80 (redirect to HTTPS), 443 (HTTPS), 7500 (Tang), 2049 (NFS — Pi only), 1883 (MQTT — LAN only).
- NFS and MQTT are restricted by IP in `extraCommands`.
- Pi allows: 22, 2049 (server only).
- All other inbound traffic is dropped.

### IPv6 exposure
- Caddy binds on IPv6 (`[::]`) for the frontend only.
- Backend services all bind on `127.0.0.1` (IPv4 localhost only).
- No IPv6 is enabled on backend containers.
- **Risk**: if your router advertises a global IPv6 prefix, Caddy's port 443 becomes accessible on that IPv6 address. This is intentional for remote access but means the LAN trust assumption weakens for IPv6.
- To disable IPv6 on Caddy: add `bind 0.0.0.0` to all virtual hosts in caddy.nix.
- **Action required**: review your router's IPv6 firewall rules to block port 443 from the internet if you don't want public access.

## Authentik / SSO risks

- Authentik is the single point of failure for most service auth.
- If Authentik is compromised, all OIDC-integrated services are compromised.
- Mitigation: Authentik runs only on the server (always-on tier, starts at boot).
- All services retain break-glass local admin accounts (Nextcloud admin, HA admin, etc.) — these do not go through Authentik.
- Authentik's own PostgreSQL password is managed via agenix (encrypted at rest in git).

## Home Assistant and Zigbee

- Home Assistant does **not** own the Zigbee USB dongle. Zigbee2MQTT (`zigbee.<domain>`)
  holds the dongle exclusively and bridges devices over MQTT.
- HA discovers Zigbee devices through MQTT discovery (`homeassistant: true` in Z2M).
  Do not enable ZHA in HA — only one service can own the dongle.
- The `ha` group can access `/dev/zigbee` for diagnostics, but Zigbee2MQTT is the
  active bridge.
- HA is not exposed on IPv6 by default (Caddy proxies it on v4 internally).
- HA retains local admin — do not disable it.
- MQTT credentials for Z2M live in `mosquitto-z2m-pass.age` (agenix).
- Zigbee devices communicate on 2.4 GHz RF; Zigbee2MQTT enforces the network key.

## Frigate / cameras

- Camera RTSP streams should use authentication (set in frigate.yml).
- Frigate UI is behind Authentik forward auth.
- Frigate binds on `127.0.0.1:5000` only — not accessible except through Caddy.
- Recordings are stored on encrypted Pi storage.
- Cloud-synced clips use rclone with credentials in an age-encrypted file.
- **Privacy consideration**: indoor cameras. Frigate does motion/object detection locally — nothing is sent to cloud except the clips you configure to sync.

## Reverse proxy isolation

- All services are exposed exclusively through Caddy. No service binds on a public port directly.
- Backend services bind on `127.0.0.1` or use Unix sockets.
- Container services use `--network host` to reach PostgreSQL/Redis on localhost, but do not bind public ports (port mapping is to `127.0.0.1` explicitly).
- The one exception: qBittorrent uses bridge networking with port mapped to `127.0.0.1:8090`.

## Secrets

- All secrets are age-encrypted via agenix.
- Secrets are decrypted by the host using its SSH host key.
- If the server disk is encrypted and the SSH host key is on that disk, secrets are protected at rest.
- The git repository stores only encrypted `.age` files — safe to push to GitHub.
- **Do not** store plaintext passwords or API keys anywhere in this repo.

## Least privilege

- Service users (nextcloud 990, immich 991, jellyfin 992, qbt 994, frigate 995) run with no sudo.
- Each service has its own user/group; they share the `media` group only for storage access.
- Containers run as non-root where possible (linuxserver.io images use PUID/PGID).
- The `admin` human user has `wheel` but is not used for day-to-day service management.

## Break-glass accounts

Every service has a local admin account that does not go through Authentik:
- Nextcloud: `admin` user with password from agenix.
- Home Assistant: local admin configured at first setup.
- qBittorrent: local web UI password (set at first run, stored in `/var/lib/qbittorrent`).
- Authentik: initial admin set at `/if/flow/initial-setup/`.

These accounts should be strong passwords stored in a password manager, not in this repo.
