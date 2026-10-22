# services/nextcloud.nix
#
# Nextcloud — file sync and collaboration.
#
# Storage split
# -------------
# Server-local (fast, reliable, always-on):
#   /var/lib/nextcloud/     — app code, config, skeleton
#   PostgreSQL              — database (workload instance)
#
# Pi-backed via NFS (/srv/storage/<drive>):
#   /srv/storage/<drive>/<path>/external/  — bulk user data (External Storage app)
#   /srv/storage/<drive>/<path>/users/
#   settings.storage.{drive,path} place them; by default b and nextcloud. The
#   module only creates the directories: the External Storage mounts that use
#   them are set up in Nextcloud's admin settings.
#
# If the Pi is down, Nextcloud still works — external storage shows errors
# for those folders only; the app itself is healthy.
#
# NFS dependency: soft (intentionally omitted from lanbat.services.*.nfs).
# Bulk data lives on Pi storage, but hard-binding Nextcloud to NFS would stop
# the whole app (and OIDC login) when the Pi is unreachable. External Storage
# fails per-folder instead; see CONTRIBUTING.md § NFS-dependent services.
#
# Auth
# ----
# Nextcloud uses the `user_oidc` app to delegate login to Authentik.
# The occ setup service below configures it automatically, but it needs
# the OIDC client credentials from Authentik first (chicken-and-egg):
#   1. Deploy Nextcloud.
#   2. Create the OIDC application in Authentik UI.
#   3. Store the client_id/secret in agenix (nextcloud-oidc-env.age).
#   4. Rebuild — the setup service runs and configures OIDC.
# Local admin account is always kept as break-glass.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;

  cfg = config.lanbat.services.nextcloud.settings;
  # Where the server mounts the Pi storage drive (modules/wiring/nfs.nix).
  bulkDir = "/srv/storage/${cfg.storage.drive}/${cfg.storage.path}";

  nextcloudSettings = {
    options.storage = {
      drive = lib.mkOption {
        type = lib.types.str;
        default = "b";
        description = ''
          Pi storage drive holding the bulk user data that the External Storage
          app serves, by its key in the storage host's storage.drives.
          Nextcloud deliberately does not bind to its NFS mount (see the top of
          services/nextcloud.nix).
        '';
      };
      path = lib.mkOption {
        type = lib.types.str;
        default = "nextcloud";
        description = ''
          Directory of the bulk user data, relative to the drive's mount
          (/srv/storage/<drive>). The module creates it with external/ and
          users/ inside.
        '';
      };
    };
  };
  db = config.lanbat.postgresql.instance "workload";
in

