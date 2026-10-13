# services/grafana.nix
#
# Grafana — metrics dashboards and alerting.
#
# Design
# ------
# - NixOS-native service; no container needed.
# - Listens on localhost:3030 (port 3000 is taken by Homepage).
# - Caddy terminates TLS and proxies grafana.<domain>.
# - Auth: Authentik OIDC via generic_oauth.  Local admin is kept as
#   break-glass.  Auto-assign the Viewer role to all Authentik users;
#   promote individuals to Editor/Admin in the Grafana UI as needed.
# - InfluxDB datasource and Homelab dashboards are provisioned declaratively.
#   The datasource reaches InfluxDB over localhost at the port its endpoint
#   publishes (services/influxdb.nix says why not 127.0.0.1).
#
# Secrets (all in grafana-env.age, one KEY=value per line)
# -------
#   GF_SECURITY_SECRET_KEY          — random 64-char string for session signing
#   GF_SECURITY_ADMIN_PASSWORD      — local break-glass admin password
#   GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET — OIDC client secret from Authentik
#   INFLUXDB_TOKEN is injected at service start from influxdb-admin-token.age.
#   GRAFANA_ALERT_WEBHOOK_URL       — grafana-alert-env.age, only with
#                                     settings.alerting.enable
#
# Alerting (settings.alerting, off by default)
# --------
# Provisioned rules on the Telegraf metrics, all sent to one webhook: a
# filesystem on any host fuller than diskUsedPercent, and, with Frigate on this
# host, no Frigate event for frigateQuietHours (its event pipeline can die
# while the container runs on). Rules, contact point and policy are read-only
# in the UI; change them here.
#
# OIDC setup (chicken-and-egg, same pattern as Nextcloud/Immich)
# -----
#   1. Deploy Grafana (OIDC settings reference env vars that are empty → login
#      falls back to local admin).
#   2. Create an OIDC application in Authentik for Grafana.
#      Redirect URI: https://grafana.<domain>/login/generic_oauth
#   3. Populate grafana-env.age with the client secret.
#   4. Rebuild — OIDC login becomes available.
#
# Always-on: yes.  No NFS dependency.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  domain = config.lanbat.deployment.domain;

  # Grafana's InfluxDB datasource reads a token that influxdb.nix owns. A
  # deployment without InfluxDB still gets Grafana, its PostgreSQL-backed
  # dashboards and its OIDC login; it just has no metrics datasource.
  hasInflux = config.lanbat.hasService "influxdb";
  dashboards = pkgs.callPackage ../pkgs/grafana-dashboards { };

  cfg = config.lanbat.services.grafana.settings;
  alerting = cfg.alerting.enable;

  grafanaSettings = {
    options.alerting = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Provision alert rules on the Telegraf metrics in InfluxDB, sent to one
          webhook whose URL is GRAFANA_ALERT_WEBHOOK_URL in the
          grafana-alert-env secret (a Home Assistant webhook automation, ntfy,
          and so on). Needs InfluxDB on this host.
        '';
      };
      diskUsedPercent = lib.mkOption {
        type = lib.types.ints.between 1 100;
        default = 85;
        description = "Alert when a filesystem on any host is fuller than this, in percent, for 10 minutes.";
      };
      frigateQuietHours = lib.mkOption {
        type = lib.types.ints.positive;
        default = 3;
        description = ''
          Alert when Frigate on this host has created no event for this many
          hours: its event pipeline can die while the container keeps running.
          Set it above the longest quiet spell the cameras normally see.
        '';
      };
    };
  };

  # A Flux query on the metrics bucket over the rule's time range.
  fluxQuery = refId: query: {
    inherit refId;
    relativeTimeRange = {
      from = 900;
      to = 0;
    };
    datasourceUid = "influxdb-homelab";
    model = {
      inherit refId query;
    };
  };

  # A rule that fires when the last value of query A is above limit. The
  # templates use .Labels and .Values: Grafana expands $ in provisioning files.
  thresholdRule =
    {
      uid,
      title,
      query,
      limit,
      for,
      summary,
    }:
    {
      inherit uid title for;
      condition = "C";
      data = [
        (fluxQuery "A" query)
        {
          refId = "B";
          datasourceUid = "__expr__";
          model = {
            refId = "B";
            type = "reduce";
            expression = "A";
            reducer = "last";
          };
        }
        {
          refId = "C";
          datasourceUid = "__expr__";
          model = {
            refId = "C";
            type = "threshold";
            expression = "B";
            conditions = [
              {
                evaluator = {
                  type = "gt";
                  params = [ limit ];
                };
              }
            ];
          };
        }
      ];
      noDataState = "NoData";
      execErrState = "Error";
      annotations.summary = summary;
    };

  alertRules = [
    (thresholdRule {
      uid = "lanbat-disk-full";
      title = "Disk almost full";
      for = "10m";
      limit = cfg.alerting.diskUsedPercent;
      summary = ''{{ .Labels.path }} on {{ .Labels.host }} is {{ printf "%.0f" .Values.B.Value }}% full'';
      # One series per filesystem, named by its shortest mount path: bind
      # mounts repeat a device, and NFS mounts repeat the storage Pi's drives.
      query = ''
        from(bucket: "metrics")
          |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
          |> filter(fn: (r) => r._measurement == "disk" and r._field == "used_percent")
          |> filter(fn: (r) => r.fstype != "nfs" and r.fstype != "nfs4" and r.fstype != "efivarfs")
          |> last()
          |> group(columns: ["host", "device"])
          |> sort(columns: ["path"])
          |> first()
          |> group(columns: ["host", "path"])
      '';
    })
  ]
  ++ lib.optional (config.lanbat.hasService "frigate") (thresholdRule {
    uid = "lanbat-frigate-quiet";
    title = "Frigate quiet";
    for = "0s";
    limit = cfg.alerting.frigateQuietHours;
    summary = ''Frigate on {{ .Labels.host }} has created no event for {{ printf "%.1f" .Values.B.Value }} hours'';
    # Hours since the newest event's start (services/telegraf.nix).
    query = ''
      from(bucket: "metrics")
        |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
        |> filter(fn: (r) => r._measurement == "frigate_events" and r._field == "newest_start")
        |> group(columns: ["host"])
        |> last()
        |> map(fn: (r) => ({r with _value: (float(v: int(v: now())) / 1000000000.0 - r._value) / 3600.0}))
    '';
  });
