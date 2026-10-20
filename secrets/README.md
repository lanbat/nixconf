# Secrets

Secrets are managed with [agenix](https://github.com/ryantm/agenix).

Each secret is an age-encrypted `.age` file in this directory.
They are decrypted at activation time using the host's SSH host key.

Services declare the secrets they read in `lanbat.services.<name>.secrets`; each entry
`<secret> = { }` is a requirement that the profile's provider satisfies. Under `agenix`
that is `<deployment.secrets.root>/<secret>.age`, decrypted to `/run/agenix/<secret>`;
services read the path as `config.lanbat.secrets.<secret>.path`. A requirement whose
`.age` file is missing fails evaluation with the secret's name and the service that
requires it.

A requirement can be conditional (`enable`, set from the setting that uses the secret:
`haLlm`, `haXiaomiBle`, `voiceRooms`, a host's overlay membership). One that is off is not
provisioned, so a profile that leaves the feature off needs no `.age` file for it. A secret
that one service declares and others on the same host read (the Mosquitto passwords,
`hass-bootstrap-env`, `influxdb-admin-token`, `telegraf-token`, `authentik-oidc-secrets`)
is a shared secret: the declarer sets owner, group and mode, and each reader lists it in
`lanbat.services.<name>.readsSecrets`.

Which hosts need each file follows from that, so you do not have to work the recipients
out by hand:

```bash
nix run .#secrets-recipients              # the default profile, as a secrets.nix body
nix run .#secrets-recipients -- homelab   # a named profile
nix run .#secrets-recipients -- --json    # {profile, recipients, off} as JSON
```

It lists every secret that a host of the profile requires with `admin` plus those hosts'
keys (named as in deploy.nix `hosts`), and, in a trailing comment, the secrets that are
declared but off on every host. It never reads or writes a key or an `.age` file.

## Chicken-and-egg: secrets before first install

agenix encrypts secrets to the host SSH key — but the host doesn't exist yet
before the first install. The solution:

1. Add your **admin (workstation) public key** to `secrets.nix` and encrypt all
   secrets with it.
2. **Server:** create its SSH host key on your workstation before installing, add the
   public key to `secrets.nix`, run `agenix -r`, and pass the key to nixos-anywhere with
   `--extra-files` (`docs/deployment-checklist.md` step 1c). The server can decrypt its
   secrets on first boot.
3. **Pi:** read the host key of the booted SD image
   (`ssh root@<pi-ip> cat /etc/ssh/ssh_host_ed25519_key.pub`), add it to `secrets.nix`
   and run `agenix -r` before the first switch (step 2e).

## Setup

### 1. Create your secrets.nix

`secrets/secrets.nix` is gitignored (like `deploy.nix`) — it contains real
SSH public keys that identify your machines and should not be committed.

```bash
cp secrets/secrets.nix.example secrets/secrets.nix
```

Fill in the keys (one per host in your profile, plus your workstation key):

```nix
server     = "ssh-ed25519 AAAA...";   # hosts.server
pi-storage = "ssh-ed25519 AAAA...";   # hosts.pi-storage
pi-bedroom = "ssh-ed25519 AAAA...";   # hosts.pi-bedroom (optional voice-pi)
admin      = "ssh-ed25519 AAAA...";   # from: cat ~/.ssh/id_ed25519.pub
```

Fill in `admin` first and add the host keys at steps 1c and 2e of the deployment
checklist. See `secrets/secrets.nix.example` for the recipient groups
(`serverKeys`, `storagePiKeys`, `allKeys`, …).

### 2. Create all required secrets

Run the generator script — it creates all purely-random secrets automatically:

```bash
bash secrets/generate-secrets.sh
```

For secrets that need manual input (MQTT passwords, rclone config), the script
prints instructions at the end.

Once Authentik is deployed and running, generate the OIDC client secrets with:

```bash
bash secrets/generate-oidc-secrets.sh
```

This creates `authentik-oidc-secrets.age` and wires the matching secrets into
`grafana-env.age`, `nextcloud-oidc-env.age`, and `immich-oidc-env.age`.  It
also prints the client credentials needed for manual UI setup in Jellyfin.

`generate-secrets.sh` also creates `hass-bootstrap-env.age` (owner username and
break-glass password for automated Home Assistant onboarding).

The full list for reference:

```bash
cd secrets

# ---- Authentik ----
# Two KEY=value lines:
#   AUTHENTIK_POSTGRESQL__PASSWORD=<random string>
#   AUTHENTIK_SECRET_KEY=<50+ random chars>
#
# Generate with:
#   openssl rand -base64 36   # AUTHENTIK_POSTGRESQL__PASSWORD
#   openssl rand -base64 50   # AUTHENTIK_SECRET_KEY
#
# AUTHENTIK_SECRET_KEY signs sessions and tokens — generate once and never
# rotate unless you intend to invalidate all active sessions.
agenix -e authentik-env.age

# ---- Nextcloud ----
# Single-line plaintext password:
agenix -e nextcloud-admin-pass.age
# Two KEY=value lines (fill in after creating Authentik OIDC app):
#   NEXTCLOUD_OIDC_CLIENT_ID=<value>
#   NEXTCLOUD_OIDC_CLIENT_SECRET=<value>
agenix -e nextcloud-oidc-env.age

# ---- Immich ----
# One KEY=value line:
#   POSTGRES_PASSWORD=<random string>
agenix -e immich-db-password.age
# Two KEY=value lines (fill in after creating Authentik OIDC app):
#   IMMICH_OAUTH_CLIENT_ID=<value>
#   IMMICH_OAUTH_CLIENT_SECRET=<value>
agenix -e immich-oidc-env.age

# ---- Mosquitto ----
# Each file: single-line plaintext password
agenix -e mosquitto-ha-pass.age
agenix -e mosquitto-frigate-pass.age
agenix -e mosquitto-z2m-pass.age

# ---- Frigate ----
# Full rclone config file — run: rclone config, then paste the result.
# See: https://rclone.org/docs/
agenix -e rclone-frigate-config.age

# ---- InfluxDB ----
# Single-line plaintext password for the admin user:
agenix -e influxdb-admin-password.age
# Single-line operator token (used by both InfluxDB and Grafana):
#   openssl rand -base64 48
agenix -e influxdb-admin-token.age

# ---- Grafana ----
# Four KEY=value lines:
#   GF_SECURITY_SECRET_KEY=<openssl rand -base64 48>
#   GF_SECURITY_ADMIN_PASSWORD=<break-glass password>
#   GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=<from Authentik UI>
#   INFLUXDB_TOKEN=<same value as influxdb-admin-token.age>
agenix -e grafana-env.age
# Only with lanbat.services.grafana.settings.alerting.enable. One KEY=value line:
#   GRAFANA_ALERT_WEBHOOK_URL=<URL alerts are posted to, e.g. a Home Assistant
#   webhook: https://<ha host>/api/webhook/<random id>>
agenix -e grafana-alert-env.age

# ---- Vaultwarden ----
# One KEY=value line:
#   ADMIN_TOKEN=<openssl rand -base64 48>
agenix -e vaultwarden-env.age

# ---- Caddy internal CA ----
# Created once when pinning the root CA (see services/caddy.nix). The public
# cert is caddy-ca-root.crt beside the .age files (committed; override with
# deployment.secrets.caCertificate). Encrypt the private key:
#   agenix -e caddy-ca-root-key.age < /path/to/root.key
# To rotate deliberately: generate a new root, re-encrypt, redeploy, then
# redistribute ca.<domain>/lanbat-ca.crt to every client.

# ---- Telegraf ----
# Choose the value now: InfluxDB provisions a write-only token for the
# "metrics" bucket with exactly this value (services/influxdb.nix), so there is
# nothing to create in its UI. It must differ from influxdb-admin-token.age,
# or provisioning rewrites the operator token into the write-only one.
# One KEY=value line:
#   TELEGRAF_INFLUXDB_TOKEN=<random value, e.g. from openssl rand -hex 32>
agenix -e telegraf-token.age

# ---- Overlay (only with deployment.overlay.provider = "wireguard-mesh") ----
# One WireGuard private key per host on the mesh. Needs a rule per
# overlay-<host>.age in secrets.nix. Generates the keys, encrypts them without
# writing plaintext to disk, and prints the public keys for deploy.nix:
nix run .#overlay-keys
```

### 3. Re-key if host keys change

If you reinstall a machine with a new SSH host key, update `secrets/secrets.nix`, then:

```bash
agenix -r
```

## Multiple Pis

When a profile has more than one Pi, add each host's SSH public key to
`secrets/secrets.nix` and group recipients like the example:

```nix
let
  server     = "...";
  pi-storage = "...";
  pi-bedroom = "...";  # optional voice-pi
  admin      = "...";

  serverKeys    = [ server admin ];
  storagePiKeys = [ pi-storage admin ];
  voicePiKeys   = [ pi-bedroom admin ];
  allPis        = [ pi-storage pi-bedroom ];
  allKeys       = serverKeys ++ allPis;
in
{
  "telegraf-token.age".publicKeys = allKeys;
  "ha-voice-token.age".publicKeys = allKeys;
  # server-only secrets stay on serverKeys
}
```

Secrets shared across hosts (Telegraf token, voice satellite token) use `allKeys`.
Server-only secrets stay on `serverKeys`. Drop `pi-bedroom` from `allPis` if you
have no voice-pi host. `nix run .#secrets-recipients` prints the exact host list for
each secret of your profile, including any Pi that does not need a given secret.

## Multiple profiles (homelab + cabin)

Secrets live at the repo root (`secrets/*.age`) and are shared across all
deployment profiles. Encrypt each `.age` file to the **union** of host keys from
every profile that needs that secret:

```nix
# homelab server + cabin server both need Grafana
serverKeys = [ homelab-server cabin-server admin ];
"grafana-env.age".publicKeys = serverKeys;
```

Run `nix run .#secrets-recipients -- <profile>` for each profile and take the union
of the host lists. This means the same encrypted file works on every profile that is a recipient.
The tradeoff is duplication: a cabin-only secret still sits beside homelab secrets,
and you must re-run `agenix -r` when any profile's host key changes.

## Per-profile secrets

A profile can keep its own secrets instead of sharing `secrets/` with the others, so
homelab and cabin never share recipient lists. Point the profile's
`deployment.secrets.root` at its own directory, for example `./secrets` next to its
`deploy.nix` (the path is resolved relative to the profile), and give that directory its
own `secrets.nix`, generated from the profile's requirements:

```bash
nix run .#secrets-recipients -- cabin   # the body of deployments/cabin/secrets/secrets.nix
cd deployments/cabin/secrets && agenix -e <name>.age
```

Put the internal CA's public certificate (`caddy-ca-root.crt`) there too, or set
`deployment.secrets.caCertificate` to the shared one. Profiles that keep the shared
`secrets/` directory use the union-of-keys pattern above.

## Notes

- The `.age` files are safe to commit to git — they are encrypted.
- `secrets.nix` stays local (gitignored); `secrets.nix.example` is the committed
  template listing every file and its recipients.
- **Never commit plaintext values.**
- The `admin` key allows editing secrets from your workstation without
  needing a running host.

## Complete secret inventory

| File | Format | Used by |
|------|--------|---------|
| `authentik-env.age` | `KEY=value` × 2 | Authentik server + worker |
| `authentik-oidc-secrets.age` | `KEY=value` lines (one per OIDC client, `AUTHENTIK_<NAME>_CLIENT_SECRET`) | Authentik blueprints; shared with Jellyfin and Audiobookshelf (client secrets for `jellyfin-bootstrap` and `audiobookshelf-bootstrap`) |
| `nextcloud-admin-pass.age` | plaintext password | Nextcloud |
| `nextcloud-oidc-env.age` | `KEY=value` × 2 | Nextcloud OIDC setup |
| `immich-db-password.age` | `POSTGRES_PASSWORD=<value>` | Immich postgres container |
| `immich-oidc-env.age` | `KEY=value` × 2 | Immich server container |
| `mosquitto-ha-pass.age` | plaintext password | Mosquitto (Home Assistant user); shared with Home Assistant |
| `mosquitto-frigate-pass.age` | plaintext password | Mosquitto (Frigate user); shared with Frigate |
| `mosquitto-assistant-router-pass.age` | plaintext password | Mosquitto (`assistant-router` user, `lanbat/#` and Home Assistant discovery only); read by the assistant router as a systemd credential, to publish the robots and who is around to Home Assistant. Only with the router and `settings.homeAssistant` (on by default) |
| `mosquitto-z2m-pass.age` | plaintext password | Mosquitto (Zigbee2MQTT user); shared with Zigbee2MQTT |
| `bitmagnet-db-pass.age` | `POSTGRES_PASSWORD=<value>` | Bitmagnet PostgreSQL |
| `frigate-rtsp-env.age` | `FRIGATE_RTSP_USER=<value>`, `FRIGATE_RTSP_PASSWORD=<value>` | Frigate camera RTSP auth |
| `rclone-frigate-config.age` | full rclone config file | Frigate rclone sync |
| `influxdb-admin-password.age` | plaintext password | InfluxDB initial setup |
| `influxdb-admin-token.age` | plaintext token | InfluxDB; shared with Grafana (datasource) |
| `grafana-env.age` | `KEY=value` × 4 | Grafana |
| `grafana-alert-env.age` | `GRAFANA_ALERT_WEBHOOK_URL=<value>` | Grafana alerts (disk almost full, Frigate quiet); only with `lanbat.services.grafana.settings.alerting.enable` |
| `vaultwarden-env.age` | `ADMIN_TOKEN=<value>` | Vaultwarden |
| `searxng-secret.age` | plaintext value | SearXNG session and image-proxy signing |
| `homepage-widgets-env.age` | `KEY=value` lines for widget API keys/tokens | Homepage dashboard widgets |
| `telegraf-token.age` | `TELEGRAF_INFLUXDB_TOKEN=<value>` | Telegraf (server + Pi); shared with InfluxDB on the server (write-token provisioning) |
| `hass-bootstrap-env.age` | `OWNER_USERNAME=<value>`, `OWNER_PASSWORD=<value>` | Home Assistant onboarding; shared with Jellyfin, Immich, Music Assistant and Audiobookshelf, whose setup units sign in with the same owner account |
| `ha-llm-api-key.age` | plaintext API key | Home Assistant's conversation agent (`lanbat.haLlm`); only with `haLlm` set to an external API. An address on the loopback runs the model on the server (`services/llama-cpp.nix`) and needs no key |
| `llm-gateway-env.age` | `ANTHROPIC_API_KEY=...`, and a `*_API_KEY=` line for each other provider in the gateway's model lists | The LLM gateway (`services/llm-gateway.nix`); only when Home Assistant's agent uses the assistant router (`haLlm.baseUrl = "http://127.0.0.1:8092/v1"`) |
| `ha-voice-token.age` | Home Assistant long-lived access token, from `generate-ha-voice-token.sh` | Voice satellites (server + Pi), to speak replies on the room's speakers (`lanbat.voiceRooms`); only with `voiceRooms` set |
| `kodi-web-password.age` | A random password (`openssl rand -hex 16`), no newline | Kodi on the TV Pi (`lanbat-tv`), for its web server, and Home Assistant's Kodi integration on the server |
| `ha-voice-refresh-token.age` | `VOICE_TOKEN_ID=`, `VOICE_TOKEN_JWT_KEY=`, `VOICE_TOKEN_CREATED=`, from `generate-ha-voice-token.sh` | `home-assistant-post-setup`, which adds the token and its "Voice satellites" user to Home Assistant |
| `ha-xiaomi-ble.age` | `<MAC> <bindkey> [entry title]` lines, one Xiaomi BLE device each | `home-assistant-post-setup`, which adds each device's `xiaomi_ble` config entry so Home Assistant can decrypt its advertisements; only with `haXiaomiBle` set. Get a bindkey locally from [Mi Activation](https://atc1441.github.io/Temp_universal_mi_activate.html) — no Xiaomi cloud account |
| `room-presence-irks.age` | `<person key> <32 hex>` lines, one phone each (`#` comments allowed) | `room-presence` on a satellite Pi, which recognises those phones' rotating Bluetooth addresses; only with `lanbat.roomPresence.irks` set on that Pi. Pair the phone with the Pi once and run `room-presence --paired` to get its key ([people.md](../docs/people.md)) |
| `techo5-dashcast-key.age` | A random key of 16 characters or more (`openssl rand -base64 24`), no newline | `techo5-dashcast`, which streams the dashboards and lets in only a device that presents it, and `techo5-bridge`, which hands it to the devices; only with a device whose `dashboard` is `streamed` (the default) |
| `techo5-<name>-api-key.age` | The device's ESPHome encryption key (base64, as the TECHO5 installer printed it), one line | `techo5-bridge`, which connects to the Echo Show with it, and `home-assistant-post-setup`, which adds the device's ESPHome entry; one per `deployment.techo5Devices` entry ([techo5.md](../docs/techo5.md)) |
| `stackchan-wifi.age` | `ssid=<network>` and `password=<password>` lines | `lva-stackchan` on a satellite Pi, as a systemd credential (`LoadCredential`); it hands them to the Stack-chan over USB so the robot can fall back to Wi-Fi when unplugged. Only with `lanbat.stackchan.wifi.enable` set on that Pi ([stackchan.md](../docs/stackchan.md#unplugged-wi-fi)) |
| `caddy-ca-root.crt` | PEM root certificate (public) | Caddy internal CA — committed plaintext; `deployment.secrets.caCertificate` defaults to it |
| `caddy-ca-root-key.age` | PEM EC private key | Caddy internal CA — agenix, owner `caddy` |
| `jellyfin-imvdb-env.age` | `IMVDB_API_KEY=<value>`, a free key from [imvdb.com](https://imvdb.com/developers/api) | `jellyfin-bootstrap`, which gives it to the IMVDb plugin for music video metadata; only with `lanbat.services.jellyfin.settings.imvdb` set |
| `ma-fanarttv-key.age` | plaintext Fanart.tv VIP API key | `music-assistant-setup`, which gives it to the fanart.tv metadata provider so it is not rate-limited; only with `lanbat.services.music-assistant.settings.fanartTvVip` set |
| `romm-db-pass.age` | `POSTGRES_PASSWORD=<value>` and `DB_PASSWD=<same value>` | RomM database password (PostgreSQL setup and the container) |
| `romm-env.age` | `ROMM_AUTH_SECRET_KEY=<openssl rand -hex 32>` and metadata provider keys (`IGDB_CLIENT_ID=`, `SCREENSCRAPER_USER=`, …) | RomM container |
| `romm-oidc-env.age` | `OIDC_CLIENT_SECRET=<value>`, the same value as `AUTHENTIK_ROMM_CLIENT_SECRET` | RomM container, Authentik OIDC login |
| `overlay-<host>.age` | WireGuard private key (`wg genkey`) | systemd-networkd on that host (`root:systemd-network`, 0440); only with `deployment.overlay.provider = "wireguard-mesh"` |
| `wifi-access-point.age` | the home Wi-Fi password, one line, as the router has it | hostapd on the host that extends the home Wi-Fi (`root`, 0400); only with `lanbat.wifi.accessPoint.enable` ([docs/wifi.md](../docs/wifi.md)) |
