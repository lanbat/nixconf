# tests/storage-drives.nix
#
# A storage host's drive set is the keys of hosts.<key>.storage.drives, not a
# fixed a and b. Builds profiles through lib/, one whose storage host has a
# single drive and one whose has three, and checks that the Pi unlocks,
# initialises and exports exactly those drives and that the server mounts them.
#
# The storage Pi also reaches its peers by what they run rather than by the
# server's address: it exports to the hosts whose services use its drives, and
# Telegraf and snapclient find InfluxDB and snapserver through consumes. A
# profile with a second server that uses the drives and runs both of those
# shows that none of it assumes the primary server.
#
# Pure evaluation: nothing is built.
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

  # A server service that uses the given drives, standing in for Immich or
  # Jellyfin without their dependencies, and stand-ins for the services the
  # storage Pi's own Telegraf and snapclient consume.
  probePlugin =
    { drives, standIns }:
    {
      name = "storage-probe";
      version = 1;
      roles = [ "server" ];
      modules = [
        {
          lanbat.services = {
            probe = {
              nfs.drives = drives;
              units = [ "probe" ];
            };
          }
          // lib.optionalAttrs standIns {
            influxdb.endpoint.port = 8086;
            snapcast.endpoint.port = 1780;
          };
          systemd.services.probe.serviceConfig.ExecStart = "/run/current-system/sw/bin/true";
        }
      ];
    };

  serverHost = ip: hostname: plugin: {
    role = "server";
    system = "x86_64-linux";
    networking = {
      inherit ip hostname;
      interface = "eno1";
    };
    disks.system = "/dev/disk/by-id/test-system-disk";
    plugins = [ plugin ];
  };

  mkDeploy =
    {
      drives,
      probeDrives,
      # A second server that also uses the drives and runs what the Pi consumes.
      second ? false,
    }:
    {
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
      }
      // lib.optionalAttrs second { primaryServer = "server"; };

      hosts = {
        server = serverHost "192.0.2.10" "server" (probePlugin {
          drives = probeDrives;
          standIns = !second;
        });

        pi-storage = {
          role = "storage-pi";
          system = "aarch64-linux";
          platform = "raspberry-pi-5";
          networking = {
            ip = "192.0.2.11";
            interface = "end0";
            hostname = "pi5";
          };
          storage = { inherit drives; };
          plugins = [ ];
        };
      }
      // lib.optionalAttrs second {
        server2 = serverHost "192.0.2.12" "server2" (probePlugin {
          drives = probeDrives;
          standIns = true;
        });
      };
    };

  profiles = {
    one = mkDeploy {
      drives.data = "test-disk-1";
      probeDrives = [ "data" ];
    };
    three = mkDeploy {
      drives = {
        a = "test-disk-1";
        b = "test-disk-2";
        c = "test-disk-3";
      };
      probeDrives = [
        "a"
        "c"
      ];
    };
    two-clients = mkDeploy {
      drives = {
        a = "test-disk-1";
        b = "test-disk-2";
      };
      probeDrives = [ "a" ];
      second = true;
    };
    # Nothing uses the drives, so nobody may mount them.
    no-clients = mkDeploy {
      drives.a = "test-disk-1";
      probeDrives = [ ];
    };
    # The probe names a drive the storage host does not have.
    unknown = mkDeploy {
      drives.a = "test-disk-1";
      probeDrives = [ "b" ];
    };
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

  cfg = name: lanbatLib.configurations.${name}.config;

  failedAssertions = c: map (a: a.message) (lib.filter (a: !a.assertion) c.assertions);

  # Everything a drive name is turned into, on the Pi and on the server.
  driveCases =
    profile: drives:
    let
      pi = cfg "${profile}-pi-storage";
      server = cfg "${profile}-server";
    in
    lib.concatMap (drive: [
      (expect "${profile}: storage-${drive}-unlock exists" (
        pi.systemd.services ? "storage-${drive}-unlock"
      ))
      (expect "${profile}: storage-${drive}-unlock opens its drive into its own mapper and mount point" (
        lib.hasSuffix
          " ${pi.lanbat.hosts.pi-storage.storage.drives.${drive}} storage-${drive} /mnt/storage-${drive}"
          pi.systemd.services."storage-${drive}-unlock".serviceConfig.ExecStart
      ))
      (expect "${profile}: storage-${drive}-unlock is retried" (
        pi.systemd.timers ? "storage-${drive}-unlock"
      ))
      (expect "${profile}: storage-${drive}-init follows its unlock" (
        pi.systemd.services."storage-${drive}-init".after == [ "storage-${drive}-unlock.service" ]
      ))
      (expect "${profile}: /mnt/storage-${drive} is exported" (
        lib.hasInfix "/mnt/storage-${drive}  " pi.services.nfs.server.exports
      ))
      (expect "${profile}: /mnt/storage-${drive} has a mount point stub" (
        lib.elem "d /mnt/storage-${drive} 0755 root root -" pi.systemd.tmpfiles.rules
      ))
      (expect "${profile}: the server mounts /srv/storage/${drive}" (
        (server.fileSystems."/srv/storage/${drive}".device or null) == "pi5:/mnt/storage-${drive}"
      ))
    ]) drives
    ++ [
      (expect "${profile}: no unlock units beyond the drives" (
        lib.sort (x: y: x < y) (
          lib.filter (n: lib.hasPrefix "storage-" n && lib.hasSuffix "-unlock" n) (
            lib.attrNames pi.systemd.services
          )
        ) == map (d: "storage-${d}-unlock") drives
      ))
      (expect "${profile}: one export line per drive" (
        lib.length (lib.filter (l: l != "") (lib.splitString "\n" pi.services.nfs.server.exports))
        == lib.length drives
      ))
      (expect "${profile}: SMART reads every drive" (
        lib.length (lib.head pi.services.telegraf.extraConfig.inputs.smart).devices == lib.length drives
      ))
      (expect "${profile}: the Pi's assertions hold: ${toString (failedAssertions pi)}" (
        failedAssertions pi == [ ]
      ))
      (expect "${profile}: the server's assertions hold: ${toString (failedAssertions server)}" (
        failedAssertions server == [ ]
      ))
    ];

  expect = name: cond: if cond then null else "FAIL: ${name}";

  one = cfg "one-pi-storage";
  three = cfg "three-pi-storage";
  unknownServer = cfg "unknown-server";

  exportOpts = "rw,sync,no_subtree_check,no_root_squash,mp";

  # Where the Pi's own consumers connect, and who it lets mount its drives.
  peerCases =
    profile:
    { peer, clients }:
    let
      pi = cfg "${profile}-pi-storage";
      firewall = pi.networking.firewall.extraCommands;
      telegraf = pi.services.telegraf.extraConfig;
    in
    [
      (expect "${profile}: Telegraf writes to the host running InfluxDB" (
        (lib.head telegraf.outputs.influxdb_v2).urls == [ "http://${peer}:8086" ]
      ))
      (expect "${profile}: Telegraf pings the host running InfluxDB" (
        (lib.head telegraf.inputs.ping).urls == [ peer ]
      ))
      (expect "${profile}: snapclient connects to the host running snapcast" (
        lib.hasInfix "--host ${peer} --port 1704 " pi.systemd.services.snapclient.serviceConfig.ExecStart
      ))
      (expect "${profile}: every export goes to exactly the NFS clients" (
        lib.all (
          line:
          line == ""
          ||
            lib.tail (lib.filter (w: w != "") (lib.splitString " " line))
            == map (c: "${c}(${exportOpts})") clients
        ) (lib.splitString "
" pi.services.nfs.server.exports)
      ))
      (expect "${profile}: NFS is dropped from everyone else" (lib.hasInfix "--dport 2049" firewall))
    ]
    ++ (
      if lib.length clients == 1 then
        [
          (expect "${profile}: a single client keeps the one-rule form" (
            lib.hasInfix "iptables -I INPUT -p tcp --dport 2049 ! -s ${lib.head clients} -j DROP\n" firewall
            && lib.hasInfix "iptables -I INPUT -p udp --dport 2049 ! -s ${lib.head clients} -j DROP\n" firewall
          ))
        ]
      else
        [
          (expect "${profile}: NFS is dropped unless a client is accepted above it" (
            lib.hasInfix "--dport 2049 ! -i lo -j DROP" firewall && !(lib.hasInfix "--dport 2049 ! -s" firewall)
          ))
        ]
        ++ map (
          c:
          expect "${profile}: ${c} is admitted to NFS" (
            lib.hasInfix "-p tcp --dport 2049 -s ${c} -j ACCEPT" firewall
            && lib.hasInfix "-p udp --dport 2049 -s ${c} -j ACCEPT" firewall
          )
        ) clients
    );

  cases =
    driveCases "one" [ "data" ]
    ++ peerCases "one" {
      peer = "192.0.2.10";
      clients = [ "192.0.2.10" ];
    }
    ++ driveCases "two-clients" [
      "a"
      "b"
    ]
    ++ peerCases "two-clients" {
      peer = "192.0.2.12";
      clients = [
        "192.0.2.10"
        "192.0.2.12"
      ];
    }
    ++ driveCases "three" [
      "a"
      "b"
      "c"
    ]
    ++ [
      # backup-server.sh refuses to run unless its destination already exists,
      # so the drive it writes to must create that directory, not only its parent.
      (expect "the backup destination backup-server.sh checks for is created" (
        let
          script = builtins.readFile ../pkgs/scripts/backup-server.sh;
          m = builtins.match ".*\nBACKUP_DIR=/srv/storage/([^/]+)/([^\n]+)\n.*" script;
          drive = lib.elemAt m 0;
          dir = lib.elemAt m 1;
        in
        m != null
        &&
          lib.hasInfix ''"$base/${dir}"''
            (cfg "three-pi-storage").systemd.services."storage-${drive}-init".serviceConfig.ExecStart.text
      ))
      # The unit runs as root with only its own path; sudo is a setuid wrapper
      # outside it, so the script drops to postgres with runuser instead.
      (expect "backup-server drops privileges with a command its unit provides" (
        let
          script = builtins.readFile ../pkgs/scripts/backup-server.sh;
          unitPath = (cfg "three-server").systemd.services.backup-server.path;
        in
        !(lib.hasInfix "sudo " script)
        && lib.hasInfix "runuser -u postgres --" script
        && lib.any (p: (p.pname or "") == "util-linux") unitPath
      ))
      # Each instance dumps the databases the profile declares on it, not a list
      # written into the script: a profile without one of them must still back up.
      (expect "backup-server dumps the databases the profile declares" (
        let
          server = cfg "three-server";
          env = server.systemd.services.backup-server.environment;
          declaredOn =
            instance:
            lib.concatStringsSep " " (
              lib.sort (x: y: x < y) (
                lib.attrNames (lib.filterAttrs (_: d: d.instance == instance) server.lanbat.postgresql.databases)
              )
            );
          script = builtins.readFile ../pkgs/scripts/backup-server.sh;
        in
        env.BACKUP_DATABASES_ALWAYS_ON == declaredOn "always-on"
        && env.BACKUP_DATABASES_WORKLOAD == declaredOn "workload"
        && lib.hasInfix "{BACKUP_DATABASES_ALWAYS_ON:-}" script
        && lib.hasInfix "{BACKUP_DATABASES_WORKLOAD:-}" script
        && !(lib.hasInfix "authentik hass grafana" script)
      ))
      (expect "one drive: nothing refers to the absent a or b" (
        !(one.systemd.services ? "storage-a-unlock")
        && !(one.systemd.services ? "storage-b-init")
        && !(one.systemd.services ? user-storage-quotas)
      ))
      (expect "three drives: the probe binds to the drives it names" (
        (cfg "three-server").systemd.services.probe.bindsTo == [
          "srv-storage-a.mount"
          "srv-storage-c.mount"
        ]
      ))
      (expect "three drives: user quotas follow drive b" (
        three.systemd.services.user-storage-quotas.requires == [ "storage-b-init.service" ]
      ))
      (expect "no clients: nothing is exported" (
        (cfg "no-clients-pi-storage").services.nfs.server.exports == ""
      ))
      (expect "no clients: nobody is admitted to NFS" (
        !(lib.hasInfix "2049 -s" (cfg "no-clients-pi-storage").networking.firewall.extraCommands)
      ))
      (expect "two clients: the provider's policy admits the Pi's Telegraf" (
        lib.hasInfix "--dport 8086 -s 192.0.2.11 -j ACCEPT" (cfg "two-clients-server2")
        .networking.firewall.extraCommands
      ))
      (expect "a drive the storage host lacks is rejected" (
        lib.any (lib.hasInfix "uses drive b, which pi-storage does not have") (
          failedAssertions unknownServer
        )
      ))
    ];

  failures = lib.filter (x: x != null) cases;
in
pkgs.runCommand "storage-drives-check" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "storage drive checks failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
