# Contributing

This repository is an extensible NixOS homelab configuration. Each **deployment profile**
is a site with its own domain, hosts, and plugins. Hosts take **roles** (server,
storage-pi, voice-pi) and enable **plugins** (services, TV, voice, or external flake
inputs). See [docs/extensibility.md](docs/extensibility.md).

It is published as a reference. You are welcome to borrow from it and to send pull
requests with fixes, documentation improvements and ideas.

## Before you start

- Read [docs/architecture.md](docs/architecture.md) and
  [docs/secure-layers.md](docs/secure-layers.md) for the overall design before making changes.
- For larger changes, such as a new service or a restructure, open an issue first so the
  approach can be agreed.
- Issues labeled
  [good first issue](https://github.com/lanbat/nixconf/labels/good%20first%20issue)
  are small and well scoped.

## Checking your change

You need [Nix](https://nixos.org/download/) with flakes enabled. No `deploy.nix` or
secrets are required: CI evaluates the `example` profile as `example-server` and
`example-pi-storage` from `deployments/example/deploy.nix`.

```bash
nix fmt                                   # format all .nix files
nix flake check --no-build --all-systems  # evaluate the example hosts and the checks (needs ~16 GB, see below)
nix build .#checks.x86_64-linux.{assertions,workload-gate,postgresql,music-assistant,overlay-mesh,overlay,policy,plugins,settings-guard,validate-deploy,load-deployments,deploy-rs-fixture}
```

`nix flake check` evaluates everything in one process, which peaks at 13–19 GB of memory.
On a smaller machine, evaluate one check or host at a time, as CI does:

```bash
nix eval --raw .#checks.x86_64-linux.<name>.drvPath
nix eval --raw .#nixosConfigurations.example-server.config.system.build.toplevel.drvPath
```

The workload-gate, postgresql, music-assistant and overlay-mesh tests boot VMs and need
KVM. CI runs every `x86_64-linux` check except `server` on every pull request, in
parallel groups (`CHECK_GROUPS` in `.github/workflows/check.yml`; a check not listed there
runs in the `rest` group). Nothing runs on the push that merges a pull request.

The full server VM test boots the complete server configuration and is too slow for every
PR. CI runs it nightly (03:00 UTC) and on manual dispatch via the **nightly** workflow,
which also runs the whole pull-request suite on `master`.
Run it locally when you change a host, a service's tier or the unlock scripts:

```bash
nix build -L .#checks.x86_64-linux.server  # KVM, about 10 GB of free memory, 15–45 minutes
nix build -L .#checks.aarch64-linux.storage-pi     # an aarch64 machine with KVM, such as the Pi (see tests/storage-pi.nix)
nix build -L .#checks.aarch64-linux.voice-pi
```

GitHub's arm64 runners have no KVM, so CI cannot boot the Pi VM tests. On every pull
request it builds their drivers (`.#checks.aarch64-linux.{storage-pi,voice-pi}.driver`), which
builds the Pi systems without booting them. Run the tests themselves on an aarch64
machine when you change a Pi role, a Pi plugin or `tests/lib/mk-host-fixture.nix`.

## Pull requests

- Keep each pull request focused on one change.
- Say how you tested it. Evaluation only is fine; mention it if you also deployed the change.
- Never commit `deploy.nix`, `deployments/*/deploy.nix`, real IP addresses, domains, SSH keys or plaintext secrets.
- Secrets are [agenix](https://github.com/ryantm/agenix) files encrypted to the
  maintainer's keys, so you cannot read or create them. You do not need to: the `example`
  profile that CI evaluates sets `deployment.secrets.provider = "none"`, which resolves
  every secret to a placeholder in the Nix store. Declare the secret under
  `lanbat.services.<name>.secrets`, add it to `secrets/secrets.nix.example` and the
  inventory in `secrets/README.md`, and say in the pull request what it should contain.
  No `.age` file of any kind is needed for evaluation or the checks to pass.
- If you add or change a service, follow the checklist below, including the docs updates.

## Reporting security issues

Please don't open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).

---

## How the repository fits together

- `services/<name>.nix`: one file per server service. It configures the service and
  describes it under `lanbat.services.<name>`.
- `modules/core/services.nix`: the description's options, with documentation for each.
- `modules/wiring/`: turns the descriptions into Caddy vhosts (`caddy.nix`), workload
  gating (`workload-gate.nix`), NFS dependencies (`nfs.nix`), on-demand activators
  (`on-demand.nix`), accounts (`accounts.nix`) and agenix secrets (`secrets.nix`), and
  rejects inconsistent descriptions (`checks.nix`). `services/homepage.nix` builds the
  dashboard from them.
- `modules/core/`: settings and configuration shared by both hosts.
- `modules/server/`, `modules/storage/`, `modules/pi/`: a role's own modules, named for
  the role (the storage-pi role is `modules/storage/`, with the TV frontend of the `tv`
  plugin), and `modules/pi/` for what both Pi roles share (audio, Snapcast client,
  speakers, Telegraf).
- `lib/roles.nix`: the role table, with each role's bundled modules (a host can
  replace or drop one with `roleModules`) and what it requires of a deploy entry.
- `lib/roles/`: host role modules (server, storage-pi, voice-pi), and what they
  share (`common.nix`, `pi-common.nix`).
- `plugins/`: built-in plugins; enable per host in `deploy.nix`.
- `deployments/`: one `deploy.nix` per site/profile.
- `hosts/server/`, `hosts/pi3/`, `hosts/pi5/`: hardware (one directory per board; a new
  board is a new directory and an entry in the platform table in `lib/platforms.nix`) and
  the server's disk layout (disko). A
  deploy entry can replace them (`roleModules.hardware`, `roleModules.disk`, `hardware`).

## Adding a new service

Follow this checklist every time:

1. **Create** `services/<name>.nix` with the service's NixOS configuration and its
   description:
   ```nix
   lanbat.services.<name> = {
     subdomain = "<name>";   # https://<name>.<domain>
     port = 1234;            # local HTTP port that Caddy proxies to
     auth = "app";           # "app", "forward-auth" or "none"
     dashboard = {
       group = "Utilities";
       name = "<Name>";
       description = "What it does";
     };
   };
   ```
2. **Register** it in `plugins/services/registry.nix` (or ship it as an external plugin's
   `services`; see [docs/plugins.md](docs/plugins.md)).
3. **Describe what applies** (all fields are documented in `modules/core/services.nix`):

   | Field | Set it when the service |
   |---|---|
   | `subdomain`, `port`, `auth` | has a web UI |
   | `apiClients = true` | is called directly by apps or sync clients (rules out forward auth) |
| `oidc` | logs users in through the identity provider over OpenID Connect |
   | `access.groups` | should only be open to some Authentik groups, admins say (needs forward auth or `oidc`) |
   | `caddy.extraConfig`, `caddy.proxyOptions` | needs extra Caddy directives |
   | `caddy.auditLog = true` | should record which user did what (forward auth only) |
   | `extraPorts` | listens on other ports |
   | `endpoint` | is reached over the network by another service |
   | `consumes` | reaches another service over the network |
   | `tier = "workload"`, `state`, `units` | holds personal data that must stay on the encrypted layer |
   | `workloadDirs` | needs directories with an owner on the workload layer before it starts (an always-on service may have one too, and must work without it while the layer is locked) |
   | `nfs.drives` (and `nfs.units`) | reads or writes Pi storage |
   | `onDemand` | should start on the first request and stop when idle |
   | `account` | runs as a rootless container (`container = true`) or needs a pinned UID |
   | `secrets.<file> = { }` | reads `secrets/<file>.age` (`enable` when only a setting needs it) |
   | `readsSecrets` | reads a secret that another service on the host declares |
   | `dashboard` | should appear on Homepage |
   | `lanbat.settingsSchema.<name>` | has configuration that differs between sites (cameras, shares, devices): declare typed options there, as `services/frigate.nix` does, and set the values from the profile (see [docs/extensibility.md](docs/extensibility.md#service-settings)). Declared keys are type checked and others rejected |

4. **Authentik**: nothing to edit. The blueprints are generated from the
   description (`services/authentik/catalogue.nix`): `auth = "forward-auth"` gives the
   service a proxy provider, an application and a place on the embedded outpost, and
   `oidc` gives it an OAuth2 provider and application. For an OIDC client, add its
   `AUTHENTIK_<NAME>_CLIENT_SECRET` line to `authentik-oidc-secrets` (see step 5).
5. **Secrets**: add new files to `secrets/secrets.nix.example` and the inventory in
   `secrets/README.md`. `nix run .#secrets-recipients` prints which hosts need each one.
6. **Check**: run the commands above. Evaluation rejects clashing ports, subdomains,
   UIDs and secrets, forward auth on services with API clients, workload-tier services
   without state, units that no module defines, and units outside `units` that would
   start a gated unit at boot (a helper service, a timer or a socket).
7. **Update docs**:
   - `docs/architecture.md` — auth matrix + hostname map
   - `docs/secure-layers.md` — add to the correct tier table
   - `docs/backup.md` — if the service has state that must be backed up
   - `docs/failure-modes.md` — add to "stays up" or "pauses" list
   - `docs/storage-layout.md` — add to the correct state section and service table
   - `docs/deployment-checklist.md` — if post-install steps are needed

---

## Patterns to follow

### Deployment settings

Deployment-specific values live in `deployments/<profile>/deploy.nix` (gitignored for
real sites) and are injected as `config.lanbat.deployment.*` and `config.lanbat.hosts.*`.
See [docs/extensibility.md](docs/extensibility.md) and [docs/migration.md](docs/migration.md).

When adding a deployment-time value:

1. Add the option to `modules/core/settings.nix` under `lanbat.deployment` or the host submodule.
2. Add a placeholder to `deployments/example/deploy.nix`.
3. Add the entry to `deployments/homelab/deploy.nix.example`.
4. Reference it as `config.lanbat.deployment.<option>` or `config.lanbat.hosts.<key>.<option>`.

| Option | Used for |
|---|---|
| `config.lanbat.deployment.domain` | All service hostnames |
| `config.lanbat.deployment.serverIp` | Primary server IPv4 (computed) |
| `config.lanbat.deployment.storageIp` | Primary storage-pi IPv4 (computed) |
| `config.lanbat.deployment.gatewayIp` | Default gateway |
| `config.lanbat.hosts.<key>.networking.ip` | Per-host static address |
| `config.lanbat.hosts.<key>.disks.system` | Server system disk path |
| `config.lanbat.hosts.<key>.storage.drives` | Pi NVMe by-id filenames, keyed by drive name |
| `config.lanbat.deployment.voiceRooms` | Area name → host key for voice satellites |
| `config.lanbat.deployment.dns` | LAN DNS for the `lanbat-dns` plugin: short suffix, upstreams, extra records |
| `config.lanbat.deployment.androidDevices` | Android TV boxes to provision over ADB |
| `config.lanbat.deployment.voiceCompute.profile` | Where the voice assistant's heavy work runs: `low-spec` or `apple-silicon` (`lib/voice-compute.nix`) |

For the domain specifically, the common pattern in service files is:
```nix
let domain = config.lanbat.deployment.domain; in
```

### Service tiers

Services run in one of two tiers. Assign the tier based on data sensitivity and
availability requirements:

**Always-on** (the default; start at boot, data on the unencrypted host root):
- The service starts without any LUKS unlock and NixOS manages `/var/lib/<name>` normally.
- Current members: Caddy, PostgreSQL (always-on instance), Redis, Authentik, Home Assistant, Grafana, InfluxDB,
  Mosquitto, Zigbee2MQTT, Frigate, Music Assistant, Snapcast, Wyoming pipeline, speaker identification (voice-id), assistant router, who is home (wifi-presence), LLM gateway, SearXNG, Telegraf, Homepage,
  CoreDNS (`lanbat-dns` plugin)

**Workload-gated** (start only after `unlock-workload`, data on encrypted LUKS):
- Set `tier = "workload"`, list the `/var/lib` directories in `state` and the systemd
  units in `units`. The wiring creates the mode-0000 stubs, bind-mounts
  `/mnt/workload/<dir>` over them, and moves the units under `workload-online.target`.
- Current members: Nextcloud, Immich, Jellyfin, Audiobookshelf, Vaultwarden, Syncthing,
  Samba, qBittorrent, Bitmagnet, RomM, PostgreSQL (workload instance)

When in doubt, prefer **always-on** for monitoring/automation/infrastructure services
and **workload-gated** for personal data vaults (passwords, photos, documents, media).

### Prefer NixOS-native services over containers
Use `services.<name>` when a good NixOS module exists.
Use `virtualisation.oci-containers` only when necessary (e.g. Authentik, Immich, Frigate).

### Databases
Prefer PostgreSQL over a service's own SQLite file when the service supports it. There are
two instances, one per tier (`services/postgresql.nix`); put a database on the instance
matching its service's tier, so workload data never lands on the unencrypted host root:
```nix
lanbat.postgresql.databases.<name> = {
  instance = "always-on";   # or "workload"
  # passwordFile = config.lanbat.secrets.<name>-env.path;   # only for TCP logins (containers)
};
```
This creates a database and owner role named `<name>`. A NixOS-native service running as
the system user `<name>` logs in over the socket without a password; take the socket, port
and the unit to order after from `config.lanbat.postgresql.instances.<instance>`.

A service using the shared Redis (`services/redis.nix`) claims a database index of its
own and reads it back rather than repeating the number; evaluation rejects two services
claiming the same index:
```nix
lanbat.redis.databases.<name>.index = 3;   # 0 Authentik, 1 Immich, 2 RomM
# REDIS_DB = toString config.lanbat.redis.databases.<name>.index;
```

### Rootless containers
Give each container service its own account:
```nix
lanbat.services.<name>.account = { uid = 9xx; container = true; };
```
Pick a free UID in 900–999 (evaluation rejects clashes). Then set `podman.user = "<name>"`
on the container and `user = "0"` (or `--uidmap` for images that run as non-root
internally), and own its data directories with the account. Sub-UID/GID ranges are
derived from the UID.

### NFS-dependent services
Any service that reads/writes Pi storage (`/srv/storage/<drive>`, where the drives are the
keys of the storage host's `storage.drives`, `a` and `b` in the default layout) declares it:
```nix
lanbat.services.<name>.nfs = { drives = [ "a" ]; storageHost = "pi-storage"; };
```
Its `units` then bind to the NFS mounts, stop when Pi storage disappears and restart when
it comes back. Use `nfs.units` when only some units touch the storage. The unit name of a
container is `podman-<container-name>`; for NixOS-native services use the actual unit
name (e.g. `samba-smbd`, not `samba`).

**Soft dependency exception:** Nextcloud keeps bulk user data on Pi storage through the
External Storage app but deliberately omits `nfs.drives`. Hard-binding would stop the
entire app (including OIDC login) when NFS drops; external folders fail individually
instead. Document the reason in the service file if you add another soft dependency.

### Secrets
- **Declare a requirement, read through the accessor.** A service lists the secrets it
  needs in `lanbat.services.<name>.secrets` (owner, group, mode); it never says where
  the file comes from. The profile's provider (`deployment.secrets.provider`) satisfies
  each requirement, and the service reads the decrypted file as
  `config.lanbat.secrets.<secret>.path`, never through `config.age.secrets`. Wiring
  (`modules/wiring/secrets.nix`) does the rest: under `agenix` it decrypts
  `<deployment.secrets.root>/<secret>.age`, under `none` it resolves to a placeholder.
- **A missing provision fails evaluation by name.** Under `agenix`, a requirement with no
  `<secret>.age` in the profile's secrets root stops evaluation with a message naming
  the secret, the service that requires it and the host.
- **Conditional requirements.** A secret that only a feature uses is required only when
  that feature is on: set the requirement's `enable` from the setting, e.g.
  `ha-llm-api-key = { enable = llm != null; owner = "hass"; }`. A requirement that is
  off is not provisioned, needs no file and has no `lanbat.secrets` entry, so guard
  its reads with the same condition.
- **Shared secrets.** A secret belongs to the one service that declares it, which sets its
  owner, group and mode. Another service on the same host that reads it (Frigate and
  Zigbee2MQTT read their `mosquitto-*-pass`; Jellyfin, Immich and Music Assistant read
  `hass-bootstrap-env`) lists it in `lanbat.services.<name>.readsSecrets`, as
  conditionally as it reads it, and reads `config.lanbat.secrets.<secret>.path` from a unit
  the declaration lets read the file. Evaluation fails, naming both services, when nothing
  on the host declares it or its requirement is off.
- **Host secrets:** a secret that belongs to a host rather than a service (the overlay
  key) is declared in `lanbat.hostSecrets.<secret>` and read the same way.
- **Recipients are derived.** `nix run .#secrets-recipients [--json] [profile]` prints,
  for every secret a host of the profile requires, `admin` and the host keys that need it
  (from where the requirements are placed), as the body of an agenix `secrets.nix`.
- Add every new secret to `secrets/secrets.nix.example` and `secrets/README.md`.
- Inject secrets at runtime via `environmentFile` or `config.lanbat.secrets.<name>.path` —
  never inline plaintext in Nix expressions.
- Only declare a secret that the service actually reads.
- A service running as a systemd `DynamicUser` has no fixed account to own a secret.
  Declare the secret with `owner = "root"` and hand it over with
  `LoadCredential = [ "<secret>:${config.lanbat.secrets.<secret>.path}" ]`; the
  service reads `$CREDENTIALS_DIRECTORY/<secret>` (`modules/pi/stackchan.nix`).
- For environment variable injection into NixOS-native services, set:
  ```nix
  systemd.services.<name>.serviceConfig.EnvironmentFile = [
    config.lanbat.secrets.<name>-env.path
  ];
  ```

### Ports
Evaluation rejects two services claiming the same `port`, `extraPorts` entry or on-demand
activator port. List the current allocation with:
```bash
nix eval --json .#nixosConfigurations.example-server.config.lanbat.services \
  --apply 'builtins.mapAttrs (_: s: { inherit (s) port extraPorts; })'
```
On the Pi: 2049 (NFS) and 10700 (Wyoming satellite), both restricted to the server.

### Caddy auth
- Services with **native OIDC** (Nextcloud, Immich, Grafana): `auth = "app"`.
- Services with **no auth** of their own (Frigate, qBittorrent, Bitmagnet, RomM): `auth = "forward-auth"`.
- Services with **their own account system** (Vaultwarden, Jellyfin): `auth = "app"` and
  `apiClients = true` — clients need direct API access.
- Deliberately open services (SearXNG, Homepage, the CA page): `auth = "none"`.

### systemd.tmpfiles.rules
Never declare `systemd.tmpfiles.rules` twice in the same `.nix` file — Nix will
throw a duplicate attribute error. Merge all rules into a single list.

---

## Things to avoid

- **Hardcoding the domain** — use `config.lanbat.deployment.domain`
- **Wiring a service by hand** — Caddy vhosts, workload stubs and bind mounts, NFS
  dependencies, service accounts and secrets come from `lanbat.services.<name>`
- **Declaring unused secrets** — only declare what a service actually uses
- **Using `linux_rpi4` kernel packages on the Pi 5** — use `boot.kernelModules`
  for kernel module loading instead of package references
- **Targeting the wrong samba unit** — the file-serving unit is `samba-smbd`,
  not `samba`
- **Duplicate `systemd.tmpfiles.rules` blocks** in the same file
- **Committing plaintext secrets** — all secrets go in `.age` files only
- **tmpfiles rules under a workload `state` directory** — the directory is a stub
  while the layer is locked; use `workloadDirs` instead
