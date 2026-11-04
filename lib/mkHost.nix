# lib/mkHost.nix
#
# Builds one nixosSystem from a deploy host entry.
{
  lib,
  inputs,
  agenix,
  disko,
  nixos-raspberrypi,
  profileName,
  deployment,
  hosts,
  hostName,
  hostCfg,
  # Profile-wide service table from lib/default.nix. Empty during the first,
  # descriptions-only pass that produces it.
  endpoints ? { },
  # Modules from the deployment's gitignored local/ directory
  # (lib/local-modules.nix).
  localModules ? [ ],
}:

let
  inherit (import ./plugins.nix { inherit lib; })
    resolvePlugins
    settingsModules
    roleTable
    legacyPlugins
    ;
  inherit (import ./roles.nix { inherit lib; }) resolveRoleModules;
  platformsLib = import ./platforms.nix { inherit lib; };

  # The machine the host runs on (lib/platforms.nix); null for a generic one.
  platformInfo = platformsLib.resolve hostCfg;
  system = hostCfg.system;

  pluginModules = resolvePlugins hostCfg.role (hostCfg.plugins or [ ]) (hostCfg.services or [ ]);

  # Deployment settings are profile-wide, so every host declares the namespaces
  # of every plugin enabled anywhere in the profile.
  pluginSettingsModules = settingsModules (
    lib.concatMap (h: h.plugins or [ ]) (lib.attrValues hosts)
  );

  legacy = legacyPlugins (hostCfg.plugins or [ ]);

  # Merged last of all, so a deployment overrides anything core, the role or
  # a plugin set without having to edit a tracked file. The deploy entry's own
  # modules come first, then the ones found in the deployment's local/.
  userModules = (hostCfg.modules or [ ]) ++ localModules;

  hostContextModule =
    { ... }:
    {
      lanbat.profile = profileName;
      lanbat.hostKey = hostName;
      lanbat.deployment = deployment;
      lanbat.endpoints = endpoints;
      lanbat.hosts = lib.mapAttrs (name: host: {
        role = host.role;
        networking = host.networking;
        disks = host.disks or { };
        storage = host.storage or { };
        overlay = host.overlay or null;
        # Needed by the endpoint wiring to work out which host runs a service.
        services = host.services or [ ];
        # Read statically by other hosts (Home Assistant finds the Kodi hosts).
        pluginNames = map (p: p.name or "") (host.plugins or [ ]);
      }) hosts;

      warnings = map (
        name:
        "lanbat plugin '${name}' uses contract version 1. It still loads; see"
        + " docs/plugins.md for moving it to version 2."
      ) legacy;
    };

  # Chosen from deploy data, which exists before any evaluation, so the module
  # can simply be imported rather than selected inside the configuration.
  overlayProviders = import ./overlay-providers.nix;
  overlayName = deployment.overlay.provider or "none";
  overlayModule =
    overlayProviders.${overlayName} or (builtins.throw (
      "lanbat: no overlay provider named \"${overlayName}\". Available: "
      + lib.concatStringsSep ", " (lib.attrNames overlayProviders)
      + "."
    ));

  commonModules = [
    agenix.nixosModules.default
    {
      nixpkgs.config = {
        allowUnfree = true;
        # The Android SDK's own Google license, for the android-presence APK
        # (pkgs/android-presence, built when a device declares `presence`):
        # Nix refuses to build the SDK without this. Here, not in the
        # android module: the assertions check evaluates the modules without
        # the `nixpkgs` option module.
        android_sdk.accept_license = true;
      };
    }
    ../modules/core
    hostContextModule
  ]
  ++ [ overlayModule ]
  # The role's bundled modules, less any the deploy entry drops or replaces
  # in roleModules (see lib/roles.nix). The role may be one that a plugin of
  # this host declares.
  ++ resolveRoleModules (roleTable (hostCfg.plugins or [ ])) hostCfg.role (hostCfg.roleModules or { })
  ++ pluginSettingsModules
  ++ pluginModules;

  # The hardware the platform brings, or nothing on a generic machine. A
  # deploy entry's hardware replaces it: a module, a list of them, or [ ] for
  # none.
  platformHardware = lib.optionals (platformInfo != null) [ platformInfo.hardware ];
  hardwareModules =
    if !(hostCfg ? hardware) then
      platformHardware
    else if hostCfg.hardware == null then
      [ ]
    else
      lib.toList hostCfg.hardware;

  genericModules =
    commonModules
    ++ hardwareModules
    ++ lib.optionals (hostCfg.role == "server") [
      disko.nixosModules.disko
    ];

  modules = genericModules ++ userModules;

  nixosSystem =
    if platformInfo.nixosRaspberrypi or false then
      nixos-raspberrypi.lib.nixosSystem {
        specialArgs = {
          inherit inputs nixos-raspberrypi;
          lanbatHostName = hostName;
        };
        inherit modules;
      }
    else
      lib.nixosSystem {
        inherit system modules;
        specialArgs = {
          inherit inputs;
          lanbatHostName = hostName;
        };
      };
in
nixosSystem
// {
  lanbatModules = modules;
}
