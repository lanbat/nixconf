# Architecture

Configuration is organized in three layers (see [extensibility.md](extensibility.md)):

| Layer | Purpose | Location |
|---|---|---|
| **Deployment profile** | Site/environment (domain, hosts, plugins) | `deployments/<profile>/deploy.nix` |
| **Role** | Host infrastructure bundle | `lib/roles/` (`server`, `storage-pi`, `voice-pi`) |
| **Plugin** | Optional features per host | `plugins/` + external flake inputs |

The flake builds one NixOS configuration per host in each active profile
(`homelab-server`, `homelab-pi-storage`, …). A single-profile `deploy.nix` without
a `profiles` wrapper uses unprefixed names (`server`, `pi-storage`).

## Overview

```
                         ┌──────────────────────────────────────────────────────────────┐
                         │                     SERVER                                   │
                         │                                                              │
                         │  LV root — host root (ext4, plain — available at boot)       │
                         │  ┌─────────────────────────────────────────────────────┐    │
                         │  │ SSH (22)  networking  firewall  admin tools          │    │
                         │  │ unlock-control / unlock-workload scripts             │    │
                         │  └────────────────────────┬────────────────────────────┘    │
                         │                           │ manual unlock (passphrase)       │
                         │  LV control — LUKS → /mnt/control                           │
                         │  ┌─────────────────────────────────────────────────────┐    │
                         │  │ Tang (7500) ──────────────────────────────────────┐ │    │
                         │  │  /mnt/control/tang → /var/lib/private/tang (bind) │ │    │
                         │  └───────────────────────────────────────────────────┼─┘    │
                         │                           │ manual unlock (passphrase)│      │
  LAN clients            │  LV workload — LUKS → /mnt/workload                  │      │
  ──────────────────────►│  ┌─────────────────────────────────────────────────┐ │      │
  (SMB: 445)             │  │ always-on + workload-online.target services     │ │      │
                         │  │  Caddy (443/80)         PostgreSQL (5432/5433)  │ │      │
                         │  │  Authentik (9000)        Redis (6379, 6380)     │ │      │
                         │  │  Home Assistant (8123)   Mosquitto (1883)       │ │      │
                         │  │  Nextcloud (8080)        Samba (445)            │ │      │
                         │  │  Vaultwarden (8222)      Grafana (3030)         │ │      │
                         │  │  InfluxDB (8086)         Music Assistant (8095) │ │      │
                         │  │  Snapserver (1704/1780)  Telegraf               │ │      │
                         │  │  Wyoming pipeline + satellite (10700)           │ │      │
                         │  │  Jellyfin / Frigate / Immich / qBittorrent      │ │      │
                         │  │  Bitmagnet / Syncthing / Homepage / SearXNG     │ │      │
                         │  └──────────────────────────┬──────────────────────┘ │      │
                         │                             │ NFS (2049)              │      │
                         └─────────────────────────────┼─────────────────────────┼──────┘
                                                       │                         │
                                                       │   CLEVIS/TANG unlock    │
                                                       │ (Tang TCP 7500) ◄───────┘
                                                       │ retries every 5 min
                                                       │ until Tang reachable
                         ┌─────────────────────────────▼────────────────────────────────┐
                         │                   RASPBERRY PI                               │
                         │                                                              │
                         │  SD card: NixOS OS (boots independently of Tang)            │
                         │                                                              │
                         │  NFS server  ──►  exports /mnt/storage-{a,b}               │
                         │  TV sessions (tv-switch, controller hotkey)                  │
                         │    ├── Kodi                                                  │
                         │    └── EmulationStation (ES-DE) + RetroArch                  │
                         │  Snapclient ──► server:1704                                  │
                         │  Wyoming Satellite (10700) ◄── HA on server                  │
                         │  Telegraf → server:8086                                      │
                         │                                                              │
                         │  /dev/nvme0n1 — NVMe drive A                                │
                         │  ┌──────────────────────────────────────────────────────┐   │
                         │  │ LUKS2  →  XFS (pquota)   [locked until Tang replies] │   │
                         │  │  /mnt/storage-a/media/  (qBittorrent, Jellyfin)      │   │
                         │  │    movies, TV, music videos                          │   │
                         │  │  /mnt/storage-a/photos/      (Immich)                │   │
                         │  │  /mnt/storage-a/surveillance/(Frigate)               │   │
                         │  └──────────────────────────────────────────────────────┘   │
                         │                                                              │
                         │  /dev/nvme1n1 — NVMe drive B                                │
                         │  ┌──────────────────────────────────────────────────────┐   │
                         │  │ LUKS2  →  XFS (pquota)   [locked until Tang replies] │   │
                         │  │  /mnt/storage-b/media/  the rest of the media        │   │
                         │  │  /mnt/storage-b/nextcloud/   (Nextcloud)             │   │
                         │  │  /mnt/storage-b/users/       (SMB homes)             │   │
                         │  │  /mnt/storage-b/shared/      (SMB shared)            │   │
                         │  │  /mnt/storage-b/backups/     (backup target)         │   │
                         │  └──────────────────────────────────────────────────────┘   │
                         └──────────────────────────────────────────────────────────────┘
```

