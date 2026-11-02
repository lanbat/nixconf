# tests/jellyfin-watch.nix
#
# The storage Pi's Jellyfin media watcher (modules/storage/jellyfin-watch.nix):
# off by default, and when enabled it declares the API-key secret, raises the
# inotify limits, trusts the internal CA and starts a unit that watches every
# drive's media tree. Pure evaluation — nothing is built.
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

  # Stand-ins for the storage Pi's bundled Telegraf and snapclient consumers, so
  # a profile can exist without InfluxDB or Snapcast (as in tests/storage-drives.nix).
  standInPlugin = {
    name = "jellyfin-watch-standins";
    version = 1;
    roles = [ "server" ];
    modules = [
      {
        lanbat.services = {
          influxdb.endpoint.port = 8086;
          snapcast.endpoint.port = 1780;
        };
      }
    ];
  };

  mkDeploy = watch: {
    deployment = {
      domain = "home.test";
      rootDomain = "test";
      secrets = {
        provider = "none";
        root = ../secrets;
      };
      gatewayIp = "192.0.2.1";
      lanSubnet = "192.0.2.0/24";
      nfsIdmapdDomain = "home.test";
      timezone = "UTC";
      phoneRegion = "GB";
      haLatitude = "51.5";
      haLongitude = "-0.1";
      haElevation = 0;
      zigbeeVendorId = "10c4";
      zigbeeProductId = "ea60";
      adminSshKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleExample test";
      haLlm = {
        baseUrl = "https://llm.test/v1";
        model = "test-model";
      };
      voiceRooms = { };
    };

    hosts = {
      server = {
        role = "server";
        system = "x86_64-linux";
        networking = {
          ip = "192.0.2.10";
          interface = "eno1";
          hostname = "server";
        };
        disks.system = "/dev/disk/by-id/test-system-disk";
        plugins = [ standInPlugin ];
      };

      pi-storage = {
        role = "storage-pi";
        system = "aarch64-linux";
        platform = "raspberry-pi-5";
        networking = {
          ip = "192.0.2.11";
          interface = "end0";
          hostname = "pi5";
        };
        storage.drives = {
          a = "test-storage-a";
          b = "test-storage-b";
        };
        plugins = [ ];
        modules = lib.optionals watch [ { lanbat.storagePi.jellyfinWatch.enable = true; } ];
      };
    };
  };

  profiles = {
    off = mkDeploy false;
    on = mkDeploy true;
  };

  lanbatLib = import ../lib {
    self = inputsWithSelf.self;
    inputs = inputsWithSelf;
    inherit
      nixpkgs
      nixos-raspberrypi
      agenix
      disko
      deploy-rs
      profiles
      ;
  };

  off = lanbatLib.configurations."off-pi-storage".config;
  on = lanbatLib.configurations."on-pi-storage".config;

  expect = name: cond: if cond then null else "FAIL: ${name}";

  cases = [
    (expect "off: no secret declared and no unit" (
      !(off.lanbat.hostSecrets ? jellyfin-watch-env) && !(off.systemd.services ? jellyfin-watch)
    ))
    (expect "on: the API-key secret is declared" (on.lanbat.hostSecrets ? jellyfin-watch-env))
    (expect "on: the secret is provisioned" (on.lanbat.secrets ? jellyfin-watch-env))
    (expect "on: the unit exists" (on.systemd.services ? jellyfin-watch))
    (expect "on: watches each drive's media tree" (
      let
        start = on.systemd.services.jellyfin-watch.serviceConfig.ExecStart;
      in
      lib.hasInfix "/mnt/storage-a/media" start && lib.hasInfix "/mnt/storage-b/media" start
    ))
    (expect "on: starts after the drives unlock and the network is up" (
      on.systemd.services.jellyfin-watch.after == [
        "storage-a-unlock.service"
        "storage-b-unlock.service"
        "network-online.target"
      ]
    ))
    (expect "on: raises the inotify watch limits" (
      on.boot.kernel.sysctl."fs.inotify.max_user_watches" == 524288
    ))
    (expect "on: trusts the internal CA" (on.security.pki.certificateFiles != [ ]))
  ];

  failures = lib.filter (x: x != null) cases;
in
pkgs.runCommand "jellyfin-watch-check" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "jellyfin-watch checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo ${lib.escapeShellArg "  - ${msg}"} >&2") failures)}
    exit 1
  fi
  touch $out
''
