# tests/private-media.nix
#
# media/adult is for the private group alone (docs/deployment-checklist.md: the
# hidden Samba "private" share). Pure evaluation of the example profile, whose
# storage Pi runs the TV session:
#
#   - the TV's auto-login account is not in the private group, and Kodi seeds
#     no source for the folder, so nobody at the TV can browse it;
#   - lanbat.services.kodi.settings.privateLibrary turns both on for a profile
#     that asks: the account joins the group, the folder becomes a movies
#     source with Kodi's local scraper, and every other source is unchanged;
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

  # The example profile with privateLibrary on, as a profile sets it in its
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
              { lanbat.services.kodi.settings.privateLibrary = true; }
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
    "privateLibrary puts the TV account in the private group" =
      lib.elem "private" piPrivate.users.users.media.extraGroups;
    "privateLibrary adds the private folder as a movies source" =
      piPrivate.lanbat.services.kodi.settings.videoSources.adult == {
        path = "/mnt/storage-b/media/adult/";
        content = "movies";
        scraper = "metadata.local";
      };
    "privateLibrary leaves the other sources alone" = lib.all (
      name:
      piPrivate.lanbat.services.kodi.settings.videoSources.${name}.path
      == pi.lanbat.services.kodi.settings.videoSources.${name}.path
    ) (lib.attrNames pi.lanbat.services.kodi.settings.videoSources);
    "privateLibrary reaches Kodi's bootstrap" =
      lib.hasInfix "adult|/mnt/storage-b/media/adult/|movies|metadata.local|1|0" piPrivate.systemd.services.kodi-bootstrap.environment.KODI_VIDEO_SOURCES;
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
