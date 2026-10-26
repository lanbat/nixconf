# Extensibility

The lanbat NixOS flake is structured in three layers:

| Layer | What it is | Where it lives |
|---|---|---|
| **Deployment profile** | One site or environment (domain, hosts, secrets scope) | `deployments/<profile>/deploy.nix` |
| **Role** | Host-type infrastructure (server, storage-pi, voice-pi) | `lib/roles/` |
| **Plugin** | Optional features enabled per host | `plugins/` or external flake inputs |

## Deployment profiles (multi-site)

A **profile** is a self-contained site: its own domain, IP addresses, disks, and host list. You can run several profiles from one flake — a home lab and a vacation cabin, staging and production, two unrelated sites.

### Layout

```
deploy.nix                          # root manifest (gitignored) — lists active profiles
deployments/
  example/deploy.nix                # CI example (checked in)
  homelab/deploy.nix                # your primary site (gitignored)
  cabin/deploy.nix                  # second site (gitignored)
```

### Root manifest

```nix
# deploy.nix
{ inputs, ... }: {
  profiles = {
    homelab = import ./deployments/homelab/deploy.nix { inherit inputs; };
    cabin   = import ./deployments/cabin/deploy.nix   { inherit inputs; };
  };
}
```

Each profile file has the same shape as before: `{ deployment = { ... }; hosts = { ... }; }`.

### Flake output names

Hosts are exposed as `<profile>-<host-key>`:

| Profile | Host key | Flake attribute | deploy-rs node |
|---|---|---|---|
| `homelab` | `server` | `homelab-server` | `homelab-server` |
| `homelab` | `pi-storage` | `homelab-pi-storage` | `homelab-pi-storage` |
| `cabin` | `server` | `cabin-server` | `cabin-server` |

```bash
deploy --skip-checks path:.#homelab-server
deploy --skip-checks path:.#cabin-pi-storage
```

A single-profile setup that inlines `{ deployment, hosts }` directly in `deploy.nix` (without a `profiles` wrapper) uses the implicit profile name `default`, so host names stay `server` and `pi-storage`.

### Isolation between profiles

Each profile is fully independent:

- Separate `deployment.*` settings (domain, gateway, SSH key, …)
- Separate host keys and IP addresses
- Separate `nixosConfigurations` and `deploy.nodes` entries
- Hosts in one profile only see other hosts **in the same profile** via `config.lanbat.hosts` (cross-profile references are not supported)

Secrets (`secrets/*.age`) are still shared at the repo level; encrypt each secret for the agenix host keys of every profile that needs it.

### Service settings

A service whose configuration differs between sites reads it from
`lanbat.services.<name>.settings`, a typed schema its module declares, rather than
from literals in `services/<name>.nix`. A profile sets those values from a NixOS
module listed in the host's `modules`, which is merged last, so it overrides a
service's defaults without `lib.mkForce`:

```nix
# deployments/<profile>/deploy.nix
hosts.server.modules = [
  ./frigate.nix   # deployments/<profile>/frigate.nix, gitignored like deploy.nix
];
```

Frigate is the first service with a schema: cameras (go2rtc inputs and their
roles, detect, zones, object filters, review, motion, LPR), the detector device,
retention, and `extraConfig` escape hatches for raw Frigate keys, globally and per
camera. The options are documented in `services/frigate.nix`;
`deployments/example/frigate.nix` is a placeholder camera that uses every option. Camera
sources reference credentials as `{FRIGATE_RTSP_USER}` and `{FRIGATE_RTSP_PASSWORD}`,
which Frigate substitutes from `secrets/frigate-rtsp-env.age`, so no credential is
ever written into the profile.

Other services with a schema keep a default that reproduces the repository's own
layout, so a profile sets only what differs:

