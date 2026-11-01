# services/romm.nix
#
# RomM — web ROM manager: browse the ROM library, scrape its metadata and
# artwork, and play in the browser.
#
# On-demand: yes (modules/wiring/on-demand.nix). Caddy routes to the
# activator, which starts RomM on the first request; it stops after 30
# minutes idle.
#
# Storage:
#   /var/lib/romm/config/     — config.yml (seeded on first start, then edited
#                               from RomM's settings)
#   /var/lib/romm/resources/  — scraped artwork and metadata
#   /var/lib/romm/assets/     — uploaded saves, states and screenshots
#   /srv/storage/<drive>/<libraryPath>
#                             — the ROM library on the Pi, in ES-DE's layout
#                               (roms/<system>), shared with EmulationStation
#                               on the TV. qBittorrent saves ROM torrents there.
#   /srv/storage/<drive>/<browserArcadePath>
#                             — zip copies of the arcade sets, which RomM shows
#                               in place of roms/mame (below)
#   Workload PostgreSQL        — "romm" database
#   Shared Redis               — database 2 (lanbat.redis.databases)
#
# Settings (lanbat.services.romm.settings): drive, libraryPath and
# browserArcadePath place the library on Pi storage; the defaults are drive b,
# media/roms and media/roms-browser/mame.
#
# Auth: RomM's own accounts. The first visit runs RomM's setup wizard, which
# creates the admin account, and every Authentik account then gets its own
# RomM user on first OIDC login (matched by email, created when none exists).
# Roles come from Authentik's groups: "authentik Admins" maps to RomM admin,
# everyone else to a normal user. romm-admin-email keeps the wizard's admin on
# the Authentik admin's email (settings.authentikAdmin, default akadmin), so
# akadmin's OIDC login lands on it rather than creating a second account.
# Apps such as Argosy Launcher pair by code (apiClients = true, no forward
# auth — clients need direct API access). Password login stays as a fallback.
#
# Arcade games in the browser
# ---------------------------
# The library's MAME sets are 7z, which the browser emulator's arcade cores
# can't read, and a game needing a BIOS (Neo Geo) only finds it inside its own
# archive. romm-browser-romsets builds a zip of each set with its parent and
# BIOS sets' files (pkgs/romm-browser-romsets), hourly, and RomM's mame folder
# is that directory. In RomM's player, pick the FinalBurn Neo core once per
# browser: RomM's default arcade core, MAME 2003, crashes on these sets.
# Dreamcast games can't run in the browser; the TV plays them.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  rommUrl = "https://${config.lanbat.services.romm.subdomain}.${domain}";
  port = 8098;
  # gunicorn behind the container's nginx. Its default, 5000, is Frigate's on
  # the host network.
  backendPort = 8100;
  workloadDb = (config.lanbat.postgresql.instance "workload");

  cfg = config.lanbat.services.romm.settings;

  # Where the server mounts a Pi storage drive (modules/wiring/nfs.nix).
  onDrive = path: "/srv/storage/${cfg.drive}/${path}";
  library = onDrive cfg.libraryPath;
  browserArcade = onDrive cfg.browserArcadePath;

  rommSettings = {
    options = {
      drive = lib.mkOption {
        type = lib.types.str;
        default = "b";
        description = ''
          Pi storage drive holding the ROM library and the browser's arcade
          copies, by its key in the storage host's storage.drives. RomM and
          romm-browser-romsets bind to that drive's NFS mount.
        '';
      };
      libraryPath = lib.mkOption {
        type = lib.types.str;
        default = "media/roms";
        description = ''
          The ROM library, in ES-DE's layout (<system>/), relative to the
          drive's mount (/srv/storage/<drive>).
        '';
      };
      authentikAdmin = lib.mkOption {
        type = lib.types.str;
        default = "akadmin";
        description = ''
          Authentik user whose email romm-admin-email copies to RomM's admin
          account, so that user's OIDC login lands on the admin.
        '';
      };
      browserArcadePath = lib.mkOption {
        type = lib.types.str;
        default = "media/roms-browser/mame";
        description = ''
          Where romm-browser-romsets writes the zip copies of the arcade sets
          that RomM shows in place of the library's mame folder, relative to
          the drive's mount.
        '';
      };
    };
  };
  browserRomsets = pkgs.callPackage ../pkgs/romm-browser-romsets { };

  # romm-admin-email reads Authentik's database, so it only exists alongside
  # Authentik on the same host.
  syncsAdminEmail = config.lanbat.hasService "authentik";
  alwaysOnDb = config.lanbat.postgresql.instance "always-on";
  psql = "${config.services.postgresql.finalPackage}/bin/psql -X -v ON_ERROR_STOP=1 -tA";

  # ES-DE's folder names are RomM's platform names, except atari800. bios is
  # RetroArch's BIOS folder (modules/storage/tv-box.nix), not a platform.
  seedConfig = pkgs.writeText "romm-config.yml" ''
    exclude:
      platforms:
        - "bios"
      roms:
        single_file:
          extensions:
            - "xml"
            - "txt"
          names:
            - "info.txt"
            - "metadata.txt"
            - "systeminfo.txt"
            - "._*"
            # qBittorrent's partial pieces and unfinished files
            - ".*.parts"
            - "*.!qB"
        multi_file:
          names:
            - "roms"
            - ".Trash*"
    system:
      platforms:
        atari800: "atari8bit"
  '';
