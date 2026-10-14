# modules/pi/telegraf.nix
#
# Telegraf metrics agent — Pi side.
#
# Collects Pi system metrics and writes them to InfluxDB, on whichever host of
# the profile runs it. Telegraf consumes influxdb, so the address comes from
# the profile-wide endpoint table and modules/wiring/policy.nix admits this
# host to InfluxDB's port.
#
# Collected metrics
# -----------------
#   cpu          — CPU usage
#   mem          — RAM and swap
#   disk         — filesystem usage including NFS-exported drives
#   diskio       — drive read/write throughput
#   net          — network interface stats
#   system       — load average, uptime
#   processes    — process states
#   temp         — Raspberry Pi CPU temperature (via thermal zone)
#   ping         — reachability of the host running InfluxDB
#   smart        — S.M.A.R.T. attributes for the NVMe drives backing
#                  /mnt/storage-<drive> (hosts.<key>.storage.drives)
#
# Secrets
# -------
# Shares telegraf-token.age with the server: the same write token, so the Pi's
# host key must be a recipient of that file.
#
{
  config,
  pkgs,
  lib,
  ...
}:

let
  lanbat = config.lanbat;
  endpointLib = import ../../lib/endpoints.nix { inherit lib; };

  # InfluxDB is wherever the profile runs it; this names it, not the server.
  # It is reached at the address its generated rule admits this host from —
  # the overlay name when the edge runs on the overlay, the LAN address
  # otherwise — and on the port and scheme InfluxDB publishes.
  influx = lanbat.endpoints.influxdb;
  influxHost = endpointLib.soleHost {
    endpoints = lanbat.endpoints;
    name = "influxdb";
    consumer = "telegraf on ${lanbat.hostKey}";
  };
  influxReach = lanbat.endpointHost "influxdb" influxHost;

  # Empty on a voice Pi, which has no storage drives.
  storageDrives = lanbat.hosts.${lanbat.hostKey}.storage.drives or { };
  driveNames = lib.attrNames storageDrives;
in

{
  services.telegraf = {
    enable = true;

    extraConfig = lib.mkForce {
      agent = {
        interval = "30s";
        flush_interval = "30s";
        round_interval = true;
        metric_batch_size = 1000;
        metric_buffer_limit = 10000;
        collection_jitter = "5s";
        flush_jitter = "5s";
        precision = "0s";
      };

      outputs.influxdb_v2 = [
        {
          urls = [ "${influx.endpoint.scheme}://${influxReach}:${toString influx.endpoint.port}" ];
          token = "$TELEGRAF_INFLUXDB_TOKEN";
          organization = "homelab";
          bucket = "metrics";
        }
      ];

      inputs.cpu = [
        {
          percpu = true;
          totalcpu = true;
          collect_cpu_time = false;
          report_active = false;
        }
      ];
      inputs.mem = [ { } ];
      inputs.disk = [
        {
          # Include the LUKS-mounted drives to track fill levels.
          mount_points = [ "/" ] ++ map (drive: "/mnt/storage-${drive}") driveNames;
          ignore_fs = [
            "tmpfs"
            "devtmpfs"
            "devfs"
            "iso9660"
            "overlay"
            "aufs"
            "squashfs"
            "nsfs"
          ];
        }
      ];
      inputs.diskio = [ { } ];
      inputs.net = [ { ignore_protocol_stats = true; } ];
      inputs.system = [ { } ];
      inputs.processes = [ { } ];
      # Raspberry Pi CPU temperature via kernel thermal zone.
      inputs.temp = [ { } ];

      # Reachability of the host the metrics go to.
      inputs.ping = [
        {
          urls = [ influxReach ];
        }
      ];

      # CoreDNS metrics, on a host with the lanbat-dns plugin. Absent, not an
      # empty list, elsewhere: Telegraf 1.38 refuses "prometheus = []" and
      # doesn't start ("unsupported config format: prometheus").
      inputs.prometheus = lib.mkIf config.services.coredns.enable [
        { urls = [ "http://127.0.0.1:9153/metrics" ]; }
      ];

      # NVMe SMART via smartctl/nvme-cli. telegraf is in the disk group so
      # use_sudo is not required.
      inputs.smart = [
        {
          use_sudo = false;
          path_smartctl = "${pkgs.smartmontools}/bin/smartctl";
          path_nvme = "${pkgs.nvme-cli}/bin/nvme";
          devices = map (drive: "/dev/disk/by-id/${storageDrives.${drive}}") driveNames;
        }
      ];
    };
  };

  systemd.services.telegraf.serviceConfig = {
    AmbientCapabilities = "CAP_NET_RAW";
    SupplementaryGroups = [ "disk" ];
    EnvironmentFile = [
      config.lanbat.secrets.telegraf-token.path
    ];
  };

  lanbat.services.telegraf.secrets.telegraf-token = { };
  lanbat.services.telegraf.consumes = [ "influxdb" ];
}
