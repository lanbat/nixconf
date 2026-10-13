# Plugin author guide

Third-party features ship as flake inputs that export a `lanbatPlugin` attribute.

## Minimal plugin

A copy-pasteable template lives in
[`examples/lanbat-plugin-minimal/`](../examples/lanbat-plugin-minimal/).

```nix
# flake.nix outputs
{
  lanbatPlugin = {
    name = "lanbat-media";
    version = 2;
    roles = [ "server" ];
    services = {
      jellyfin = ./services/jellyfin.nix;
      immich = ./services/immich.nix;
    };
  };
}
```

## Contract (version 2)

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique plugin identifier |
| `version` | yes | Contract version: `2` |
| `roles` | yes | Host roles that may enable this plugin |
| `modules` | one of `modules`, `services` | NixOS modules imported on every host that enables the plugin |
| `services` | one of `modules`, `services` | Service name → module. These enter the placement registry that `hosts.<key>.services` selects from |
| `settings` | no | Deployment namespace → `lib: lib.mkOption { ... }`, declared as `lanbat.deployment.<namespace>` |
| `hostRoles` | no | Role name → `{ modules; requirements; }`: a host role the plugin adds (see [Host roles](#host-roles)) |

Validation happens at evaluation time in `lib/plugins.nix`, and each error names
the plugin:

- A plugin whose `roles` do not include the host's role fails evaluation.
- A plugin with no modules, services or host roles fails evaluation.
- An unknown field (a typo such as `module`) fails evaluation.
- A version other than 1 or 2 fails evaluation.
- Two plugins offering the same service, declaring the same settings
  namespace or the same host role, fail evaluation, as does a host role that
  redefines a built-in one or is missing from the plugin's `roles`. A namespace core already declares fails with the
  module system's "already declared" error, which names the plugin.

### Services and placement

A host lists the services it runs in `hosts.<key>.services`. Each name must be
offered by one of the host's plugins; the host then imports the plugin's
`modules` and the modules of the services it names. A host that names no
services takes every service its plugins offer.

```nix
hosts.server = {
  plugins = [ inputs.self.lanbatPlugins.services inputs.lanbat-media.lanbatPlugin ];
  services = [ "caddy" "authentik" "jellyfin" ];   # not immich
};
```

### Settings namespaces

A plugin that needs deployment-wide values declares its own namespace instead
of asking for an option in `modules/core/settings.nix`:

```nix
lanbatPlugin = {
  name = "lanbat-parking";
  version = 2;
  roles = [ "server" ];
  modules = [ ./module.nix ];
  settings.parking = lib: lib.mkOption {
    type = lib.types.nullOr (lib.types.submodule {
      options.siteId = lib.mkOption { type = lib.types.str; };
    });
    default = null;
    description = "Parking guard settings.";
  };
};
```

The deploy file sets `deployment.parking = { siteId = "…"; };` and the plugin's
modules read `config.lanbat.deployment.parking`. Deployment settings are
profile-wide, so the namespace is declared on every host of a profile in which
any host enables the plugin; a host without the plugin accepts the value and
ignores it. Give the option a default (`null` or `{ }`) so a profile that
enables the plugin without configuring it still evaluates.

### Host roles

A plugin can add a host role next to `server`, `storage-pi` and `voice-pi`. It
declares the role in `hostRoles`, in the shape `lib/roles.nix` uses for the
built-in ones, and lists it in `roles` too:

```nix
lanbatPlugin = {
  name = "lanbat-nas";
  version = 2;
  roles = [ "nas" ];
  hostRoles.nas = {
    # In import order. Each has a name, so a host can replace or drop it with
    # hosts.<key>.roleModules.
    modules = [
      { name = "role"; module = ./role.nix; }
      { name = "exports"; module = ./exports.nix; }
    ];
    # The deploy entry → what is wrong with it, as messages. Optional.
    requirements = host:
      lib.optional (!(host ? nas.disk)) "nas role requires nas.disk";
  };
};
```

A host takes the role by enabling the plugin that declares it:

```nix
hosts.nas = {
  role = "nas";
  plugins = [ inputs.lanbat-nas.lanbatPlugin ];
  nas.disk = "…";
  # system, networking, platform as for any host
};
```

`lib/validate-deploy.nix` runs the role's `requirements` along with the built-in
roles' own, and a role no plugin of the host declares fails validation. The
role module can start from what every built-in role shares, the hostname,
static address and firewall baseline, by importing
`inputs.lanbat.nixosModules.lanbat-role-common`. The Raspberry Pi hardware comes
with `platform = "raspberry-pi-3"`, `"raspberry-pi-4"` or `"raspberry-pi-5"`, whatever the role.

### Service settings

A service's own configuration lives in `lanbat.services.<name>.settings`. To
have typos rejected rather than silently ignored, declare the keys the service
accepts:

```nix
lanbat.settingsSchema.jellyfin = {
  options.transcodeThreads = lib.mkOption {
    type = lib.types.ints.positive;
    description = "Threads for transcoding.";
  };
};
lanbat.services.jellyfin.settings.transcodeThreads = lib.mkDefault 4;
```

Once a service declares any key, `modules/wiring/checks.nix` rejects any other
key, naming the service and the key. A service that declares none stays
freeform. A service that models some keys and renders the rest into its
configuration unchanged sets `settingsFreeform = true`.

### Migrating from version 1

Version 1 plugins still load, with their old meaning (`modules` is everything
the plugin contributes; an optional `services` attribute is the subset a host
can select by name), and the host that enables one gets an evaluation warning.
To move to version 2:

1. Set `version = 2`.
2. Move each service module out of `modules` into `services.<name>`. What is
   left in `modules` is imported whether or not the host selects any service.
3. Move any deployment options you asked core to declare into `settings`.

A version 1 plugin that sets `settings` fails with an error asking for
`version = 2`.

## Service modules

Server plugins should declare services under `lanbat.services.<name>` using the interface in `modules/core/services.nix`. The wiring modules (`modules/wiring/`) generate Caddy vhosts, LUKS gating, NFS dependencies, accounts, and secrets from those declarations.

Some wiring comes with the host's role rather than with the service: on-demand
activation and workload gating are imported by the server role only (and a host
can drop them with `roleModules`; see
[extensibility.md](extensibility.md#replacing-a-roles-bundled-modules)). A service
with `onDemand` or `tier = "workload"` placed on another role's host fails
evaluation instead of starting at boot or keeping its state on the host root.

A service with a subdomain may run on a host without Caddy: Caddy proxies its
vhost to that host, and the host's generated firewall policy admits Caddy's host
(see [architecture.md](architecture.md#services-on-another-host) for what this
rejects, such as a remote on-demand service).

## Consuming a plugin

```nix
# flake.nix
inputs.lanbat-media.url = "github:you/lanbat-media";
inputs.lanbat-media.inputs.nixpkgs.follows = "nixpkgs";

# deployments/homelab/deploy.nix
hosts.server.plugins = [
  inputs.self.lanbatPlugins.services
  inputs.lanbat-media.lanbatPlugin
];
```

## Secrets

Plugins use the repo's agenix `secrets/` directory. If your plugin needs new
secrets:

1. Declare them under `lanbat.services.<name>.secrets` (or `lanbat.hostSecrets`
   for secrets that belong to no service), and read each one as
   `config.lanbat.secrets.<secret>.path`, never `config.age.secrets`. Set a
   requirement's `enable` from the setting that uses it, and list a secret that
   another service declares (a Mosquitto password, say) in
   `lanbat.services.<name>.readsSecrets` rather than declaring it again.
2. Add entries to `secrets/secrets.nix.example` and the inventory in
   `secrets/README.md`. `nix run .#secrets-recipients` shows which hosts need them.
3. **In your pull request:** document the required secrets in your plugin
   README (format, how to generate values). No `.age` file is needed — the
   `example` profile resolves secrets to placeholders, so evaluation and the
   checks pass without one.
4. **Before merge:** the lanbat maintainer creates the real `.age` files
   encrypted to the deployment host keys (see
   [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Exported modules

The core flake also exports reusable NixOS modules for plugin authors:

```nix
inputs.lanbat.url = "github:lanbat/nixconf";
# inputs.lanbat.inputs.nixpkgs.follows = "nixpkgs";

imports = [
  inputs.lanbat.nixosModules.lanbat
];
```

Available modules: `lanbat`, `lanbat-server`, `lanbat-storage-pi`, `lanbat-voice-pi`,
`lanbat-role-common` (what every built-in role shares) and `lanbat-android`.
