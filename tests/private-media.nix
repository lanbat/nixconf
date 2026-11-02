# tests/private-media.nix
#
# media/adult is for the private group alone (docs/deployment-checklist.md: the
# hidden Samba "private" share). Pure evaluation of the example profile, whose
# storage Pi runs the TV session:
#
#   - the TV's auto-login account is not in the private group, and Kodi seeds
#     no source for the folder, so nobody at the TV can browse it;
#   - lanbat.services.kodi.settings.privateMedia turns the group on for a
#     profile that asks and adds the folder as a browse-only source: it reaches
#     sources.xml (Videos > Files) but never videoSources, so Kodi never scans
#     it into the library or Global Search;
#   - the drive's init keeps everything inside the folder in the private group
#     with no access for others, so a file moved in from elsewhere does not stay
#     readable to every media user behind the folder's own lock.
{
  lib,
  pkgs,
  inputs,
  self,
  agenix,
  disko,
  deploy-rs,
  nixpkgs,
  nixos-raspberrypi,
}:

let
  inputsWithSelf = inputs // {
    self = self // {
      lanbatPlugins = import ../plugins;
    };
  };

  exampleDeploy = import ../deployments/example/deploy.nix { inputs = inputsWithSelf; };

  lanbatLib = import ../lib {
    self = inputsWithSelf.self;
    inputs = inputsWithSelf;
    profiles = { };
    inherit
      nixpkgs
      nixos-raspberrypi
      agenix
      disko
      deploy-rs
      ;
  };

  pi = (lanbatLib.mkProfile "example" exampleDeploy).configurations.example-pi-storage.config;

  # The example profile with privateMedia on, as a profile sets it in its
  # hosts.pi-storage.modules.
  piPrivate =
    (lanbatLib.mkProfile "example" (
      exampleDeploy
      // {
        hosts = lib.mapAttrs (
          name: host:
          host
          // lib.optionalAttrs (name == "pi-storage") {
            modules = (host.modules or [ ]) ++ [
              { lanbat.services.kodi.settings.privateMedia = true; }
            ];
          }
        ) exampleDeploy.hosts;
      }
    )).configurations.example-pi-storage.config;

  initB = pi.systemd.services."storage-b-init".serviceConfig.ExecStart.text;

  checks = {
    "the example Pi runs the TV session" = pi.users.users ? media;
    "the TV account is not in the private group" =
      !(lib.elem "private" pi.users.users.media.extraGroups);
    "Kodi's seeded sources leave out the private folder" =
      !(lib.hasInfix "media/adult" (builtins.readFile ../pkgs/kodi-tv-config/sources.xml));
    "Kodi's library bootstrap leaves out the private folder" =
      !(lib.hasInfix "media/adult" (builtins.readFile ../pkgs/kodi-bootstrap/bootstrap-kodi.sh));
    "privateMedia puts the TV account in the private group" =
      lib.elem "private" piPrivate.users.users.media.extraGroups;
    "privateMedia keeps the folder out of the video library" =
      !(piPrivate.lanbat.services.kodi.settings.videoSources ? adult);
    "privateMedia leaves the other sources alone" = lib.all (
      name:
      piPrivate.lanbat.services.kodi.settings.videoSources.${name}.path
      == pi.lanbat.services.kodi.settings.videoSources.${name}.path
    ) (lib.attrNames pi.lanbat.services.kodi.settings.videoSources);
    "privateMedia lists the folder in Videos > Files only" =
      piPrivate.systemd.services.kodi-bootstrap.environment.KODI_FILE_SOURCES
      == "Private|/mnt/storage-b/media/adult/";
    "privateMedia adds no scanned source to the bootstrap" =
      !(lib.hasInfix "media/adult" piPrivate.systemd.services.kodi-bootstrap.environment.KODI_VIDEO_SOURCES);
    "the add-on works in the folder the source lists" =
      lib.all
        (
          file:
          lib.hasInfix "/mnt/storage-b/media/adult/" (
            builtins.readFile (../pkgs/kodi-addon-private-media/script.private-media + "/${file}")
          )
        )
        [
          "addon.xml"
          "default.py"
          "delete.py"
        ];
    "the drive init keeps the folder's contents private" =
      lib.hasInfix ''chgrp -R ${toString pi.users.groups.private.gid} "$base/media/adult"'' initB
      && lib.hasInfix ''chmod -R o-rwx "$base/media/adult"'' initB;
  };

  failed = lib.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
if failed != [ ] then
  throw "private-media: failed: ${lib.concatStringsSep "; " failed}"
else
  pkgs.runCommand "private-media" { } ''
    echo ${lib.escapeShellArg (lib.concatStringsSep "\n" (lib.attrNames checks))} > $out
  ''