## Service responsibilities

**Server is responsible for:**
- All compute-heavy work (Immich ML, Jellyfin transcoding, Frigate detection)
- All databases (PostgreSQL, SQLite)
- All caches and indexes
- Reverse proxy and TLS
- Identity and SSO
- MQTT broker
- Tang trust anchor
- Voice assistant pipeline (Wyoming: wake word, STT, TTS; Home Assistant's
  conversation agent, backed by a small local LLM served by llama.cpp, an
  external OpenAI-compatible API, or the assistant router, which filters noise,
  lets the local model act on clear device commands and passes the rest to cloud
  models through an LLM gateway) and a voice satellite (microphone + internal
  speaker)
- Metrics storage (InfluxDB) and dashboards (Grafana)
- Metrics collection from both machines (Telegraf)

**Pi is responsible for:**
- Encrypted bulk storage
- NFS export
- TV/gaming frontend
- Voice hardware endpoint (Wyoming satellite: microphone + speaker)
- Metrics collection (Telegraf → server InfluxDB)

## Auth matrix

| Service | Auth method | Why |
|---|---|---|
| Authentik | local only | It IS the identity provider |
| Homepage | none | LAN landing page |
| Home Assistant | Native OIDC (`auth_oidc`, linked by username) + local password fallback | Companion apps sign in through authentik with a device code; API clients use long-lived tokens |
| Nextcloud | OIDC (user_oidc app) + local admin | Native OIDC support |
| Immich | Caddy forward-auth (Authentik) + native OIDC | Bootstrap admin links to Authentik email; mobile apps use /api/* |
| Jellyfin | OIDC (plugin) or local | Native OIDC plugin available |
| Frigate | Caddy forward-auth (Authentik) | No native OIDC |
| qBittorrent | Caddy forward-auth (Authentik), web UI and API | No OIDC; VueTorrent web UI; who did what is in the audit log; Homepage reads it on loopback |
| Bitmagnet | Caddy forward-auth (Authentik) | No native OIDC |
| Jackett | Caddy forward-auth (Authentik), admins only (`access.groups`) | One shared Jackett login and no roles, so everyone let in can change indexers and read their credentials; qBittorrent reaches it on loopback with the API key, not through Caddy |
| Audiobookshelf | Own accounts + native OIDC (Authentik) | Phone apps log in through the same OIDC; an Authentik user gets the account with their username, or a new one |
| RomM | Own accounts + native OIDC (Authentik) | App clients (Argosy Launcher) pair by code; admin email must match the Authentik user |
| SearXNG | None (intentional) | Public LAN search |
| Redlib | None (intentional) | Stateless Reddit front-end; open to the LAN |
| Invidious | Forward-auth, path-bypassed | YouTube front-end; browsing, watching, search and the API are open to the LAN, while its own account pages (preferences, subscriptions, history, playlists) require Authentik |
| Samba | Local smbpasswd (optionally Authentik LDAP) | SMB doesn't speak OIDC |
| MQTT | Local password file | IoT devices don't speak OIDC |
| Vaultwarden | Own account system + admin token | Bitwarden clients need direct API access; no forward auth |
| Grafana | OIDC (Authentik) + local admin | Native generic_oauth support |
| InfluxDB | Token auth (not exposed publicly) | Accessed by Grafana only; no browser UI needed on LAN |
| Sign-in code (`techo5-bridge`, 8775) | The firewall: the Echo Shows' addresses only | A QR code of the room's who link, which the device shows; plain HTTP, nothing more than the robot's NFC tag gives |
| Who tapped (`who`, the assistant router's page, 8772) | Open, but `/signin` and `/admin` behind Caddy forward-auth (Authentik); `/admin` for `settings.tapAdmins` groups only | A phone held to a Stack-chan opens it. An account signs in once; a guest types a name. The page knows the phone by its own signed cookie and reads the login's headers only behind the login; `/admin` is the household list, "People Nabu knows" (docs/people.md); `/settings` and `/lists` (My settings, My lists) are open paths too, but answer accounts only, by the cookie, and every change needs the page's own form token |
| Syncthing | Caddy forward-auth (Authentik), admins only (`access.groups`) | Sync clients use port 22000 directly, not Caddy; only devices declared in the profile connect |
| Music Assistant | Caddy forward-auth (Authentik) | No native OIDC; stream port (8097) not exposed on firewall. Its video-channel feeds (`media-feed-bridge`, 8101) listen on loopback only and resolve only the channels the profile lists |
| Snapcast | Caddy forward-auth (Authentik) | No native auth; streaming and control ports (1704/1705) admit only declared clients (`services/snapcast.nix`) |
| Wyoming satellites | No auth (Pi: firewall-restricted to server IP; server: localhost only) | Internal protocol; only HA connects |
| Wyoming pipeline (STT/TTS/wake word) | No auth (localhost only) | Never exposed outside server |
| Assistant router (`assistant-router`, 8092) | No auth (localhost only) | Home Assistant's conversation agent when `haLlm.baseUrl` is `http://127.0.0.1:8092/v1`; calls llama.cpp and the gateway |
| Assistant router bodies (`assistant-router`, 8770) | No auth; open only to the hosts that consume it (a Stack-chan's Pi) | Robot bodies (`lva-stackchan`) report presence and receive moods, gestures and body commands; fixed fields only (docs/stackchan.md) |
| YouTube for the Echo Show (`yt-show`, 8102) | No auth (localhost only) | The assistant router asks it for a search and for the MP4 an Echo Show plays; it talks to Invidious (`127.0.0.1:8083`) first and yt-dlp second, so only it reaches the internet (docs/techo5.md) |
| YouTube media (`yt-show`'s media app, 8103, `yt.<domain>`) | None (intentional) | Only the 720p remuxes the helper made, with ranges so the screen can seek; the search/resolve API is on the loopback and is not on this vhost |
| LLM gateway (`llm-gateway`, LiteLLM, 8093) | No auth (localhost only); provider keys in `llm-gateway-env` (agenix) | Only the assistant router calls it; it calls cloud LLM APIs outbound, with fallbacks and a monthly budget |
| Speaker identification (`voice-id`, 10303) | No auth (localhost only) | Home Assistant's speech-to-text; a Wyoming proxy in front of faster-whisper |
| Speaker identification API (`voice-id`, 10304) | No auth (localhost only) | The assistant router enrols, refines and forgets voiceprints; voice-id reports each sentence's scores to the router's `/v1/voice` |
| Conversation LLM (`lanbat.haLlm`) | None for the local model (`services/llama-cpp.nix`, loopback only, port 8091); API key (agenix) for an external API | A `haLlm` on the loopback runs llama.cpp on the server; only HA calls it. Any other URL is an external OpenAI-compatible API, called outbound (with the `apple-silicon` voice profile, a Mac on the LAN; `apiKey = false` when it takes none) |

The Authentik side of this matrix is generated: every `forward-auth` service gets a
proxy provider, an application and a place on the embedded outpost, and every service
with an `oidc` description gets an OAuth2 provider and application
(`services/authentik/catalogue.nix`). That includes services with a subdomain that run
on another host, since their vhosts are served, and their forward auth checked, by the
Caddy and Authentik on this one.

## Hostname map

| Hostname | Service |
|---|---|
| `home.<domain>` | Homepage dashboard |
| `auth.<domain>` | Authentik IdP |
| `cloud.<domain>` | Nextcloud |
| `photos.<domain>` | Immich |
| `media.<domain>` | Jellyfin |
| `audiobooks.<domain>` | Audiobookshelf |
| `ha.<domain>` | Home Assistant |
| `nvr.<domain>` | Frigate NVR |
| `torrent.<domain>` | qBittorrent |
| `bitmagnet.<domain>` | Bitmagnet (on-demand) |
| `jackett.<domain>` | Jackett (indexer manager for qBittorrent search; admins only) |
| `romm.<domain>` | RomM (on-demand) |
| `search.<domain>` | SearXNG |
| `reddit.<domain>` | Redlib (Reddit front-end) |
| `youtube.<domain>` | Invidious (YouTube front-end) |
| `ca.<domain>` | CA cert distribution |
| `vault.<domain>` | Vaultwarden password manager |
| `grafana.<domain>` | Grafana dashboards |
| `sync.<domain>` | Syncthing web UI |
| `music.<domain>` | Music Assistant web UI |
| `audio.<domain>` | Snapcast control UI (may merge with `music` when retired) |
| `who.<domain>` | The page a phone held to a Stack-chan opens (assistant router, `settings.tap`) |

DNS assumption: `*.<domain>` resolves to the server's IPv4 address.
Either the router answers it, or hosts with the `lanbat-dns` plugin do (below).

### LAN DNS (lanbat-dns)

`lanbatPlugins.dns` runs CoreDNS (`modules/core/dns.nix`) on the hosts that list
it, normally the server and the storage Pi, so that DNS survives either rebooting.
Every record is computed from the profile at evaluation time, so the instances
serve identical zones and nothing is replicated between them:

| Name | Answers |
|---|---|
| `<subdomain>.<domain>`, `<subdomain>.<short>` | the host running Caddy |
| `<service>.<domain>`, `<service>.<short>` | the host running a service that has no subdomain and runs on one host (Samba, Mosquitto, InfluxDB...) |
| `<hostname>.<domain>`, `<hostname>.<short>` | that host |
| `deployment.dns.extraRecords` | as given |

`<short>` is `deployment.dns.shortSuffix` (e.g. `torrent.lan`). Caddy redirects
each short web name to `<subdomain>.<domain>`, which stays the only name logins,
OIDC and the apps' own URLs see. A short name that is not listed is NXDOMAIN;
an unlisted name under `<domain>` and every other name go to
`deployment.dns.upstreams` (the gateway by default). Only the LAN subnet is
answered.

Monitoring is Telegraf's: each CoreDNS host's Telegraf reads CoreDNS's metrics
page on the loopback (query counts, cache hits), and the server's Telegraf
queries every CoreDNS host (`inputs.dns_query`), recording whether each answers
and how fast. systemd restarts a crashed CoreDNS.

The DNS hosts resolve these names through their own CoreDNS too: a
systemd-resolved delegation (`/etc/systemd/dns-delegate.d/lanbat.dns-delegate`)
sends the domain and the short suffix to the local CoreDNS, then to the other
DNS hosts, so neither a router's IPv6 DNS advertisement nor resolved staying on
the gateway after a failed query can send them elsewhere.

Service-to-service traffic does not use DNS: endpoints resolve to addresses at
evaluation time. The exception is OIDC (`auth.<domain>`), which rootless
containers resolve through the gateway, because Podman drops loopback
resolvers; keep the router's `*.<domain>` record as well.

### Services on another host

Caddy serves the subdomain of every service in the profile, not only those on
its own host. A service on the Caddy host is proxied to `localhost:<port>`, as
always. A service that runs only on another host is proxied to that host at its
endpoint's port and scheme, through `lanbat.endpointHost`: the overlay name when
the edge runs on the overlay, the LAN address otherwise. That is the address the
other host's generated rule admits, because the Caddy host counts as a consumer
of every remote service whose subdomain it serves (see [Network policy](#network-policy)).
Forward auth still runs in Caddy on its own host, before the request leaves it.

Evaluation rejects a remote service that Caddy cannot serve:

- one placed on several hosts, none of them Caddy's. A vhost has one upstream,
  and picking one would be a guess. A copy on the Caddy host wins over the others.
- one without an endpoint, or whose endpoint scheme is not `http` or `https`.
- one that is `onDemand`. Its activator runs on its own host and is not part of
  its endpoint, so place on-demand services on the Caddy host.
- one whose `caddy.extraConfig` or `caddy.proxyOptions` mentions localhost. Those
  directives run on the Caddy host, where localhost is not the service.
- one whose subdomain is also used by a service on the Caddy host.

## On-demand services

Services with `lanbat.services.<name>.onDemand` start on the first HTTP request via
the activator proxy (`modules/wiring/on-demand.nix`) and stop after `idleMinutes` without
requests. Bitmagnet stops after 3 days idle, RomM after 30 minutes.

The activator is a lightweight Python proxy that:
1. Receives requests meant for Bitmagnet.
2. If Bitmagnet is not running: starts it, returns a loading page.
3. If Bitmagnet is running: transparently proxies the request.

## Network policy

A service that publishes an `endpoint` admits exactly the hosts running a
service that named it in `consumes`, and drops everything else.
`modules/wiring/policy.nix` generates those rules from the descriptions, so
adding a host to a profile needs no firewall edit. A host whose Caddy serves a
service's subdomain from elsewhere is a consumer too, without naming it in
`consumes` (`proxyHostsOf` in `lib/endpoints.nix`); the vhosts in
`modules/wiring/caddy.nix` are generated from the same function.

An inserted `ACCEPT` opens a port on its own, because it lands above the jump to
`nixos-fw`. A service reached from another host therefore does not need its port
in `allowedTCPPorts` as well — declaring the edge is enough.

Two things sit outside this deliberately:

- **NFS**, because it is wiring driven by `nfs.drives` rather than a service, so
  it has no endpoint to generate from. `lib/roles/storage-pi.nix` generates its
  rules from `nfs.drives` instead: the hosts running a service that uses a
  storage host's drives (`lib/nfs-clients.nix`) are the hosts it exports to and
  the only ones admitted to port 2049.
- **Tang**, because it publishes no endpoint. The Pi reaches it to unlock its
  LUKS storage, and that path must not depend on generated policy.

A TV box's Kodi publishes its web server (8080) and TCP notification port (9090) as the
`kodi` and `kodi-events` endpoints, and Home Assistant consumes both, so only Home
Assistant's host is admitted to them.

The Pi's own clients use the same edges. Its Telegraf consumes `influxdb` and its
snapclient consumes `snapcast`, and each takes the provider's host from the
profile-wide endpoint table rather than assuming the server, so either provider can
move to another host. A service publishes one endpoint, and snapcast's is its web UI
(1780, bound to loopback) rather than the streaming port snapclient uses (1704), so the
edge admits the Pi to a port it does not connect to. That rule is inert; it is the cost
of one endpoint per service. The streaming and control ports have their own allowlist in
`services/snapcast.nix`, built from the same consumers plus Snapdroid boxes and listed
clients.

**The rules are IPv4 only.** `lanbat.hosts.<key>.networking.ip` is typed `ipv4`,
so they are `iptables` rather than `ip6tables`. A service that also listens on
IPv6 is not covered by them, and is reachable over IPv6 on the LAN if its port
is open. Closing that needs a v6 address in the host schema.

## Wi-Fi coverage

A wired host can extend the home Wi-Fi from a USB dongle: `lanbat.wifi.accessPoint`
serves the router's own network name and password on a channel of its own, and its
clients reach the LAN through the host's bridge (`lanbat.network.bridge`), which the
host's LAN address moves onto. See [wifi.md](wifi.md).

## Overlay network

Cross-host service traffic can run on a private encrypted network instead of
the LAN segment shared with IoT devices and guests. Which network, if any, is a
per-profile choice, `deployment.overlay.provider`, answered by a provider
module selected in `lib/mkHost.nix` from `lib/overlay-providers.nix`. Every
provider answers the same contract, `lanbat.overlay` in
`modules/core/overlay.nix`: `nameOf`, `addressOf`, `onOverlay`, `interface` and
`unit`.

| Provider | Cross-host traffic runs over | Names |
|---|---|---|
| `none` (default) | the LAN, exactly as without an overlay | LAN hostname and address |
| `wireguard-mesh` | a WireGuard interface, `lanbat0`, on every host with an `overlay` block | `<hostname>.<overlay.domain>` in `networking.hosts` |

An endpoint's `transport` says whether its edges use the overlay. It defaults to
`"overlay"` once the profile runs one and `"lan"` otherwise. An edge runs on the
overlay only when its transport is `"overlay"` and both hosts are on it.
Otherwise it stays on the LAN, so a host can join later. Both ends make the same
choice. The providing host's generated rule admits the consumer's overlay
address on the overlay interface, and the consumer dials the overlay name
(`lanbat.endpointHost`). A LAN edge admits and dials the LAN address.

In the mesh, every member has a peer entry for every other member. A host with
an `endpoint` can be dialled. A host without one sends keepalives toward those
that have one, so a host behind NAT joins by dialling out, and a rendezvous host
is just a host with an endpoint. Each host's private key is
`secrets/overlay-<host>.age`, created with `nix run .#overlay-keys`, which also
prints the public keys for `deploy.nix`.

The overlay is never required for a host to come online. **Tang and NFS stay on
the LAN by design:** Tang publishes no endpoint, so it has no transport to move,
and NFS is wiring with its own literal rules. A broken overlay therefore cannot
stop the storage Pi unlocking its drives or the server mounting them. See
[failure-modes.md](failure-modes.md#overlay-down).

Clients (phones, TVs, laptops) are not overlay members. They reach Caddy on the
LAN as before, and discovery (mDNS, Jellyfin, Snapcast) stays on the LAN.

## NFS dependency model

Services that read/write Pi storage set `lanbat.services.<name>.nfs.drives`.
`modules/wiring/nfs.nix` adds `bindsTo` and `after` dependencies on the NFS mount units.
If the mount disappears, the service is stopped. When the mount returns, the service restarts.

The mounts use soft NFS with a 30-second timeout,
meaning the kernel gives up on a stalled NFS call after ~90 seconds rather than
blocking forever.

## Tooling

Validate deployment files and query profile values from the flake:

```bash
nix run .#validate-deploy
nix run .#hosts
nix run .#deploy-query -- server-ip
```

See [extensibility.md](extensibility.md) for the full list of `deploy-query` keys.