in

{
  # The schema is merged into lanbat.services.grafana.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.grafana = grafanaSettings;

  assertions = [
    {
      assertion = alerting -> hasInflux;
      message = "lanbat.services.grafana.settings.alerting needs InfluxDB on the same host: its rules query the Telegraf metrics there.";
    }
  ];

  lanbat.services.grafana = {
    consumes = lib.optional hasInflux "influxdb";
    # The datasource token, which influxdb.nix declares.
    readsSecrets = lib.optional hasInflux "influxdb-admin-token";
    subdomain = "grafana";
    port = 3030;
    oidc.redirectPaths = [ "/login/generic_oauth" ];
    secrets = {
      grafana-env = { };
      # GRAFANA_ALERT_WEBHOOK_URL=<where alerts are posted>
      grafana-alert-env.enable = alerting;
    };
    dashboard = {
      group = "Monitoring";
      name = "Grafana";
      description = "Metrics dashboards";
      widget = {
        type = "grafana";
        version = 2;
        username = "admin";
        password = {
          _secret = {
            file = "grafana-env";
            var = "GF_SECURITY_ADMIN_PASSWORD";
          };
        };
      };
    };
  };

  # Dashboards, users and alert state live in the always-on PostgreSQL. Grafana
  # logs in as its system user over the socket, so no password is needed.
  lanbat.postgresql.databases.grafana.instance = "always-on";

  systemd.services.grafana-influxdb-token = lib.mkIf hasInflux {
    description = "InfluxDB operator token for Grafana datasource";
    before = [ "grafana.service" ];
    requiredBy = [ "grafana.service" ];
    # Use a separate runtime dir from grafana.service — stopping Grafana clears
    # its own RuntimeDirectory=grafana and would delete a shared token file.
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      RuntimeDirectory = "grafana-datasource";
      RuntimeDirectoryMode = "0750";
    };
    script = ''
      umask 077
      echo "INFLUXDB_TOKEN=$(cat ${config.lanbat.secrets.influxdb-admin-token.path})" \
        > /run/grafana-datasource/influxdb-token.env
      test -s /run/grafana-datasource/influxdb-token.env
    '';
  };

  systemd.services.grafana = {
    after = [
      (config.lanbat.postgresql.instance "always-on").unit
    ]
    ++ lib.optional hasInflux "grafana-influxdb-token.service";
    requires = [
      (config.lanbat.postgresql.instance "always-on").unit
    ]
    ++ lib.optional hasInflux "grafana-influxdb-token.service";
    serviceConfig = {
      # OAuth token/userinfo calls hit https://auth.<domain> server-side; trust
      # the internal Caddy CA (global environment.variables do not reach units).
      Environment = [
        "SSL_CERT_FILE=/var/lib/caddy-local-ca/ca-certificates.crt"
      ];
      EnvironmentFile = [
        config.lanbat.secrets.grafana-env.path
      ]
      ++ lib.optional hasInflux "/run/grafana-datasource/influxdb-token.env"
      ++ lib.optional alerting config.lanbat.secrets.grafana-alert-env.path;
    };
  };

  services.grafana = {
    enable = true;

    settings = {
      database =
        let
          pg = (config.lanbat.postgresql.instance "always-on");
        in
        {
          type = "postgres";
          # A socket directory with the port, which selects the socket file.
          host = "${pg.socket}:${toString pg.port}";
          name = "grafana";
          user = "grafana";
        };

      server = {
        http_addr = "127.0.0.1";
        http_port = config.lanbat.services.grafana.port;
        domain = "${config.lanbat.services.grafana.subdomain}.${domain}";
        root_url = "https://${config.lanbat.services.grafana.subdomain}.${domain}";
      };

      dashboards.default_home_dashboard_uid = "homelab-overview";

      users = {
        default_theme = "system";
        viewers_can_edit = false;
      };

      security = {
        # Injected at runtime from grafana-env.age — never written to store.
        secret_key = "$__env{GF_SECURITY_SECRET_KEY}";
        admin_password = "$__env{GF_SECURITY_ADMIN_PASSWORD}";
        admin_user = "admin";
      };

      auth.signout_redirect_url = "https://auth.${domain}/application/o/grafana/end-session/";

      # ---------------------------------------------------------------------------
      # Authentik OIDC
      # ---------------------------------------------------------------------------
      "auth.generic_oauth" = {
        enabled = true;
        name = "Authentik";
        allow_sign_up = true;
        # Client ID is not a secret — set it here directly.
        # CHANGE_ME: replace with the client ID from the Authentik application.
        client_id = "grafana";
        client_secret = "$__env{GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET}";
        scopes = "openid email profile";
        auth_url = "https://auth.${domain}/application/o/authorize/";
        token_url = "https://auth.${domain}/application/o/token/";
        api_url = "https://auth.${domain}/application/o/userinfo/";
        # Map all Authentik users to Viewer by default; promote in Grafana UI.
        role_attribute_path = "contains(groups[*], 'grafana-admins') && 'Admin' || 'Viewer'";
        login_attribute_path = "preferred_username";
        name_attribute_path = "name";
        email_attribute_path = "email";
        use_pkce = true;
      };
    };

    # ---------------------------------------------------------------------------
    # Declarative datasource provisioning
    # ---------------------------------------------------------------------------
    provision = {
      enable = true;

      datasources.settings = {
        # Replace the auto-generated UID from first boot with a stable one used
        # by the provisioned dashboards.
        deleteDatasources = [
          {
            name = "InfluxDB";
            orgId = 1;
          }
        ];

        datasources = lib.optionals hasInflux [
          {
            name = "InfluxDB";
            uid = "influxdb-homelab";
            type = "influxdb";
            access = "proxy";
            url =
              let
                inherit (config.lanbat.services.influxdb) endpoint;
              in
              "${endpoint.scheme}://localhost:${toString endpoint.port}";
            isDefault = true;
            editable = false;

            jsonData = {
              version = "Flux";
              organization = "homelab";
              defaultBucket = "metrics";
              tlsSkipVerify = false;
            };

            # Token injected from the environment — not stored in Nix store.
            secureJsonData = {
              token = "$__env{INFLUXDB_TOKEN}";
            };
          }
        ];
      };

      dashboards.settings = {
        apiVersion = 1;
        providers = [
          {
            name = "homelab";
            orgId = 1;
            folder = "Homelab";
            type = "file";
            disableDeletion = true;
            allowUiUpdates = false;
            updateIntervalSeconds = 30;
            options.path = dashboards;
          }
        ];
      };

      # settings.alerting: the rules above, all to one webhook.
      alerting = lib.mkIf alerting {
        rules.settings.groups = [
          {
            orgId = 1;
            name = "homelab";
            folder = "Homelab";
            interval = "1m";
            rules = alertRules;
          }
        ];

        contactPoints.settings.contactPoints = [
          {
            orgId = 1;
            name = "lanbat-webhook";
            receivers = [
              {
                uid = "lanbat-webhook";
                type = "webhook";
                settings = {
                  url = "$__env{GRAFANA_ALERT_WEBHOOK_URL}";
                  httpMethod = "POST";
                };
              }
            ];
          }
        ];

        policies.settings.policies = [
          {
            orgId = 1;
            receiver = "lanbat-webhook";
            group_by = [
              "grafana_folder"
              "alertname"
            ];
            repeat_interval = "12h";
          }
        ];
      };
    };
  };

}