| Service | Settings | Default |
|---|---|---|
| Snapcast | `clients.<name>.mac` / `.host`: devices allowed to play and control besides the automatic ones (snapclient hosts, Snapdroid boxes), by MAC (IPv4 and IPv6) or IPv4 address | none |
| Samba | workgroup, server and NetBIOS names, `homes` (the per-user `[homes]` share), `shares.<name>` (drive, path, access, masks, a directory to create, raw keys) and `extraGlobal` | the media, private and shared shares on drives `a` and `b` |
| Wyoming | `wakeWord.threshold`, `speechToText.{model,language}`, `textToSpeech.voice`, and the server satellite's `satellite.{name,speaker,mixer,microphoneUsbId}` | British English (`en`, `en_GB-alan-medium`) and the onboard Intel codec (ALSA card `PCH`) |
| Home Assistant | `zigbee2mqttBridge`: the bridge-offline notification and the Overview card | on when Zigbee2MQTT runs on the host |
| Grafana | `alerting.enable`: provisioned alerts sent to the webhook in `grafana-alert-env.age`; `alerting.diskUsedPercent` (a filesystem on any host fuller than this); `alerting.frigateQuietHours` (no Frigate event for this long, when Frigate runs on Grafana's host). Needs InfluxDB on the host | off; 85 %; 3 hours |
| Telegraf | `pingTargets`: the hosts pinged for reachability | the storage Pi, the default gateway and `1.1.1.1` |
| Music Assistant | `stations` (radio stations by name, or by `url`), `podcasts` (RSS feeds), `channels.<name>.url` (YouTube, PeerTube, LBRY/Odysee, Vimeo ... channels played as audio podcasts) and `channelEpisodes`; `updateYtDlp` (daily yt-dlp update, adopted only if it plays every channel the current one does and one more; default on); `podcastCountry`, `bbcSounds`, `radioParadise`, `tuneinUsername`. Needs Home Assistant on the host | RadioBrowser, Radio Paradise, BBC Sounds and iTunes Podcast Search, no stations, podcasts or channels; Audiobookshelf when it runs on the host |
| Audiobookshelf | `drive` and `libraryPath`: the audiobooks folder on Pi storage; `metadataProvider`: where "Match books" looks books up (an Audible store, Google, Open Library, iTunes, FantLab) | drive `b`, `media/audiobooks`; `audible` |
| RomM | `drive`, `libraryPath` and `browserArcadePath`: where the ROM library and the browser's zip copies of the arcade sets live on Pi storage; `authentikAdmin`: the Authentik user whose email RomM's admin gets | drive `b`, `media/roms` and `media/roms-browser/mame`; `akadmin` |
| Immich | `drive` and `uploadPath`: where the originals and uploads live on Pi storage | drive `a`, `photos` |
| Kodi (TV box) | `videoSources.<name>.{path,content,scraper}`: the video library sources (`content` is `movies`, `tvshows` or `musicvideos`; shows are a folder each directly in the source). The defaults use Jellyfin's library folders and content types; Jellyfin's `mixed` Games and Misc libraries are `movies` in Kodi, which has no mixed type; `musicSources.<name>`: the music sources, each scanned once after Kodi starts if Kodi never has. Written into Kodi's databases on every boot; a source no longer listed stops being one; `resumeRewindSeconds`: how far back a film paused for a question resumes (Kodi lands on the keyframe at or before it, so a sparse file goes back further); `invidious`: install the Invidious add-on and point it at this deployment's instance, proxying playback through it | movies, tv, music videos, documentaries, documentary series, gym, games and misc under `media/` on drives `a` and `b`; music and audiobooks on drive `b`; 3 seconds; no Invidious |
| Jellyfin | `imvdb`: music video metadata from IMVDb, with the key in `jellyfin-imvdb-env.age` | off |
| Nextcloud | `storage.{drive,path}`: the directory created for the External Storage app's bulk user data | drive `b`, `nextcloud` |
| Syncthing | `devices.<name>.id` (Syncthing device IDs), `folders.<id>` (label, absolute path, type, `ignorePerms`, the device names it is shared with) and `groups` (supplementary groups for the `syncthing` user, to write a folder another service owns). Reuse the folder IDs your other devices already use, so they pick the server up without re-pairing | no devices or folders: Syncthing runs unpaired |
| qBittorrent | `categories`: category name to save path (a `/` nests a subcategory, whose parent must be declared; an empty path is a grouping category); `preferences`: `qBittorrent.conf` as section, key (with the backslash qBittorrent writes), value. Both are rewritten on every start, so a change in the web UI lasts until the next restart. The web UI keys that make Authentik the only login are fixed | no categories; loopback web UI, VueTorrent, `/media/b/misc` and `/media/b/incomplete`, no UPnP |

A default share is defined field by field at `lib.mkDefault`, so a profile changes
one field of it, drops it with `enable = false`, or adds its own beside it:

```nix
# deployments/<profile>/samba.nix, listed in hosts.server.modules
{
  lanbat.services.samba.settings.shares = {
    private.enable = false;
    scans = { drive = "a"; path = "scans"; readOnly = false; validUsers = [ "@media" ]; };
  };
}
```

Samba's smbd binds to the NFS mounts of exactly the drives its enabled shares use.

### Limiting a service to some users

A service behind Authentik is open to every Authentik user unless its description
lists the groups allowed in, `lanbat.services.<name>.access.groups`. The Authentik
catalogue then binds those groups to the service's applications (forward auth and
OIDC), and everyone else gets Authentik's "Permission denied". Syncthing sets it to
`[ "authentik Admins" ]` (Authentik's built-in admin group) by default; a profile
overrides that like any other value, for instance to a dedicated group instead of
every authentik admin:

```nix
{ lanbat.services.syncthing.access.groups = [ "syncthing" ]; }
```

A group Authentik does not have yet is created, empty, so a mistyped name locks the
service rather than opening it. Blueprints only add and update, so a group you
remove from the list keeps its binding until you delete it in Authentik's admin UI.

Who belongs to those groups is declared the same way, under
`lanbat.deployment.authentik` (the `users` blueprint): each user's `groups` is its
full membership, and a group it names is created if missing.

## Roles

Roles bundle infrastructure modules for a host type. They are not optional — every host declares `role = "server"` (or `storage-pi`, `voice-pi`, or a role one of its plugins adds).

| Role | Purpose |
|---|---|
| `server` | LUKS layers, Caddy, databases, containers |
| `storage-pi` | Encrypted NVMe, NFS export, optional TV/voice plugins |
| `voice-pi` | Lightweight Wyoming satellite endpoint |

`lib/roles.nix` is the role table: for each role, the modules it bundles and
what it requires of a host's deploy entry (a server needs `disks.system`, a
storage Pi at least one entry in `storage.drives`), which
`lib/validate-deploy.nix` checks. What the built-in roles share (hostname,
static address, firewall baseline) is in `lib/roles/common.nix`, and what the Pi
roles share in `lib/roles/pi-common.nix`.