{
  # The schema is merged into lanbat.services.nextcloud.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.nextcloud = nextcloudSettings;

  # The timer would start nextcloud-cron, and with it the workload layer, five
  # minutes after boot. Run it only while the layer is unlocked.
  #
  # The nixpkgs module also sets the timer's after = [ "nextcloud-setup.service"
  # ]. That edge, together with this timer's PartOf=workload-online.target (which
  # makes systemd order the target after the timer) and the workload gate's
  # BindsTo+After=workload-online.target on nextcloud-setup, forms the ordering
  # cycle target -> timer -> nextcloud-setup -> target. While the layer is
  # stopping all three are hard-linked to the target, so systemd cannot drop any
  # of them to break it: the stop fails, nextcloud and the database stay up, and
  # the LUKS close fails with EBUSY. Drop the timer's After edge to break the
  # cycle; the cron service still orders itself after nextcloud-setup on its own.
  systemd.timers.nextcloud-cron = {
    wantedBy = lib.mkForce [ "workload-online.target" ];
    partOf = [ "workload-online.target" ];
    after = lib.mkForce [ ];
  };

  # The database and its owner role, both "nextcloud", on the workload instance
  # (services/postgresql.nix), which puts them in the NixOS module's
  # ensureDatabases and ensureUsers exactly as database.createLocally did. The
  # nextcloud system user logs in over the socket without a password (peer).
  lanbat.postgresql.databases.nextcloud.instance = "workload";

  # createLocally ordered setup after the database; keep that.
  systemd.services.nextcloud-setup = {
    after = [ db.unit ];
    requires = [ db.unit ];
  };

  lanbat.services.nextcloud = {
    subdomain = "cloud";
    port = 8080;
    apiClients = true; # desktop and mobile sync clients
    oidc.redirectPaths = [ "/apps/user_oidc/code" ];
    caddy.extraConfig = ''
      redir /.well-known/carddav /remote.php/dav 301
      redir /.well-known/caldav  /remote.php/dav 301
    '';
    tier = "workload";
    state = [ "nextcloud" ];
    units = [
      "phpfpm-nextcloud"
      "nextcloud-setup"
      "nextcloud-update-db"
      "nextcloud-cron"
    ];
    # The setup script requires config/ to be owned by nextcloud on first run.
    workloadDirs = {
      "nextcloud".user = "nextcloud";
      "nextcloud/config".user = "nextcloud";
    };
    account.uid = 990;
    secrets = {
      nextcloud-admin-pass = { };
      nextcloud-oidc-env = { };
    };
    dashboard = {
      group = "Files & Sync";
      name = "Nextcloud";
      description = "File sync & collaboration";
      widget = {
        type = "nextcloud";
        username = "admin";
        password = {
          _secret = {
            file = "nextcloud-admin-pass";
          };
        };
      };
    };
  };

  services.nextcloud = {
    enable = true;
    hostName = "cloud.${domain}";
    # Pin the major version explicitly — Nextcloud cannot skip majors, and nixpkgs
    # removes old majors (nextcloud31 already throws). Without this line, the module
    # default depends on system.stateVersion and will drift on nixpkgs bumps.
    #
    # UPGRADE PATH: one major at a time (NC32 → NC33 → …). See docs/runbook.md
    # § Nextcloud major version upgrade. nix flake check still warns while the pin
    # is below the latest packaged major (33); that warning clears only after the
    # live instance is migrated and this line is bumped to pkgs.nextcloud33.
    package = pkgs.nextcloud32;

    https = true;

    config = {
      # PostgreSQL over the workload instance's Unix socket (peer auth, no
      # password), in the database lanbat.postgresql.databases.nextcloud.
      dbtype = "pgsql";
      dbhost = db.socket;
      dbname = "nextcloud";
      dbuser = "nextcloud";
      adminuser = "admin";
      adminpassFile = config.lanbat.secrets.nextcloud-admin-pass.path;
    };

    phpOptions = {
      "opcache.interned_strings_buffer" = "16";
      "opcache.max_accelerated_files" = "10000";
      "opcache.memory_consumption" = "128";
      "opcache.save_comments" = "1";
      "opcache.revalidate_freq" = "1";
      upload_max_filesize = lib.mkForce "16G";
      post_max_size = lib.mkForce "16G";
      memory_limit = "512M";
    };

    extraApps = with config.services.nextcloud.package.packages.apps; {
      # user_oidc is the OIDC login app.
      # It may not be in the generated apps attrset for all NC versions;
      # if it isn't, install it via occ app:install user_oidc post-deploy.
      # inherit user_oidc;
    };
    extraAppsEnable = true;

    settings = {
      trusted_proxies = [ "127.0.0.1" ];
      overwrite.cli.url = "https://cloud.${domain}";
      default_phone_region = config.lanbat.deployment.phoneRegion;
    };
  };

  # ---------------------------------------------------------------------------
  # OIDC setup via occ (runs once after Nextcloud is up)
  # ---------------------------------------------------------------------------
  # This service configures the user_oidc provider pointing to Authentik.
  # It is idempotent: it checks whether the provider already exists first.
  # It will fail (and be retried) until nextcloud-oidc-env.age is populated.
  systemd.services."nextcloud-oidc-setup" = {
    description = "Configure Nextcloud OIDC provider";
    after = [ "nextcloud-setup.service" ];
    wantedBy = [ "nextcloud-setup.service" ];
    # Only runs if the env file exists and is non-empty.
    unitConfig.ConditionPathExists = config.lanbat.secrets.nextcloud-oidc-env.path;
    serviceConfig = {
      Type = "oneshot";
      User = "nextcloud";
      # Source the env file which exports:
      #   NEXTCLOUD_OIDC_CLIENT_ID=<value>
      #   NEXTCLOUD_OIDC_CLIENT_SECRET=<value>
      ExecStart = pkgs.writeShellScript "nextcloud-oidc-setup" ''
        set -euo pipefail
        . ${config.lanbat.secrets.nextcloud-oidc-env.path}

        OCC="${config.services.nextcloud.occ}/bin/nextcloud-occ"

        # Install and enable the OIDC app (not always in extraApps for every NC version).
        if ! $OCC app:list 2>/dev/null | grep -q "user_oidc"; then
          $OCC app:install user_oidc
        fi
        $OCC app:enable user_oidc || true

        # Check if provider already registered.
        if $OCC user_oidc:provider:list 2>/dev/null | grep -q "Authentik"; then
          echo "OIDC provider already configured."
          exit 0
        fi

        $OCC user_oidc:provider Authentik \
          --clientid="$NEXTCLOUD_OIDC_CLIENT_ID" \
          --clientsecret="$NEXTCLOUD_OIDC_CLIENT_SECRET" \
          --discoveryuri="https://auth.${domain}/application/o/nextcloud/.well-known/openid-configuration" \
          --mapping-uid="preferred_username" \
          --unique-uid=0

        echo "Nextcloud OIDC provider configured."
      '';
      RemainAfterExit = false;
    };
  };

  # Configure nginx to listen on the internal port so Caddy can own :80/:443.
  services.nginx.virtualHosts."cloud.${domain}" = {
    listen = [
      {
        addr = "127.0.0.1";
        inherit (config.lanbat.services.nextcloud) port;
        ssl = false;
      }
    ];
  };

  # External storage paths (created when NFS is mounted).
  systemd.tmpfiles.rules = [
    "d ${bulkDir}          0750 nextcloud nextcloud -"
    "d ${bulkDir}/external 0750 nextcloud nextcloud -"
    "d ${bulkDir}/users    0750 nextcloud nextcloud -"
  ];
}
