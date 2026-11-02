# lib/roles.nix
#
# The host roles, as a table: for each role, the modules it bundles in the
# order lib/mkHost.nix imports them, and what it requires of a host's deploy
# entry, which lib/validate-deploy.nix checks. A plugin adds a role by
# declaring it under hostRoles, in the same shape (lib/plugins.nix,
# docs/plugins.md); a host whose plugins declare a role may take it.
#
#   <role> = {
#     modules = [ { name = "role"; module = ./role.nix; } ... ];
#     # Deploy entry → the problems with it, as messages; [ ] when it is fine.
#     requirements = host: [ ];
#   };
#
# Every bundled module has a name, so a host can replace or drop it from its
# deploy entry without editing this file:
#
#   hosts.server.roleModules = {
#     wiring-caddy = null;               # drop the Caddy wiring
#     backups = ./my-backups.nix;        # replace the backup module
#   };
#
# A replacement takes the place of the module it replaces, so the rest keep
# their order. Naming a module the role does not bundle fails evaluation.
{ lib }:

let
  root = ../.;

  bundle = name: module: { inherit name module; };

  nonEmptyString =
    path: value:
    lib.optional (!(lib.isString value) || value == "") "${path} must be a non-empty string";

  builtinRoles = {
    server = {
      modules = [
        (bundle "role" (root + "/lib/roles/server.nix"))
        (bundle "hardware" (root + "/hosts/server/hardware.nix"))
        (bundle "disk" (root + "/hosts/server/disk.nix"))
        (bundle "control-layer" (root + "/modules/server/control-layer.nix"))
        (bundle "backups" (root + "/modules/server/backups.nix"))
        (bundle "wiring-caddy" (root + "/modules/wiring/caddy.nix"))
        (bundle "wiring-nfs" (root + "/modules/wiring/nfs.nix"))
        (bundle "wiring-on-demand" (root + "/modules/wiring/on-demand.nix"))
        (bundle "wiring-workload-gate" (root + "/modules/wiring/workload-gate.nix"))
      ];
      requirements =
        host:
        if !(host ? disks) || !(host.disks ? system) then
          [ "server role requires disks.system" ]
        else
          nonEmptyString "disks.system" host.disks.system;
    };

    storage-pi = {
      modules = [
        (bundle "role" (root + "/lib/roles/storage-pi.nix"))
        (bundle "clevis-unlock" (root + "/modules/storage/clevis-unlock.nix"))
        (bundle "nfs-exports" (root + "/modules/storage/nfs-exports.nix"))
        (bundle "storage" (root + "/modules/storage/storage.nix"))
        (bundle "jellyfin-watch" (root + "/modules/storage/jellyfin-watch.nix"))
        (bundle "user-quotas" (root + "/modules/storage/user-quotas.nix"))
        (bundle "snapclient" (root + "/modules/pi/snapclient.nix"))
        (bundle "telegraf" (root + "/modules/pi/telegraf.nix"))
      ];
      requirements =
        host:
        let
          drives = (host.storage or { }).drives or { };
          # A drive's key names its unlock unit, LUKS mapper, mount point and
          # NFS mount (storage-<key>-unlock, /mnt/storage-<key>,
          # /srv/storage/<key>). systemd escapes a "-" in a mount path, so the
          # mount unit would no longer be srv-storage-<key>.mount; keep keys to
          # letters and digits.
          badKeys = lib.filter (key: builtins.match "[a-z0-9]+" key == null) (lib.attrNames drives);
        in
        if !(lib.isAttrs drives) || drives == { } then
          [ "storage-pi role requires at least one entry in storage.drives" ]
        else if badKeys != [ ] then
          [
            "storage.drives keys must be lowercase letters and digits: ${lib.concatStringsSep ", " badKeys}"
          ]
        else
          lib.concatMap (key: nonEmptyString "storage.drives.${key}" drives.${key}) (lib.attrNames drives);
    };

    voice-pi = {
      modules = [
        (bundle "role" (root + "/lib/roles/voice-pi.nix"))
        (bundle "audio" (root + "/modules/pi/audio.nix"))
        (bundle "speakers" (root + "/modules/pi/speakers.nix"))
        (bundle "snapclient" (root + "/modules/pi/snapclient.nix"))
        (bundle "telegraf" (root + "/modules/pi/telegraf.nix"))
      ];
    };
  };

  # The built-in roles and those the given (validated) plugins declare. A role
  # is declared once: a plugin may not redefine a built-in role or one another
  # plugin declares.
  withPluginRoles =
    plugins:
    lib.foldl' (
      acc: p:
      let
        declared = p.hostRoles or { };
        clashing = lib.attrNames (lib.intersectAttrs declared acc);
      in
      if clashing != [ ] then
        builtins.throw (
          "lanbat plugin '${p.name}' declares host role(s) that already exist: "
          + lib.concatStringsSep ", " clashing
        )
      else
        acc // declared
    ) builtinRoles plugins;

  roleOf =
    roles: role:
    if !(roles ? ${role}) then
      builtins.throw "unknown lanbat host role '${role}'; known roles: ${lib.concatStringsSep ", " (lib.attrNames roles)}"
    else
      roles.${role};

  # What a deploy entry lacks for its role, as messages; [ ] when nothing.
  requirementErrors = roles: host: ((roleOf roles host.role).requirements or (_: [ ])) host;

  # The role's modules with a host's overrides applied: each name in
  # `overrides` maps to a replacement module, a list of them, or null for none.
  resolveRoleModules =
    roles: role: overrides:
    let
      bundled = (roleOf roles role).modules;
      names = map (b: b.name) bundled;
      unknown = lib.filter (name: !(lib.elem name names)) (lib.attrNames overrides);
      replace =
        b:
        if !(overrides ? ${b.name}) then
          [ b.module ]
        else if overrides.${b.name} == null then
          [ ]
        else
          lib.toList overrides.${b.name};
    in
    if unknown != [ ] then
      builtins.throw (
        "roleModules: role '${role}' bundles no module named "
        + lib.concatStringsSep ", " unknown
        + "; it bundles "
        + lib.concatStringsSep ", " names
      )
    else
      lib.concatMap replace bundled;

  # A built-in role's modules as they are bundled.
  getRoleModules = role: resolveRoleModules builtinRoles role { };

in
{
  inherit
    builtinRoles
    withPluginRoles
    requirementErrors
    resolveRoleModules
    getRoleModules
    ;
}
