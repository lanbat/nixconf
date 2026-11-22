# services/paperless.nix
#
# Paperless-ngx — document archive: scans and PDFs are OCR'd, tagged and made
# searchable.
#
# Why the NixOS module?
#   `services.paperless` runs the upstream web server, task queue, scheduler and
#   consumer as units under one user; there is no reason to containerize.
#
# Storage (workload layer, /var/lib/paperless):
#   data/index    — search index (regenerable)
#   media/        — the archived documents and thumbnails (back this up)
#   consume/      — the inbox: anything dropped here is imported, then removed
#   PostgreSQL    — the workload instance, database "paperless"
#   Shared Redis  — database 3 (lanbat.redis.databases), task queue and cache
#
# Import of the existing documents:
#   The household's documents sit in each person's folder under
#   /srv/storage/<drive>/<usersPath>/<person>/ on the Pi (the SMB home
#   directories). paperless-import-users COPIES the document files in them
#   (never moves or deletes: the Pi stays as it was) into consume/<person>/,
#   at every unlock and nightly for files added since. The consumer tags each
#   document with the person's name and the folders it was in
#   (PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS). A ledger of what was copied keeps a
#   document the consumer has already imported (and removed from consume/) from
#   being copied again, and Paperless's own checksum check drops exact
#   duplicates.
#
# Settings (lanbat.services.paperless.settings): drive and usersPath place the
# folders to import.
#
# Auth: Authentik OIDC (django-allauth, provider id "authentik"). Anyone in the
# Authentik group "paperless" signs in and gets an account on first login;
# the username/password login stays as the break-glass way in for the admin.
# No forward auth: the mobile apps and scanners talk to the API directly.
#
# Secrets
# -------
# paperless-env.age — KEY=value lines (read by systemd as EnvironmentFile):
#   PAPERLESS_ADMIN_USER=<name>        (optional, creates the superuser)
#   PAPERLESS_ADMIN_PASSWORD=<password>
#   PAPERLESS_SOCIALACCOUNT_PROVIDERS=<single line of JSON>, for example
#     {"openid_connect":{"OAUTH_PKCE_ENABLED":true,"APPS":[{"provider_id":"authentik","name":"Authentik","client_id":"paperless","secret":"<value>","settings":{"server_url":"https://auth.<domain>/application/o/paperless/.well-known/openid-configuration"}}]}}
#   <value> is the same as AUTHENTIK_PAPERLESS_CLIENT_SECRET in
#   authentik-oidc-secrets.age (secrets/generate-oidc-secrets.sh writes both).
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  port = 28981;
  cfg = config.lanbat.services.paperless.settings;
  workloadDb = config.lanbat.postgresql.instance "workload";
  dataDir = "/var/lib/paperless";
  consumeDir = "${dataDir}/consume";
  ledger = "${dataDir}/import-ledger";

  # Where the server mounts a Pi storage drive (modules/wiring/nfs.nix).
  usersDir = "/srv/storage/${cfg.drive}/${cfg.usersPath}";

  paperlessSettings = {
    options = {
      drive = lib.mkOption {
        type = lib.types.str;
        default = "b";
        description = ''
          Pi storage drive holding the people's folders, by its key in the
          storage host's storage.drives. Paperless binds to that drive's NFS
          mount.
        '';
      };
      usersPath = lib.mkOption {
        type = lib.types.str;
        default = "users";
        description = ''
          Folder holding one subfolder per person, relative to the drive's
          mount (/srv/storage/<drive>). Documents in it are copied into
          Paperless and tagged with the person's name; the originals are
          left alone.
        '';
      };
    };
  };

  # Documents Paperless can read without Tika.
  extensions = [
    "pdf"
    "png"
    "jpg"
    "jpeg"
    "tif"
    "tiff"
    "webp"
    "gif"
    "txt"
    "csv"
    "md"
  ];

  # The workload instance, and Redis, before the units that use them.
  unitsAfterDeps =
    lib.genAttrs
      [
        "paperless-web"
        "paperless-task-queue"
        "paperless-scheduler"
        "paperless-consumer"
      ]
      (_: {
        after = [
          workloadDb.unit
          "redis-shared.service"
        ];
        requires = [ workloadDb.unit ];
      });

  importScript = pkgs.writeShellScript "paperless-import-users" ''
    set -eu
    shopt -s nullglob nocasematch
    touch ${ledger}
    exts='${lib.concatStringsSep "|" extensions}'
    for home in ${usersDir}/*/; do
      person=$(basename "$home")
      find "$home" -type f -not -path '*/.*' -print0 |
      while IFS= read -r -d "" file; do
        ext="''${file##*.}"
        [[ "$ext" =~ ^($exts)$ ]] || continue
        rel="''${file#${usersDir}/}"
        key="$(stat -c '%Y %s' "$file") $rel"
        grep -qxF -- "$key" ${ledger} && continue
        dest="${consumeDir}/$rel"
        install -d -o paperless -g paperless "$(dirname "$dest")"
        # Into a hidden name first: the consumer ignores dotfiles, so it never
        # reads a half-copied file.
        tmp="$(dirname "$dest")/.import-$(basename "$dest")"
        cp -- "$file" "$tmp"
        chown paperless:paperless "$tmp"
        mv -- "$tmp" "$dest"
        printf '%s\n' "$key" >> ${ledger}
      done
    done
  '';