Add a built-in role by creating `lib/roles/<name>.nix` and giving it an entry in
`lib/roles.nix`. A plugin adds one without editing either, through `hostRoles`
(see [plugins.md](plugins.md#host-roles)).

### Replacing a role's bundled modules

Each module a role bundles has a name in `lib/roles.nix`. A host replaces or
drops one from its deploy entry, without editing a tracked file:

```nix
hosts.server.roleModules = {
  wiring-caddy = null;                # no Caddy vhost wiring on this server
  backups = ./backups.nix;            # deployments/<profile>/backups.nix instead
};
```

A replacement is a module or a list of modules and takes the place of the one it
replaces, so the others keep their order; `null` drops it. Naming a module the
role does not bundle fails evaluation and lists the names it does bundle:

| Role | Bundled modules |
|---|---|
| `server` | `role`, `hardware`, `disk`, `control-layer`, `backups`, `wiring-caddy`, `wiring-nfs`, `wiring-on-demand`, `wiring-workload-gate` |
| `storage-pi` | `role`, `clevis-unlock`, `nfs-exports`, `storage`, `user-quotas`, `snapclient`, `telegraf` |
| `voice-pi` | `role`, `audio`, `telegraf` |

Dropping wiring a placed service needs is caught: `modules/wiring/checks.nix`
rejects a service with `onDemand` or `tier = "workload"` on a host without the
on-demand or workload-gate wiring. To add to a role rather than take from it, use the
host's `modules` or its `local/` directory (see [Local modules](#local-modules)).

### Replacing a host's hardware module

A host's `platform` selects the hardware module of its board (kernel, firmware, SD card
filesystems), whatever its role:

| `platform` | Imports | Board |
|---|---|---|
| `raspberry-pi-3` | `hosts/pi3/hardware.nix` | Pi 3 B/B+ booted from the stock NixOS aarch64 SD image (see [pi3-satellite.md](pi3-satellite.md)) |
| `raspberry-pi-4` | `hosts/pi4/hardware.nix` | Pi 4, built with nixos-raspberrypi |
| `raspberry-pi-5` | `hosts/pi5/hardware.nix` | Pi 5, built with nixos-raspberrypi |

`raspberry-pi` still works as an alias for `raspberry-pi-5`. Each board has its own
directory under `hosts/` and an entry in the platform table in `lib/platforms.nix`, which
`lib/validate-deploy.nix` checks a host against (an unknown platform, or a `system` the
board does not run); nothing else in the code names a board. A deploy entry replaces the hardware module with
`hardware`, a module or a list of them, or drops it with `[ ]`:

```nix
hosts.pi-storage.hardware = [ ./pi-nvme-boot.nix ];   # boots from NVMe, not SD
```

On a server the hardware comes with the role instead, as its `hardware` module:
replace that with `roleModules.hardware`, and the disk layout with
`roleModules.disk`. On any other generic machine there is no platform hardware,
and `hardware` only adds its modules.

## Overlay providers

How hosts in a profile reach each other is chosen per profile with
`deployment.overlay.provider`. Each provider is a module that answers the
contract in `modules/core/overlay.nix`, registered by name in
`lib/overlay-providers.nix`. The name is read from deploy data before any module
is evaluated, so `lib/mkHost.nix` imports the chosen module directly. Adding a
provider means writing one module and adding one line to the registry.

| Provider | Fits | Needs | Gives up |
|---|---|---|---|
| `none` (default) | No overlay wanted; LAN traffic with generated allowlists | nothing | Isolation from the LAN segment |
| `wireguard-mesh` | 2–20 hosts you control, reachable from each other or through one that has an endpoint | `overlay.subnet`, `overlay.domain`, and per host `overlay.{ip,publicKey,endpoint?}` plus `secrets/overlay-<host>.age` | Hosts that roam between networks with nobody dialable |

A mesh profile, with keys from `nix run .#overlay-keys`:

```nix
deployment.overlay = {
  provider = "wireguard-mesh";
  subnet = "10.100.0.0/24";
  domain = "lanbat.internal";
};
hosts.server.overlay = {
  ip = "10.100.0.1";
  publicKey = "…";                     # printed by overlay-keys
  endpoint = "192.0.2.10:51820";       # this host can be dialled
};
hosts.pi-storage.overlay = {
  ip = "10.100.0.2";
  publicKey = "…";                     # no endpoint: dials out
};
```

A host without an `overlay` block stays off the mesh, and its edges stay on the
LAN. To pin one edge to the LAN whatever the profile runs, set
`endpoint.transport = "lan"` on the service. Tang and NFS never move: see
[architecture.md](architecture.md#overlay-network).

## Plugins

Plugins add optional features to compatible roles. Built-in plugins:

| Plugin | Roles | What it enables |
|---|---|---|
| `lanbatPlugins.services` | `server` | All homelab services |
| `lanbatPlugins.tv` | `storage-pi` | Kodi + EmulationStation |
| `lanbatPlugins.voice` | `storage-pi`, `voice-pi` | Wyoming satellite |
| `lanbatPlugins.android` | `server` | Provision Android TV boxes over ADB |
| `lanbatPlugins.follow-me` | `server` | Music that follows people, lights and music off in empty rooms (`lanbat.homeAssistant.followMe`) |
| `lanbatPlugins.xiaomi-clock` | `server` | Clock sync for Xiaomi BLE thermometers |
| `lanbatPlugins.dns` | `server`, `storage-pi`, `voice-pi` | CoreDNS LAN resolver: service, host and short names (`deployment.dns`) |

### External plugins

Add a flake input and reference it in a host's `plugins` list:

```nix
# flake.nix inputs
lanbat-media.url = "github:you/lanbat-media";

# deployments/homelab/deploy.nix
hosts.server.plugins = [
  inputs.self.lanbatPlugins.services
  inputs.lanbat-media.lanbatPlugin
];
```

See [plugins.md](plugins.md) for the plugin author contract (version 2: plugins
register their services for `hosts.<key>.services` to select, and declare their
own `deployment.<namespace>` settings). Version 1 plugins still load, with a
warning.

**Worked example — parking-guard.** The reference homelab adds
[*parking-guard*](https://github.com/lanbat/lanbat-justpark-parking), a Frigate-LPR
parking-enforcement service that watches Frigate plate detections and alerts when a
plate is not on the allowlist (JustPark bookings, a Luna CSV export, or a resident
list). It is wired entirely in the gitignored `deploy.nix`:

```nix
# flake.nix inputs
lanbat-justpark-parking = {
  url = "github:lanbat/lanbat-justpark-parking";
  inputs.nixpkgs.follows = "nixpkgs";
};

# deployments/homelab/deploy.nix
hosts.server.plugins = [
  inputs.self.lanbatPlugins.services
  inputs.lanbat-justpark-parking.lanbatPlugin
];

# plugin-specific options (schema in modules/core/settings.nix)
deployment.parkingGuard = {
  siteId = "…";              # JustPark site id
  cameras = [ "driveway" ];  # Frigate camera names to watch
  graceMinutes = 5;
  cooldownMinutes = 30;
  residentPlates = [ ];
};
```

It runs as a small set of systemd units on the server: a continuous LPR evaluator
(`parking-guard.service`), a JustPark/CSV allowlist sync timer
(`parking-guard-sync.*`), and a CSV-import path unit
(`parking-guard-csv-import.*`).

## Local modules

A fork often needs a change no option covers: a firewall rule, a package, an
override of a service's configuration. Rather than editing a tracked file, put
a NixOS module in the deployment's `local/` directory, which is gitignored:

```
deployments/<profile>/local/*.nix              # every host of the profile
deployments/<profile>/local/hosts/<key>/*.nix  # host <key> only
```

Each `.nix` file directly in those directories is imported, in name order,
profile-wide files first; anything else, such as a subdirectory holding a
module's data, is left alone. They are merged after every other module source,
including a host's `modules` list in the deploy file, so they can override
anything core, a role or a plugin sets. A single-profile `deploy.nix` without a
`profiles` wrapper uses `deployments/default/local/`.

```nix
# deployments/homelab/local/hosts/server/firewall.nix
{ ... }:
{
  networking.firewall.allowedTCPPorts = [ 25565 ];
}
```

Because the directory is untracked, a flake evaluated from git (`.#`, and CI)
does not see it; evaluate and deploy the real site from the working tree
(`path:.`), as you already do for `deploy.nix`. With no `local/` directory
nothing changes, which is why the example profile is unaffected.

## Multiple machines per profile

Within one profile you can declare any number of hosts:

```nix
hosts = {
  server = { role = "server"; ... };
  pi-storage = { role = "storage-pi"; ... };
  pi-bedroom = { role = "voice-pi"; ... };
  pi-garage   = { role = "voice-pi"; ... };
};
```

Services that use Pi storage declare which storage host to mount from:

```nix
lanbat.services.jellyfin.nfs = {
  storageHost = "pi-storage";  # defaults to primary storage-pi
  drives = [ "a" "b" ];        # keys of that host's storage.drives
};
```

A storage host's drives are the keys of `hosts.<key>.storage.drives`, one or more,
named with lowercase letters and digits. See
[storage-layout.md](storage-layout.md#drives-are-named-not-counted).

Voice satellites map Home Assistant areas to host keys:

```nix
deployment.voiceRooms = {
  "Living Room" = "pi-storage";
  "Bedroom"     = "pi-bedroom";
  "Office"      = "server";
};
```

### Example: storage Pi + bedroom voice Pi

A profile with one storage host and one voice satellite:

```nix
# deployments/homelab/deploy.nix
hosts = {
  server = {
    role = "server";
    plugins = [ inputs.self.lanbatPlugins.services ];
    # ...
  };
  pi-storage = {
    role = "storage-pi";
    plugins = [
      inputs.self.lanbatPlugins.tv
      inputs.self.lanbatPlugins.voice
    ];
    # ...
  };
  pi-bedroom = {
    role = "voice-pi";
    plugins = [ inputs.self.lanbatPlugins.voice ];
    # ...
  };
};

deployment.voiceRooms = {
  "Living Room" = "pi-storage";
  "Bedroom"     = "pi-bedroom";
};
```

Services on the server that mount Pi storage point at the storage host explicitly:

```nix
# services/jellyfin.nix (or any lanbat.services.*.nfs)
lanbat.services.jellyfin.nfs = {
  storageHost = "pi-storage";
  drives = [ "a" "b" ];
};
```

Match agenix recipients in `secrets/secrets.nix` to those host keys.
`nix run .#secrets-recipients -- homelab` prints the host list of each secret for the
profile; the hand-written equivalent is:

```nix
let
  server     = "ssh-ed25519 ..."; # hosts.server
  pi-storage = "ssh-ed25519 ..."; # hosts.pi-storage
  pi-bedroom = "ssh-ed25519 ..."; # hosts.pi-bedroom
  admin      = "ssh-ed25519 ...";

  serverKeys = [ server admin ];
  allPis     = [ pi-storage pi-bedroom ];
  allKeys    = serverKeys ++ allPis;
in
{
  "telegraf-token.age".publicKeys = allKeys;
  "ha-voice-token.age".publicKeys = allKeys;
  # everything else → serverKeys
}
```

Deploy each host independently:

```bash
deploy --skip-checks path:.#homelab-server
deploy --skip-checks path:.#homelab-pi-storage
deploy --skip-checks path:.#homelab-pi-bedroom
```

See [secrets/README.md](../secrets/README.md) for multi-Pi and multi-profile
recipient patterns.

## Tooling

Flake apps help validate deployment files and query values from scripts:

| Command | Purpose |
|---|---|
| `nix run .#validate-deploy` | Run deploy/profile validation checks (same as the CI check) |
| `nix run .#hosts` | List flake host names and IPs across active profiles |
| `nix run .#deploy-query -- server-ip` | Print the primary server IP (optional second arg: profile name) |
| `nix run .#secrets-recipients` | Print which host keys each secret must be encrypted to (optional `--json`, profile name) |

Other `deploy-query` keys: `domain`, `profile`, `flake-server`, `immich-admin-email`,
`host-ips`, `deploy-file`. Example:

```bash
nix run .#deploy-query -- domain homelab
nix run .#deploy-query -- deploy-file cabin
```

`secrets/lib/read-deploy.sh` and other scripts delegate to `deploy-query` instead
of parsing `deploy.nix` by hand.
