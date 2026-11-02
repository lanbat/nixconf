# services/immich.nix
#
# Immich — self-hosted photo/video library.
#
# Storage split
# -------------
# Server-local (always-on):
#   /var/lib/postgresql/          — PostgreSQL data (shared instance, vectorchord)
#   /var/lib/immich/model-cache/  — ML model cache (~4 GB on first run)
#   Redis (services.redis.servers.immich)
#
# Pi-backed via NFS (/srv/storage/<drive>):
#   /srv/storage/<drive>/<uploadPath>/ — Immich's media root. Immich v3 keeps
#   originals, thumbnails, encoded video and profile pictures all under
#   UPLOAD_LOCATION, so they share this one Pi-backed tree.
#   settings.drive and settings.uploadPath place them; by default a and
#   photos.
#
# NFS dependency: partial.
#   - If Pi is down: Immich is still up, new uploads fail, existing
#     thumbnails (server-local) still load.
#   - The immich-server container binds the upload directory.
#     We declare that dependency so Immich stops if the mount disappears.
#
# PostgreSQL, Redis and Immich's own API are reached on the loopback ports of
# the workload instance, the shared Redis and the service's description.
#
# Auth with Authentik
# -------------------
# Browser access is gated by Caddy forward-auth (Authentik session), like Home
# Assistant.  Immich v3 reads OAuth settings from IMMICH_CONFIG_FILE (not env
# vars); a preStart hook writes that file from immich-oidc-env.age.
# Mobile apps keep using /api/* with Immich credentials (apiClients bypass).
#
# Immich requires a local admin record before OAuth auto-launch works; the
# immich-bootstrap oneshot creates that admin (email must match Authentik) and
# password login is disabled so the browser only uses OIDC.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  immichVersion = "v3.2.1";
  immichDigests = {
    server = "sha256:87bb1b208434a8503e1a2465edd84f3cf94bd72c66feb7ca474629015b8dbfd6";
    machineLearning = "sha256:49a53dbf5fbea5c785075667056fb010498969b9143005df5434868035bf5654";
  };
  domain = config.lanbat.deployment.domain;
  bootstrap = pkgs.callPackage ../pkgs/immich-bootstrap { };

  # The bootstrap creates Immich's first admin from the owner credentials that
  # hass-bootstrap-env carries, so it only runs when Home Assistant is part of
  # the deployment. Immich itself serves fine without it; it just has no admin
  # account until someone creates one.
  bootstraps = config.lanbat.hasService "home-assistant";

  cfg = config.lanbat.services.immich.settings;
  workloadDb = config.lanbat.postgresql.instance "workload";
  # Where the server mounts the Pi storage drive (modules/wiring/nfs.nix).
  uploadDir = "/srv/storage/${cfg.drive}/${cfg.uploadPath}";

  immichSettings = {
    options = {
      drive = lib.mkOption {
        type = lib.types.str;
        default = "a";
        description = ''
          Pi storage drive holding the originals and uploads, by its key in
          the storage host's storage.drives. The server container binds to
          that drive's NFS mount.
        '';
      };
      uploadPath = lib.mkOption {
        type = lib.types.str;
        default = "photos";
        description = ''
          Directory of the originals and uploads (Immich's UPLOAD_LOCATION),
          relative to the drive's mount (/srv/storage/<drive>).
        '';
      };
    };
  };
  # immich-db-password.age exports POSTGRES_PASSWORD for postgres init; Immich v3
  # reads DB_PASSWORD at runtime.
  immichServerEnv = "/run/immich/server.env";
  immichConfigPath = "/run/immich/config.json";
  immichConfigMount = "/config/immich-config.json";
  # Immich reaches Authentik over https://auth.<domain> during OAuth discovery.
  lanbatCaBundle = "/var/lib/caddy-local-ca/ca-certificates.crt";
  lanbatCaBundleMount = "/etc/ssl/lanbat/ca-certificates.crt";
  lanbatCaRootMount = "/etc/ssl/lanbat/ca-root.crt";
in
{
  config = {
    # The schema is merged into lanbat.services.immich.settings; checks.nix
    # rejects any key it does not declare.
    lanbat.settingsSchema.immich = immichSettings;

    lanbat.deployment.immich.adminEmail = lib.mkDefault (
      "${lib.elemAt config.lanbat.homeAssistant.ssoUsers 0}@${config.lanbat.deployment.rootDomain}"
    );

    lanbat.services.immich = {
      subdomain = "photos";
      # immich-bootstrap creates the admin from Home Assistant's owner account.
      readsSecrets = lib.optional bootstraps "hass-bootstrap-env";
      port = 2283;
      extraPorts = [ 3003 ]; # machine learning
      auth = "forward-auth";
      apiClients = true; # mobile app — /api/* bypasses Authentik at Caddy
      # Browser login after the forward-auth gate, and the mobile app.
      oidc = {
        redirectPaths = [ "/auth/login" ];
        redirectUris = [ "app.immich:///oauth-callback" ];
      };
      tier = "workload";
      state = [ "immich" ];
      units = [
        "podman-immich-server"
        "podman-immich-machine-learning"
      ]
      ++ lib.optional bootstraps "immich-bootstrap";
      consumes = lib.optional bootstraps "home-assistant";
      # Podman requires volume host paths to exist before the container starts.
      workloadDirs =
        lib.genAttrs
          [
            "immich"
            "immich/model-cache"
          ]
          (_: {
            user = "immich";
          });
      # Only the server container reads the originals on Pi storage.
      nfs = {
        drives = [ cfg.drive ];
        units = [ "podman-immich-server" ];
      };
      account = {
        uid = 991;
        container = true;
      };
      secrets = {
        # group postgres: postgresql-immich-init reads the password too.
        immich-db-password = {
          group = "postgres";
          mode = "0440";
        };
        immich-oidc-env = { };
      };
      dashboard = {
        group = "Media";
        name = "Immich";
        description = "Photo library";
        widget = {
          type = "immich";
          version = 2;
          key = {
            _secret = "IMMICH_API_KEY";
          };
        };
      };
    };

    lanbat.postgresql.databases.immich = {
      instance = "workload";
      passwordFile = config.lanbat.secrets.immich-db-password.path;
      extraSql = ''
        CREATE EXTENSION IF NOT EXISTS vchord CASCADE;
        CREATE EXTENSION IF NOT EXISTS cube;
        CREATE EXTENSION IF NOT EXISTS earthdistance;
      '';
    };

    # Job queues and cache in the shared Redis (services/redis.nix).
    lanbat.redis.databases.immich.index = 1;

    # ---------------------------------------------------------------------------
    # Immich server container
    # ---------------------------------------------------------------------------
    virtualisation.oci-containers.containers."immich-server" = {
      image = "ghcr.io/immich-app/immich-server:${immichVersion}@${immichDigests.server}";
      extraOptions = [ "--network=host" ];
      podman.user = "immich";
      user = "0";
      environment = {
        DB_HOSTNAME = "127.0.0.1";
        DB_PORT = toString workloadDb.port;
        DB_USERNAME = "immich";
        DB_DATABASE_NAME = "immich";
        REDIS_HOSTNAME = "127.0.0.1";
        REDIS_PORT = toString config.services.redis.servers.shared.port;
        REDIS_DBINDEX = toString config.lanbat.redis.databases.immich.index;
        UPLOAD_LOCATION = "/usr/src/app/upload";
        IMMICH_CONFIG_FILE = immichConfigMount;
        # Node fetch for OIDC discovery does not inherit the host trust store.
        NODE_EXTRA_CA_CERTS = lanbatCaRootMount;
        SSL_CERT_FILE = lanbatCaBundleMount;
      };
      environmentFiles = [
        immichServerEnv
        config.lanbat.secrets.immich-oidc-env.path
      ];
      volumes = [
        "${uploadDir}:/usr/src/app/upload"
        "${immichConfigPath}:${immichConfigMount}:ro"
        "/etc/localtime:/etc/localtime:ro"
        "/etc/caddy/ca-root.crt:${lanbatCaRootMount}:ro"
        "${lanbatCaBundle}:${lanbatCaBundleMount}:ro"
      ];
      autoStart = true;
    };

    # ---------------------------------------------------------------------------
    # Immich machine learning container
    # ---------------------------------------------------------------------------
    virtualisation.oci-containers.containers."immich-machine-learning" = {
      image = "ghcr.io/immich-app/immich-machine-learning:${immichVersion}@${immichDigests.machineLearning}";
      extraOptions = [
        "--network=host"
        "--memory=8g"
        "--memory-reservation=6g"
        "--memory-swap=10g"
        "--cpus=2"
      ];
      podman.user = "immich";
      user = "0";
      environment = {
        # ML service binds on 3003 by default; server reaches it on localhost.
        MACHINE_LEARNING_WORKERS = "1";
        MACHINE_LEARNING_WORKER_TIMEOUT = "120";
      };
      volumes = [
        "/var/lib/immich/model-cache:/cache"
      ];
      dependsOn = [ "immich-server" ];
      autoStart = true;
    };

    systemd.services.podman-immich-server = {
      path = [ pkgs.jq ];
      preStart = ''
        set -euo pipefail
        install -d -m 0750 -o immich -g immich /run/immich
        umask 0177
        . ${config.lanbat.secrets.immich-db-password.path}
        printf 'DB_PASSWORD=%s\n' "$POSTGRES_PASSWORD" > ${immichServerEnv}
        chown immich:immich ${immichServerEnv}

        set -a
        . ${config.lanbat.secrets.immich-oidc-env.path}
        set +a
        ${pkgs.jq}/bin/jq -n \
          --arg issuer "https://auth.${domain}/application/o/immich/" \
          --arg clientId "$IMMICH_OAUTH_CLIENT_ID" \
          --arg clientSecret "$IMMICH_OAUTH_CLIENT_SECRET" \
          --arg externalDomain "https://${config.lanbat.services.immich.subdomain}.${domain}" \
          '{
            oauth: {
              enabled: true,
              issuerUrl: $issuer,
              clientId: $clientId,
              clientSecret: $clientSecret,
              scope: "openid email profile",
              buttonText: "Login with Authentik",
              autoRegister: true,
              autoLaunch: true,
              tokenEndpointAuthMethod: "client_secret_post"
            },
            passwordLogin: {
              enabled: false
            },
            server: {
              externalDomain: $externalDomain
            }
          }' > ${immichConfigPath}
        chown immich:immich ${immichConfigPath}
      '';
    };

    systemd.services.immich-bootstrap = lib.mkIf bootstraps {
      description = "Create the first Immich admin for Authentik OAuth login";
      wantedBy = [ "multi-user.target" ];
      after = [ "podman-immich-server.service" ];
      wants = [ "podman-immich-server.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "root";
      };

      path = [ bootstrap ];

      script = ''
        set -a
        . ${config.lanbat.secrets.hass-bootstrap-env.path}
        set +a
        export IMMICH_URL="http://127.0.0.1:${toString config.lanbat.services.immich.port}"
        export ADMIN_EMAIL="${config.lanbat.deployment.immich.adminEmail}"
        export ADMIN_NAME="$OWNER_USERNAME"
        export ADMIN_PASSWORD="$OWNER_PASSWORD"
        exec immich-bootstrap
      '';
    };

    # Ensure the photo path exists when NFS is mounted.
    systemd.tmpfiles.rules = [
      "d ${uploadDir} 0750 immich immich -"
      "d /run/immich 0750 immich immich -"
    ];
  };
}
