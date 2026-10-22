# lanbat/nixconf

[![CI](https://github.com/lanbat/nixconf/actions/workflows/check.yml/badge.svg?event=pull_request)](https://github.com/lanbat/nixconf/actions/workflows/check.yml)
[![nightly](https://github.com/lanbat/nixconf/actions/workflows/nightly.yml/badge.svg)](https://github.com/lanbat/nixconf/actions/workflows/nightly.yml)
[![pages](https://github.com/lanbat/nixconf/actions/workflows/pages.yml/badge.svg)](https://github.com/lanbat/nixconf/actions/workflows/pages.yml)
[![Last commit](https://img.shields.io/github/last-commit/lanbat/nixconf)](https://github.com/lanbat/nixconf/commits/master)
[![Good first issues](https://img.shields.io/github/issues/lanbat/nixconf/good%20first%20issue?label=good%20first%20issues)](https://github.com/lanbat/nixconf/labels/good%20first%20issue)
[![Built with Nix](https://img.shields.io/badge/Built%20with-Nix-5277C3?logo=nixos&logoColor=white)](https://nixos.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![IRC: #lanbat on Libera.Chat](https://img.shields.io/badge/IRC-%23lanbat%20on%20Libera.Chat-2f4bff.svg)](https://web.libera.chat/#lanbat)

**[Feature list](docs/features.md)** · **[Take the interactive tour →](https://lanbat.github.io/nixconf/)** · part of [LAN BAT](https://lanbat.github.io/), open home infrastructure

**A complete, production-grade homelab written in Nix** — two machines, one identity
provider, 20+ self-hosted services, and zero Kubernetes.

An `x86_64` server is the brain: single sign-on, media, photos, home automation and the
reverse proxy. A Raspberry Pi 5 is the encrypted storage, the TV frontend and a voice
satellite. Both are one NixOS flake — declarative, checked in CI, laid out with
[disko](https://github.com/nix-community/disko), secrets encrypted with
[agenix](https://github.com/ryantm/agenix) and deployed with
[deploy-rs](https://github.com/serokell/deploy-rs).

This repository is published as a **reference**. Read it to see how the pieces fit, fork
it to make it yours, or borrow the patterns that save you weeks.

## What you get

- **One SSO for everything.** [Authentik](https://github.com/goauthentik/authentik) signs
  users into every service, wired automatically from each service's one-line description
  — not hand-configured per app. See [Authentication & access control](docs/auth.md).
- **Two security tiers.** Always-on infrastructure boots without a key; personal data
  (photos, passwords, cloud, media) sits on an encrypted LUKS layer you unlock on demand.
  See [Secure layers](docs/secure-layers.md).
- **Fail-safe by design.** NFS-backed services stop cleanly when the Pi's storage drops
  and resume when it returns. See [Failure modes](docs/failure-modes.md).
- **One file per service.** A `lanbat.services.<name>` block declares the subdomain,
  port, auth, tier, storage, account and secrets; the wiring generates the Caddy vhost,
  systemd ordering, accounts, agenix secrets and dashboard entry. Evaluation *rejects*
  inconsistencies — clashing ports or UIDs, forward-auth on an API client — before you
  deploy. See [Architecture](docs/architecture.md).
- **Extensible.** A **profile** is a whole site (home, cabin, staging); hosts take
  **roles** (server, storage-pi, voice-pi); **plugins** — built-in or external flakes —
  add services and machines. Add a service or a site without rewriting config. See
  [Extensibility](docs/extensibility.md).
- **Provisions its own Android TVs** over ADB — device-owner policy, CA trust and APKs —
  as a Nix-built package. See [Android device provisioning](docs/android-devices.md).
- **Tested.** Every pull request runs the assertion and VM suite; a nightly job boots the
  full server.

You can also poke at any deployment straight from the flake: `nix run .#validate-deploy`,
`nix run .#hosts`, and `nix run .#deploy-query -- <host>`.

## Quick links

**Setting up a site**
- [Deployment checklist](docs/deployment-checklist.md): from bare hardware to a running site, step by step
- [Secrets setup](secrets/README.md)
- [Extensibility](docs/extensibility.md): profiles, hosts, plugins and per-site settings
- [Android device provisioning](docs/android-devices.md): TV boxes over ADB

**Running it**
- [Operations guide](docs/operations.md): deploying, updating, health checks, per-service tasks
- [Runbook](docs/runbook.md): unlocking after a reboot, locking, Tang/Clevis, restores
- [Failure modes](docs/failure-modes.md): what happens when the server or the Pi goes away
- [Backup strategy](docs/backup.md)

**How it works**
- [Feature list](docs/features.md): what a site can run
- [Architecture](docs/architecture.md)
- [Authentication & access control](docs/auth.md): users, groups and per-service entitlements
- [Secure layers design](docs/secure-layers.md)
- [Storage layout](docs/storage-layout.md)
- [Security model](docs/security.md)
- [Plugins](docs/plugins.md) and [migrating an older site](docs/migration.md)

## How it fits together

Every server service is one file in `services/`. Besides configuring the service,
the file describes it under `lanbat.services.<name>`: its subdomain and port, who
authenticates users, whether its data lives on the encrypted workload layer, which
Pi drives it needs, its container account, secrets and dashboard entry. The
modules in `modules/wiring/` generate the Caddy vhosts, workload gating, NFS
dependencies, on-demand activators, accounts, agenix secrets and Homepage entries
from those descriptions, and evaluation fails on inconsistencies such as port
clashes. Importing a service file enables it.

```nix
lanbat.services.jellyfin = {
  subdomain = "media";
  port = 8096;
  apiClients = true;
  tier = "workload";
  state = [ "jellyfin" ];
  units = [ "jellyfin" ];
  nfs.drives = [ "a" ];
  account = { uid = 992; extraGroups = [ "media" ]; };
  dashboard = { group = "Media"; name = "Jellyfin"; description = "Media server"; };
};
```

## Repository structure

```
flake.nix                 dynamic hosts from deployment profiles, deploy-rs, checks
deploy.nix.example        root manifest listing active profiles (copy to deploy.nix)
deployments/
  example/deploy.nix      CI example profile
  homelab/deploy.nix.example   template for one site
lib/                      host builder, roles, plugin loader
plugins/                  built-in plugins (services, tv, voice)
services/                 one file per server service
modules/                  core, wiring, and one directory per role: server/, storage/, pi/ (shared by the Pi roles)
hosts/server/             hardware.nix, disk.nix (disko layout)
hosts/pi3/, hosts/pi5/    hardware.nix, one per Raspberry Pi model
tests/                    assertion tests and VM tests
docs/                     feature list, architecture, extensibility, plugins, migration
```

## Before you start

You need:

- an `x86_64` server and a Raspberry Pi 5 with NVMe storage and a 64 GB microSD card (32 GB at least);
- optionally a Raspberry Pi 3 as a Snapcast speaker and voice satellite (a 32 GB microSD card,
  a USB microphone and a speaker; [docs/pi3-satellite.md](docs/pi3-satellite.md));
- a domain for the services (`<service>.<domain>`), and local DNS that resolves those
  names to the server: the router, or the optional `lanbat-dns` plugin, which also
  gives short names (`torrent.lan`). The services stay on your LAN; Caddy serves them with certificates
  from an internal CA that each client device trusts once;
- a workstation with [Nix](https://nixos.org/download/) and flakes enabled.

## First run

Going from a fresh clone to a running site. The full step-by-step — disk layout
(disko), secrets, and post-install — is in
[docs/deployment-checklist.md](docs/deployment-checklist.md).

1. **Enter the dev shell** — it provides `deploy` (deploy-rs), `agenix` and
   `nixos-anywhere`:
   ```bash
   nix develop
   ```
2. **Create the local deploy files** from the checked-in templates. The real files
   are gitignored, so your domain, IPs and plugins are never committed:
   ```bash
   cp deploy.nix.example deploy.nix
   cp deployments/homelab/deploy.nix.example deployments/homelab/deploy.nix
   ```
   Then edit `deployments/homelab/deploy.nix` to set your domain, host IPs and the
   plugins each host enables (see [docs/extensibility.md](docs/extensibility.md)).
3. **Encrypt your secrets** — see [secrets/README.md](secrets/README.md).
4. **Deploy** each host:
   ```bash
   deploy --skip-checks path:.#homelab-server
   deploy --skip-checks path:.#homelab-pi-storage
   ```

## Dashboards

Home Assistant's dashboards build themselves from the devices connected to it: a Home
tab with a section per room (lights, plugs, climate, media, the voice satellite),
media, climate, garden and energy tabs, live cameras with what they have seen, and a
system view. See [docs/dashboards.md](docs/dashboards.md).

## Services

Services run in two tiers. See [docs/secure-layers.md](docs/secure-layers.md) for the full design.

### Start at boot (no unlock needed)

| Service | URL | Auth |
|---|---|---|
| Homepage | `home.<domain>` | none |
| Authentik | `auth.<domain>` | local |
| Home Assistant | `ha.<domain>` | OIDC + local |
| Frigate | `nvr.<domain>` | Caddy fwd-auth |
| Grafana | `grafana.<domain>` | OIDC |
| SearXNG | `search.<domain>` | **none (intentional)** |
| Zigbee2MQTT | `zigbee.<domain>` | Caddy fwd-auth (MQTT bridge to HA) |
| CA page | `ca.<domain>` | none |
| Mosquitto | MQTT port 1883 | local password file |
| InfluxDB | internal only | token auth |
| Music Assistant | `music.<domain>` | Caddy fwd-auth |
| Snapcast | `audio.<domain>` | Caddy fwd-auth |
| Wyoming voice assistant | no web UI (LAN-internal) | firewall-restricted |
| Telegraf | no web UI (writes to InfluxDB) | internal only |

### Workload-gated (require `unlock-workload` after reboot)

| Service | URL | Auth |
|---|---|---|
| Nextcloud | `cloud.<domain>` | OIDC |
| Immich | `photos.<domain>` | OIDC |
| Jellyfin | `media.<domain>` | OIDC / local |
| qBittorrent | `torrent.<domain>` | Caddy fwd-auth |
| Vaultwarden | `vault.<domain>` | own account system |
| Syncthing | `sync.<domain>` | Caddy fwd-auth |
| Samba | SMB port 445 | local smbpasswd |

### On-demand

| Service | URL | Notes |
|---|---|---|
| Bitmagnet | `bitmagnet.<domain>` | Starts on first request, stops after 3 days idle (DHT index needs uptime) |
| RomM | `romm.<domain>` | Starts on first request, stops after 30 min idle |

> **External plugin example:** the reference homelab adds *parking-guard* — Frigate-LPR
> parking enforcement that alerts on unauthorised plates — as an external flake plugin,
> wired in the gitignored `deploy.nix` (not one of the built-in services above). See
> [docs/extensibility.md](docs/extensibility.md#external-plugins).

## Deploying

```bash
nix develop               # deploy (deploy-rs), agenix, nixos-anywhere

# (First time? see "First run" above.) Deploy with a path: reference:
deploy --skip-checks path:.#homelab-server
deploy --skip-checks path:.#homelab-pi-storage
```

`--skip-checks` skips deploy-rs's own `nix flake check`, which would build the Pi's
`aarch64` checks on your workstation; CI runs them instead. See
[Deploying changes](docs/operations.md#deploying-changes).

Host names are `<profile>-<host-key>` (e.g. `homelab-server`). A single-profile
setup that inlines `{ deployment, hosts }` in `deploy.nix` without a `profiles`
wrapper uses unprefixed names (`server`, `pi-storage`).

Configurations only exist when `deploy.nix` is present (gitignored). CI evaluates
the checked-in `deployments/example` profile as `example-server` and
`example-pi-storage`. See [docs/deployment-checklist.md](docs/deployment-checklist.md).

## Design principles

- **Server is the brain** — all compute, databases, SSO, and reverse proxy live on the server.
- **Pi is storage + TV** — encrypted drives, NFS export, Kodi and EmulationStation.
- **Fail safe** — NFS-dependent services stop when the Pi is unreachable; they restart automatically when storage returns.
- **No Kubernetes** — systemd + Podman + NixOS modules are sufficient and far simpler.
- **Minimal containers** — NixOS native services are preferred where modules exist (Nextcloud, Jellyfin, HA, Samba, etc.). Containers are used where native packaging is impractical (Authentik, Immich, Frigate, etc.).
- **One place per service** — a service's vhost, tier, storage dependencies, account and secrets are declared in its own file and checked at evaluation.

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to check a
change locally and the conventions this repo follows, and [SECURITY.md](SECURITY.md)
for reporting vulnerabilities.

## License

[MIT](LICENSE)
