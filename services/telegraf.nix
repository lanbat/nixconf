# services/telegraf.nix
#
# Telegraf metrics agent — server side.
#
# Telegraf collects system and service metrics and writes them to InfluxDB.
# Grafana queries InfluxDB to display dashboards and fire alerts.
#
# Collected metrics
# -----------------
# System:
#   cpu          — per-core and total CPU usage
#   mem          — RAM and swap usage
#   disk         — filesystem usage per mount point
#   diskio       — read/write throughput per device
#   net          — network interface bytes/packets/errors
#   system       — load average, uptime, number of processes
#   processes    — process states (running, sleeping, zombie, etc.)
#   temp         — hardware temperature sensors (if available)
#
# Services:
#   systemd_units — active/failed state for all systemd services
#   nfsclient     — NFS mount operation counters and latency
#   http_response — HTTP health checks for Grafana, Home Assistant, Jellyfin,
#                   Immich, and Vaultwarden, those of them that run on this
#                   host, on the loopback ports their descriptions give
#   ping          — settings.pingTargets: by default the storage Pi, the
#                   default gateway and 1.1.1.1
#   redis         — shared Redis instance (loopback, no auth), when it runs here
#   http          — frigate_events: when Frigate last created an event, when
#                   it runs here
#
# InfluxDB
# --------
# Metrics go to InfluxDB at the port its endpoint publishes: over
# http://localhost when it runs on this host (see services/influxdb.nix for why
# not 127.0.0.1), otherwise wherever the profile runs it, as the Pi's Telegraf
# (modules/pi/telegraf.nix) reaches it.
#
# Note: inputs.docker removed — rootless Podman containers each have their own
# socket under /run/user/<uid>/podman; there is no single shared Docker-compat
# socket for telegraf to query.  Container metrics can be added via cgroups or
# per-container prometheus exporters if needed later.
#
# Secrets
# -------
# telegraf-token.age — one line: TELEGRAF_INFLUXDB_TOKEN=<write token>
# Any random value (openssl rand -base64 48). services/influxdb.nix provisions
# it in InfluxDB as a write token for the "metrics" bucket; the Pi's Telegraf
# uses the same token.
#
# Always-on: yes. No NFS dependency.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  lanbat = config.lanbat;
  cfg = lanbat.services.telegraf.settings;
  endpointLib = import ../lib/endpoints.nix { inherit lib; };

  influxUrl =
    if lanbat.hasService "influxdb" then
      let
        inherit (lanbat.services.influxdb) endpoint;
      in
      "${endpoint.scheme}://localhost:${toString endpoint.port}"
    else
      let
        influx = lanbat.endpoints.influxdb;
        host = endpointLib.soleHost {
          endpoints = lanbat.endpoints;
          name = "influxdb";
          consumer = "telegraf on ${lanbat.hostKey}";
        };
      in
      "${influx.endpoint.scheme}://${lanbat.endpointHost "influxdb" host}:${toString influx.endpoint.port}";

  # Every CoreDNS host in the profile (the lanbat-dns plugin), asked for this
  # host's own name: the health check of LAN DNS, from one place.
  dnsHosts = (lanbat.endpoints.coredns or { hosts = [ ]; }).hosts;
  dnsZone =
    if lanbat.deployment.dns.shortSuffix != null then
      lanbat.deployment.dns.shortSuffix
    else
      lanbat.deployment.domain;
  dnsProbeName = "${lanbat.hosts.${lanbat.hostKey}.networking.hostname}.${dnsZone}";

  # A health check of a service on this host, at the port its description
  # gives; none when the service does not run here.
  serviceHealthCheck =
    name: path:
    lib.optional (lanbat.hasService name) {
      urls = [ "http://127.0.0.1:${toString lanbat.services.${name}.port}${path}" ];
      response_status_code = 200;
      interval = "60s";
      response_timeout = "5s";
      name_override = name;
    };

  telegrafSettings = {
    options.pingTargets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = lib.filter (target: target != null) [
        lanbat.deployment.storageIp
        lanbat.deployment.gatewayIp
        "1.1.1.1"
      ];
      defaultText = lib.literalExpression ''
        [ config.lanbat.deployment.storageIp config.lanbat.deployment.gatewayIp "1.1.1.1" ]
      '';
      example = [
        "192.0.2.1"
        "9.9.9.9"
      ];
      description = ''
        Hosts Telegraf pings for reachability: by default the storage Pi, the
        default gateway, and 1.1.1.1 as a probe of the internet connection.
      '';
    };
  };
in

{
  # The schema is merged into lanbat.services.telegraf.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.telegraf = telegrafSettings;

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
          urls = [ influxUrl ];
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
          report_active = true;
        }
      ];
      inputs.mem = [ { } ];
      inputs.disk = [
        {
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
      inputs.temp = [ { } ];
      inputs.systemd_units = [ { } ];
      inputs.nfsclient = [ { fullstat = false; } ];

      inputs.http_response =
        serviceHealthCheck "grafana" "/api/health"
        ++ serviceHealthCheck "home-assistant" "/"
        ++ serviceHealthCheck "jellyfin" "/health"
        ++ serviceHealthCheck "immich" "/api/server/ping"
        ++ serviceHealthCheck "vaultwarden" "/alive";

      inputs.ping = [
        {
          urls = cfg.pingTargets;
        }
      ];

      # Whether every CoreDNS host answers, with what rcode and how fast.
      inputs.dns_query = lib.optionals (dnsHosts != [ ]) [
        {
          servers = map (host: lanbat.hosts.${host}.networking.ip) dnsHosts;
          domains = [ dnsProbeName ];
          record_type = "A";
          timeout = "2s";
          interval = "60s";
        }
      ];

      # CoreDNS metrics, on a host with the lanbat-dns plugin.
      inputs.prometheus = lib.optionals config.services.coredns.enable [
        { urls = [ "http://127.0.0.1:9153/metrics" ]; }
      ];

      # When Frigate last created an event (frigate_events.newest_start, Unix
      # seconds). Frigate can stay up with its event pipeline dead, as when a
      # full disk kills its detected_frames_processor thread; this goes stale
      # then, and Grafana's "Frigate quiet" alert reads it.
      inputs.http = lib.optionals (lanbat.hasService "frigate") [
        {
          urls = [ "http://127.0.0.1:${toString lanbat.services.frigate.port}/api/events?limit=1" ];
          interval = "60s";
          timeout = "5s";
          name_override = "frigate_events";
          data_format = "json_v2";
          json_v2 = [
            {
              field = [
                {
                  path = "0.start_time";
                  rename = "newest_start";
                  type = "float";
                }
              ];
            }
          ];
        }
      ];

      inputs.redis = lib.optionals (lanbat.hasService "redis") [
        {
          servers = [ "tcp://127.0.0.1:${toString config.services.redis.servers.shared.port}" ];
        }
      ];
    };
  };

  systemd.services.telegraf.serviceConfig = {
    AmbientCapabilities = "CAP_NET_RAW";
    EnvironmentFile = [
      config.lanbat.secrets.telegraf-token.path
    ];
  };

  lanbat.services.telegraf.secrets.telegraf-token = { };
  lanbat.services.telegraf.consumes = [ "influxdb" ];
}
