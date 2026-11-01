# tests/plugins.nix
#
# Pure eval checks for the lanbat plugin contract, including the service
# selection that lets a host import only some of what a plugin offers. The
# fixtures without a version comment are contract version 1, which still loads.
{ lib, pkgs }:

let
  pluginLib = import ../lib/plugins.nix { inherit lib; };
  rolesLib = import ../lib/roles.nix { inherit lib; };

  badPlugin = {
    name = "broken";
    version = 1;
    roles = [ "server" ];
    modules = [ ];
  };

  wrongRolePlugin = {
    name = "pi-only";
    version = 1;
    roles = [ "storage-pi" ];
    modules = [
      ({ ... }: { })
    ];
  };

  # resolvePlugins never looks inside a module, so a marker string stands in for
  # one and makes the resolved list easy to compare.
  offering = name: services: {
    inherit name services;
    version = 1;
    roles = [ "server" ];
    modules = lib.attrValues services;
  };

  media = offering "media" {
    jellyfin = "jellyfin-module";
    immich = "immich-module";
  };

  infra = offering "infra" {
    caddy = "caddy-module";
  };

  # No services attribute: all-or-nothing, so a selection must not shrink it.
  monolith = {
    name = "monolith";
    version = 1;
    roles = [ "server" ];
    modules = [
      "monolith-a"
      "monolith-b"
    ];
  };

  # Version 2 with modules only: nothing to select, always imported.
  modulesOnly = {
    name = "modules-only";
    version = 2;
    roles = [ "server" ];
    modules = [ "modules-only-module" ];
  };

  clashing = offering "other-media" {
    jellyfin = "a-different-jellyfin";
  };

  # Version 2: modules are always imported, services are selectable.
  v2 = {
    name = "v2-media";
    version = 2;
    roles = [ "server" ];
    modules = [ "v2-common" ];
    services = {
      jellyfin = "v2-jellyfin";
      immich = "v2-immich";
    };
  };

  v2ServicesOnly = {
    name = "v2-infra";
    version = 2;
    roles = [ "server" ];
    services.caddy = "v2-caddy";
  };

  withSettings = name: settings: {
    inherit name settings;
    version = 2;
    roles = [ "server" ];
    modules = [ "${name}-module" ];
  };

  parking = withSettings "parking" {
    parkingDemo =
      lib:
      lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
      };
  };

  parkingRival = withSettings "rival" {
    parkingDemo = lib: lib.mkOption { type = lib.types.str; };
  };

  # Evaluates the namespaces as a host would see them, with a deploy value.
  evalSettings =
    plugins: deployment:
    (lib.evalModules {
      modules = pluginLib.settingsModules plugins ++ [ { lanbat.deployment = deployment; } ];
    }).config.lanbat.deployment;

  resolve = pluginLib.resolvePlugins "server";

  # A plugin that adds a host role. Markers stand in for modules.
  withRole = extra: {
    name = "nas";
    version = 2;
    roles = [ "nas" ];
    hostRoles.nas = {
      modules = [
        {
          name = "role";
          module = "nas-role";
        }
        {
          name = "exports";
          module = "nas-exports";
        }
      ];
      requirements = host: lib.optional (!(host ? nasDisk)) "nas role requires nasDisk";
    }
    // extra;
  };
  nasPlugin = withRole { };
  nasRoles = pluginLib.roleTable [ nasPlugin ];

  # lib/local-modules.nix, against a tracked copy of the gitignored layout.
  localModules = (import ../lib/local-modules.nix { inherit lib; }).localModules;
  localRoot = ./fixtures/local-modules;
  localNames =
    profile: host:
    map (p: lib.removePrefix "${toString localRoot}/" (toString p)) (
      localModules localRoot profile host
    );

  expectThrow =
    name: thunk:
    if (builtins.tryEval thunk).success then "expected ${name} to throw, but it succeeded" else null;

  expect = name: cond: if cond then null else "FAIL: ${name}";

  sorted = lib.sort (a: b: a < b);

  cases = [
    (expect "a plugin can add a host role alongside the built-in ones" (
      lib.attrNames nasRoles == [
        "nas"
        "server"
        "storage-pi"
        "voice-pi"
      ]
    ))
    (expect "a plugin role's modules are imported in order" (
      rolesLib.resolveRoleModules nasRoles "nas" { } == [
        "nas-role"
        "nas-exports"
      ]
    ))
    (expect "a plugin role's modules can be overridden by name" (
      rolesLib.resolveRoleModules nasRoles "nas" { exports = null; } == [ "nas-role" ]
    ))
    (expect "a plugin role's requirements are checked" (
      rolesLib.requirementErrors nasRoles { role = "nas"; } == [ "nas role requires nasDisk" ]
    ))
    (expectThrow "a plugin role not listed in the plugin's roles" (
      pluginLib.validatePlugin (nasPlugin // { roles = [ "server" ]; })
    ))
    (expectThrow "a plugin role with unnamed modules" (
      pluginLib.validatePlugin (withRole {
        modules = [ "nas-role" ];
      })
    ))
    (expectThrow "a plugin role with an unknown field" (
      pluginLib.validatePlugin (withRole {
        hardware = [ ];
      })
    ))
    (expectThrow "a plugin role redefining a built-in role" (
      pluginLib.roleTable [
        (
          nasPlugin
          // {
            roles = [ "server" ];
            hostRoles.server = nasPlugin.hostRoles.nas;
          }
        )
      ]
    ))
    (expectThrow "two plugins declaring the same role" (
      pluginLib.roleTable [
        nasPlugin
        (nasPlugin // { name = "other-nas"; })
      ]
    ))
    (expectThrow "a version 1 plugin declaring a host role" (
      pluginLib.validatePlugin (
        nasPlugin
        // {
          version = 1;
          modules = [ "x" ];
        }
      )
    ))
    (expectThrow "empty modules" (pluginLib.validatePlugin badPlugin))
    (expectThrow "incompatible role" (resolve [ wrongRolePlugin ] [ ]))

    (expect "an empty selection takes everything a plugin offers" (
      sorted (resolve [ media ] [ ]) == [
        "immich-module"
        "jellyfin-module"
      ]
    ))

    (expect "a modules-only plugin is imported alongside a selection" (
      sorted (resolve [ media modulesOnly ] [ "jellyfin" ]) == [
        "jellyfin-module"
        "modules-only-module"
      ]
    ))

    (expect "a selection takes only the named services" (
      resolve [ media ] [ "jellyfin" ] == [ "jellyfin-module" ]
    ))

    (expect "a selection spanning two plugins takes from both" (
      sorted (
        resolve
          [ media infra ]
          [
            "caddy"
            "immich"
          ]
      ) == [
        "caddy-module"
        "immich-module"
      ]
    ))

    (expect "a plugin with no services attribute ignores a selection" (
      sorted (resolve [ media monolith ] [ "jellyfin" ]) == [
        "jellyfin-module"
        "monolith-a"
        "monolith-b"
      ]
    ))

    (expectThrow "a service no enabled plugin offers" (resolve [ media ] [ "nonexistent" ]))

    (expectThrow "two plugins offering the same service" (resolve [ media clashing ] [ "jellyfin" ]))

    # ── Contract version 2 ────────────────────────────────────────────────
    (expect "v2: an empty selection takes the modules and every service" (
      sorted (resolve [ v2 ] [ ]) == [
        "v2-common"
        "v2-immich"
        "v2-jellyfin"
      ]
    ))

    (expect "v2: a selection keeps the modules and takes only the named services" (
      sorted (resolve [ v2 ] [ "jellyfin" ]) == [
        "v2-common"
        "v2-jellyfin"
      ]
    ))

    (expect "v2: a plugin may offer services and no modules" (
      resolve [ v2ServicesOnly ] [ ] == [ "v2-caddy" ]
    ))

    (expect "v1 and v2 plugins load side by side" (
      sorted (
        resolve
          [ media v2ServicesOnly ]
          [
            "caddy"
            "immich"
          ]
      ) == [
        "immich-module"
        "v2-caddy"
      ]
    ))

    (expectThrow "v2: neither modules nor services" (
      pluginLib.validatePlugin {
        name = "empty";
        version = 2;
        roles = [ "server" ];
      }
    ))

    (expectThrow "v2: an unknown field" (pluginLib.validatePlugin (v2 // { module = [ ]; })))

    (expectThrow "v2: a setting that is not a function of lib" (
      pluginLib.validatePlugin (withSettings "bad" { demo = { }; })
    ))

    (expectThrow "v1: settings need version 2" (
      pluginLib.validatePlugin (monolith // { settings.demo = lib: lib.mkOption { }; })
    ))

    (expectThrow "an unsupported version" (pluginLib.validatePlugin (v2 // { version = 3; })))

    (expectThrow "a missing version" (pluginLib.validatePlugin (removeAttrs v2 [ "version" ])))

    (expect "legacyPlugins names the version 1 plugins" (
      pluginLib.legacyPlugins [
        media
        v2
        monolith
      ] == [
        "media"
        "monolith"
      ]
    ))

    # ── Settings namespaces ───────────────────────────────────────────────
    (expect "a plugin's namespace is declared under lanbat.deployment" (
      (evalSettings [ parking ] { parkingDemo = [ "AB12CDE" ]; }).parkingDemo == [ "AB12CDE" ]
    ))

    (expect "a namespace takes its default when the deploy file leaves it out" (
      (evalSettings [ parking ] { }).parkingDemo == [ ]
    ))

    (expect "the same plugin on two hosts declares its namespace once" (
      (evalSettings [ parking parking ] { }).parkingDemo == [ ]
    ))

    (expectThrow "a key no plugin declares" (
      (evalSettings [ parking ] { parkingDeom = [ ]; }).parkingDemo
    ))

    (expectThrow "two plugins declaring one namespace" (
      pluginLib.settingsModules [
        parking
        parkingRival
      ]
    ))

    # ── Local modules ─────────────────────────────────────────────────────
    (expect "local modules: profile-wide files in name order, then the host's" (
      localNames "demo" "server" == [
        "deployments/demo/local/10-a.nix"
        "deployments/demo/local/20-b.nix"
        "deployments/demo/local/hosts/server/host.nix"
      ]
    ))

    (expect "local modules: a host without a directory gets the profile-wide ones" (
      localNames "demo" "voice" == [
        "deployments/demo/local/10-a.nix"
        "deployments/demo/local/20-b.nix"
      ]
    ))

    (expect "local modules: a profile without local/ contributes nothing" (
      localModules localRoot "example" "server" == [ ]
    ))

    (expect "offeredServices merges what every plugin offers" (
      sorted (
        lib.attrNames (
          pluginLib.offeredServices [
            media
            infra
          ]
        )
      ) == [
        "caddy"
        "immich"
        "jellyfin"
      ]
    ))
  ];

  failures = lib.filter (x: x != null) cases;
in
pkgs.runCommand "plugins-check" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "plugin contract checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