in
{
  # The schema is merged into lanbat.services.paperless.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.paperless = paperlessSettings;

  lanbat.services.paperless = {
    subdomain = "docs";
    inherit port;
    auth = "app";
    # Paperless's allauth callback for the provider id "authentik".
    oidc.redirectPaths = [ "/accounts/oidc/authentik/login/callback/" ];
    access.groups = [ "paperless" ];
    tier = "workload";
    state = [ "paperless" ];
    units = [
      "paperless-secret-key"
      "paperless-web"
      "paperless-task-queue"
      "paperless-scheduler"
      "paperless-consumer"
      "paperless-import-users"
    ];
    # The nixpkgs module's tmpfiles rules only reach the locked stub; the
    # workload layer creates the mounted directories with their owner.
    workloadDirs = {
      "paperless".user = "paperless";
      "paperless/index".user = "paperless";
      "paperless/media".user = "paperless";
      "paperless/consume".user = "paperless";
    };
    nfs = {
      drives = [ cfg.drive ];
      units = [ "paperless-import-users" ];
    };
    # No account.uid: the nixpkgs module pins the paperless user's UID (315),
    # and nothing of Paperless's writes to the Pi, where a UID would matter.
    secrets.paperless-env = { };
    dashboard = {
      group = "Utilities";
      name = "Paperless";
      description = "Document archive";
    };
  };

  lanbat.postgresql.databases.paperless.instance = "workload";

  # Task queue and cache in the shared Redis (services/redis.nix).
  lanbat.redis.databases.paperless.index = 3;

  # The module reaches Authentik through python requests, which trusts the
  # system bundle; add the internal CA Caddy issues auth.<domain> from.
  security.pki.certificateFiles = [ config.lanbat.deployment.secrets.caCertificate ];

  services.paperless = {
    enable = true;
    address = "127.0.0.1";
    inherit port dataDir;
    environmentFile = config.lanbat.secrets.paperless-env.path;
    settings = {
      PAPERLESS_URL = "https://${config.lanbat.services.paperless.subdomain}.${domain}";
      PAPERLESS_DBENGINE = "postgresql";
      PAPERLESS_DBHOST = workloadDb.socket;
      PAPERLESS_DBPORT = workloadDb.port;
      PAPERLESS_DBNAME = "paperless";
      PAPERLESS_DBUSER = "paperless";
      PAPERLESS_REDIS = "redis://127.0.0.1:${toString config.services.redis.servers.shared.port}/${toString config.lanbat.redis.databases.paperless.index}";
      PAPERLESS_APPS = "allauth.socialaccount.providers.openid_connect";
      # An Authentik user in the group gets an account on first login.
      PAPERLESS_SOCIAL_AUTO_SIGNUP = true;
      PAPERLESS_SOCIALACCOUNT_ALLOW_SIGNUPS = true;
      # The people's folders and sub-folders become tags.
      PAPERLESS_CONSUMER_RECURSIVE = true;
      PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS = true;
      PAPERLESS_CONSUMER_DELETE_DUPLICATES = true;
      PAPERLESS_OCR_LANGUAGE = "eng";
    };
  };

  systemd.services = unitsAfterDeps // {
    paperless-import-users = {
      description = "Copy the people's documents from Pi storage into Paperless's inbox";
      # Starts with the layer (units above); the timer repeats it nightly.
      after = [ "paperless-consumer.service" ];
      path = [
        pkgs.coreutils
        pkgs.findutils
        pkgs.gnugrep
      ];
      serviceConfig = {
        Type = "oneshot";
        # Reads the people's folders over NFS as root (the export keeps root);
        # writes the copies as the paperless user.
        ExecStart = importScript;
      };
    };
  };

  systemd.timers.paperless-import-users = {
    # Only while the workload layer is unlocked, like the service it starts.
    wantedBy = [ "workload-online.target" ];
    partOf = [ "workload-online.target" ];
    timerConfig = {
      OnCalendar = "03:30";
      RandomizedDelaySec = "30min";
    };
  };
}