in
{
  # The schema is merged into lanbat.services.romm.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.romm = rommSettings;

  lanbat.services.romm = {
    subdomain = "romm";
    inherit port;
    extraPorts = [ backendPort ];
    auth = "app";
    apiClients = true; # Argosy Launcher and other apps call the API directly
    oidc.redirectPaths = [ "/api/oauth/openid" ];
    tier = "workload";
    state = [ "romm" ];
    units = [ "podman-romm" ] ++ lib.optional syncsAdminEmail "romm-admin-email";
    workloadDirs =
      lib.genAttrs [ "romm/config" "romm/assets" ] (_: {
        user = "romm";
      })
      // {
        # The image's nginx has a distinct in-container UID. It needs to
        # traverse these host-backed directories to serve scraped artwork.
        "romm" = {
          user = "romm";
          mode = "0711";
        };
        "romm/resources" = {
          user = "romm";
          mode = "0711";
        };
      };
    nfs = {
      drives = [ cfg.drive ];
      units = [
        "podman-romm"
        "romm-browser-romsets"
      ];
    };
    onDemand = {
      activatorPort = 8094;
      idleMinutes = 30;
    };
    account = {
      uid = 965;
      container = true;
      # The library on the Pi belongs to qbt, group media (modules/storage/storage.nix).
      extraGroups = [ "media" ];
    };
    secrets = {
      # POSTGRES_PASSWORD for the database setup, DB_PASSWD for RomM.
      romm-db-pass = {
        group = "postgres";
        mode = "0440";
      };
      # ROMM_AUTH_SECRET_KEY and the metadata provider keys.
      romm-env = { };
      # OIDC_CLIENT_SECRET, the same value as AUTHENTIK_ROMM_CLIENT_SECRET.
      romm-oidc-env = { };
    };
    dashboard = {
      group = "Media";
      name = "RomM";
      description = "ROM library (on-demand)";
    };
  };

  lanbat.postgresql.databases.romm = {
    instance = "workload";
    passwordFile = config.lanbat.secrets.romm-db-pass.path;
  };

  # Task queues and cache in the shared Redis (services/redis.nix).
  lanbat.redis.databases.romm.index = 2;

  virtualisation.oci-containers.containers."romm" = {
    image = "docker.io/rommapp/romm:5.4.1@sha256:8677fb7b81b1d3db20b849e989a1dadc2a11159e51eb34a2acdcb01102274edf";

    environment = {
      ROMM_PORT = toString port;
      # The entrypoint also points nginx's upstream at it.
      DEV_PORT = toString backendPort;
      ROMM_BASE_URL = rommUrl;
      ROMM_SESSION_SECURE_COOKIE = "true";
      ROMM_DB_DRIVER = "postgresql";
      DB_HOST = "127.0.0.1";
      DB_PORT = toString workloadDb.port;
      DB_NAME = "romm";
      DB_USER = "romm";
      # The shared Redis: RomM's internal Valkey would listen on 6379 too.
      REDIS_HOST = "127.0.0.1";
      REDIS_PORT = toString config.services.redis.servers.shared.port;
      REDIS_DB = toString config.lanbat.redis.databases.romm.index;
      HASHEOUS_API_ENABLED = "true";
      LAUNCHBOX_API_ENABLED = "true";
      # Picks up the zips romm-browser-romsets adds or replaces.
      ENABLE_RESCAN_ON_FILESYSTEM_CHANGE = "true";
      TZ = config.lanbat.deployment.timezone;

      OIDC_ENABLED = "true";
      OIDC_PROVIDER = "authentik";
      OIDC_CLIENT_ID = "romm";
      OIDC_REDIRECT_URI = "${rommUrl}/api/oauth/openid";
      OIDC_SERVER_APPLICATION_URL = "https://auth.${domain}/application/o/romm";
      OIDC_TLS_CACERTFILE = "/etc/ssl/lanbat/ca-root.crt";
      # Every Authentik account gets its own RomM user on first OIDC login:
      # RomM matches by email, then creates one when none matches. The role
      # comes from Authentik's groups — members of "authentik Admins" are RomM
      # admins, everyone else a normal user. Password login stays as the
      # fallback.
      OIDC_ALLOW_REGISTRATION = "true";
      OIDC_CLAIM_ROLES = "groups";
      OIDC_ROLE_ADMIN = "authentik Admins";
      OIDC_ROLE_VIEWER = "*";
      DISABLE_USERPASS_LOGIN = "false";
    };
    environmentFiles = [
      config.lanbat.secrets.romm-db-pass.path
      config.lanbat.secrets.romm-env.path
      config.lanbat.secrets.romm-oidc-env.path
    ];

    volumes = [
      "/var/lib/romm/config:/romm/config"
      "/var/lib/romm/resources:/romm/resources"
      "/var/lib/romm/assets:/romm/assets"
      "${library}:/romm/library/roms"
      # After the library, so it covers the library's mame folder.
      "${browserArcade}:/romm/library/roms/mame"
      "/etc/caddy/ca-root.crt:/etc/ssl/lanbat/ca-root.crt:ro"
    ];

    extraOptions = [
      # Reaches PostgreSQL and Redis on the server's loopback, like Bitmagnet.
      "--network=host"
      # Keeps the media group, for the library on the Pi.
      "--group-add=keep-groups"
    ];

    podman.user = "romm";
    user = "0";
    # The on-demand activator starts it.
    autoStart = false;
  };

  systemd.services."podman-romm" = {
    after = [ workloadDb.unit ];
    requires = [ workloadDb.unit ];
    preStart = ''
      if [ ! -e /var/lib/romm/config/config.yml ]; then
        install -m 0644 ${seedConfig} /var/lib/romm/config/config.yml
      fi
      # podman won't mount a missing directory; it fills on the next build.
      install -d -m 2775 ${browserArcade}
    '';
    serviceConfig = {
      Restart = lib.mkForce "on-failure";
      RestartSec = "10s";
    };
  };

  # Copies the Authentik admin's email to RomM's admin account (see the top)
  # at unlock and each time RomM starts. While RomM runs without an admin (the
  # first visit, before its setup wizard finishes), it waits and applies the
  # email once the wizard creates one. It changes nothing while Authentik has
  # no email for the user, or when RomM stops before it has an admin.
  systemd.services.podman-romm.wants = lib.mkIf syncsAdminEmail [ "romm-admin-email.service" ];
  systemd.services.romm-admin-email = lib.mkIf syncsAdminEmail {
    description = "Give RomM's admin the Authentik admin's email";
    after = [
      "podman-romm.service"
      alwaysOnDb.unit
      workloadDb.unit
    ];
    requires = [
      alwaysOnDb.unit
      workloadDb.unit
    ];
    serviceConfig = {
      Type = "oneshot";
      # The superuser logs in over both instances' sockets (peer).
      User = "postgres";
    };
    script = ''
      set -euo pipefail
      authentik() { ${psql} -h ${alwaysOnDb.socket} -p ${toString alwaysOnDb.port} -d authentik "$@"; }
      romm() { ${psql} -h ${workloadDb.socket} -p ${toString workloadDb.port} -d romm "$@"; }

      email=$(authentik -v user=${lib.escapeShellArg cfg.authentikAdmin} <<'SQL'
      SELECT email FROM authentik_core_user WHERE username = :'user';
      SQL
      )
      if [ -z "$email" ]; then
        echo "Authentik user ${cfg.authentikAdmin} has no email; nothing to copy"
        exit 0
      fi

      rommRunning() { systemctl is-active --quiet podman-romm.service; }
      hasUsers() { [ "$(romm -c "SELECT to_regclass('public.users') IS NOT NULL")" = t ]; }
      admins() { romm -c "SELECT count(*) FROM users WHERE lower(role::text) = 'admin'"; }

      # While RomM runs, wait for its tables and for its setup wizard to
      # create the admin, so the first visit needs nothing else.
      while rommRunning && ! { hasUsers && [ "$(admins)" != 0 ]; }; do
        sleep 10
      done
      if ! hasUsers; then
        echo "RomM hasn't created its tables yet; nothing to change"
        exit 0
      fi

      admins=$(admins)
      if [ "$admins" != 1 ]; then
        echo "RomM has $admins admin accounts, not one; changing none"
        exit 0
      fi
      taken=$(romm -v email="$email" <<'SQL'
      SELECT count(*) FROM users WHERE email = :'email' AND lower(role::text) <> 'admin';
      SQL
      )
      if [ "$taken" != 0 ]; then
        echo "Another RomM account already has the Authentik admin's email; not changing the admin" >&2
        exit 1
      fi
      changed=$(romm -v email="$email" <<'SQL'
      WITH updated AS (
        UPDATE users SET email = :'email'
        WHERE lower(role::text) = 'admin' AND email IS DISTINCT FROM :'email'
        RETURNING 1
      )
      SELECT count(*) FROM updated;
      SQL
      )
      if [ "$changed" = 1 ]; then
        echo "RomM's admin now has the Authentik admin's email"
      else
        echo "RomM's admin already has the Authentik admin's email"
      fi
    '';
  };

  # Zip copies of the arcade sets for the browser player (see the top).
  systemd.services.romm-browser-romsets = {
    description = "Build RomM's browser copies of the arcade romsets";
    environment = {
      SOURCE_DIR = "${library}/mame";
      TARGET_DIR = browserArcade;
      FBNEO_DAT = browserRomsets.dat;
      SEVENZIP = "${pkgs.p7zip}/bin/7z";
    };
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${browserRomsets}/bin/romm-browser-romsets";
      User = "romm";
      # The library's group, so the zips are readable like the rest of it.
      Group = "media";
      UMask = "0002";
      PrivateTmp = true;
      Nice = 19;
      IOSchedulingClass = "idle";
    };
  };

  systemd.timers.romm-browser-romsets = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10min";
      OnUnitActiveSec = "1h";
    };
  };
}
