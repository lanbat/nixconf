# services/invidious.nix
#
# Invidious — privacy-respecting front-end for YouTube.
#
# Auth: forward-auth, but only for the personal pages.
#   Invidious has no OIDC, so Authentik cannot sign a user into it: accounts,
#   if used, are Invidious's own.  What Authentik gates are the routes that
#   expose those accounts — preferences, subscriptions, history, playlists and
#   the account pages.  Browsing, watching, search and the API stay open to the
#   LAN (caddy.authBypassPaths), so the front-end works without a login.
#
# Always-on: yes, by choice.
#   Its database holds cached video/channel metadata and, if someone creates
#   an account, subscriptions and watch history. That is mild personal data,
#   so — like Home Assistant and Authentik — it starts at boot instead of
#   waiting for unlock-workload. To keep it encrypted at rest instead, set
#   tier = "workload", list "invidious" in state/units, and move
#   lanbat.postgresql.databases.invidious to the "workload" instance (which
#   then also removes the password below: the workload instance uses peer auth).
#
# Database:
#   The "invidious" database lives on the always-on PostgreSQL instance (port
#   5433). The NixOS module wants the password as a bare value in
#   database.passwordFile, while lanbat.postgresql's passwordFile is a
#   POSTGRES_PASSWORD=... shell file, so the two cannot be the same file. The
#   secret here is the bare password: invidious-db-password sets it on the
#   role once the instance is up, and the module reads it from a systemd
#   credential, so it never reaches the Nix store.
#
# YouTube is a moving target: playback can break when YouTube changes its
# client checks.  Invidious companion (upstream image, on localhost)
# mints the PO token and resolves the stream URLs; without it modern Invidious
# still browses but cannot play videos.  services.invidious.sig-helper and
# http3-ytproxy are older alternatives and stay off: sig-helper runs
# JavaScript downloaded from YouTube.
{
  config,
  pkgs,
  lib,
  inputs,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  port = 8083;
  alwaysOn = config.lanbat.postgresql.instance "always-on";

  # Pinned from nixpkgs-invidious rather than this flake's nixpkgs, like
  # ZigbeeMQTT.  2.20260207.0 (what the pinned nixpkgs carries) sends Innertube
  # requests that YouTube rejects with HTTP 400: trending and search return
  # nothing, comments fail, while suggestions still work.  The 2026-07-23
  # release follows YouTube's backend changes and 2026-08-04 works.
  #
  # The override lapses by itself once nixpkgs ships a newer Invidious, and is
  # skipped for a flake that consumes lanbat without the nixpkgs-invidious
  # input.
  pinnedInvidious =
    if inputs ? nixpkgs-invidious then
      inputs.nixpkgs-invidious.legacyPackages.${pkgs.stdenv.hostPlatform.system}.invidious
    else
      pkgs.invidious;
  usePinned =
    inputs ? nixpkgs-invidious && lib.versionOlder pkgs.invidious.version pinnedInvidious.version;

in
{
  lanbat.services.invidious = {
    subdomain = "youtube";
    inherit port;
    auth = "forward-auth";
    # Anonymous browsing, watching, search and the API stay open; everything
    # else (preferences, account, subscriptions, history, playlists) goes
    # through Authentik.  Paths use Caddy's prefix matcher (`*`).
    caddy.authBypassPaths = [
      "/"
      "/watch*"
      "/embed*"
      "/shorts*"
      "/clip*"
      "/channel*"
      "/playlist*"
      "/mix*"
      "/hashtag*"
      "/search*"
      "/redirect*"
      "/feed/trending*"
      "/feed/popular*"
      "/feed/top*"
      "/api/*"
      "/videoplayback*"
      "/latest_version*"
      "/token_ajax*"
      # Image, storyboard and caption proxies.
      "/vi*"
      "/ggpht*"
      "/sb*"
      "/storyboards*"
      "/caption*"
      # Static assets the pages need before anything else loads.
      "/css*"
      "/js*"
      "/fonts*"
      "/assets*"
      "/favicon*"
      "/apple-touch-icon*"
      "/robots.txt"
      "/opensearch.xml"
    ];
    secrets = {
      # A bare password, one line. postgresql-always-on-setup creates the
      # role; invidious-db-password sets this on it, and the Invidious module
      # reads it from the systemd credential below. Group postgres so the
      # oneshot can read it as that user.
      invidious-db-pass = {
        owner = "root";
        group = "postgres";
        mode = "0440";
      };
    };
    dashboard = {
      group = "Utilities";
      name = "Invidious";
      description = "YouTube front-end";
    };
  };

  # Always-on instance, so it is up at boot with the rest of Invidious.
  lanbat.postgresql.databases.invidious = {
    instance = "always-on";
  };

  services.invidious = {
    enable = true;
    package = if usePinned then pinnedInvidious else pkgs.invidious;
    address = "127.0.0.1";
    port = config.lanbat.services.invidious.port;
    domain = "youtube.${domain}";

    database = {
      # Managed by lanbat.postgresql above, not a private instance.
      createLocally = false;
      host = "127.0.0.1";
      port = alwaysOn.port;
      # Read through the systemd credential below, never the Nix store.
      passwordFile = "$CREDENTIALS_DIRECTORY/invidious-db-password";
    };

    settings = {
      # Caddy terminates TLS on 443.
      https_only = true;
      external_port = 443;
      # Companion runs on localhost, so Invidious proxies playback itself.
      invidious_companion = [
        { private_url = "http://127.0.0.1:8282/companion"; }
      ];
    };

    # Holds invidious_companion_key. Generated at boot (below) rather than
    # stored in the Nix store; the module merges it into the config.
    extraSettingsFile = "/run/invidious/companion-settings.json";
  };

  systemd.services.invidious = {
    after = [
      alwaysOn.unit
      "invidious-db-password.service"
      "podman-invidious-companion.service"
    ];
    requires = [
      alwaysOn.unit
      "invidious-db-password.service"
      "invidious-companion-key.service"
    ];
    wants = [ "podman-invidious-companion.service" ];
    serviceConfig = {
      LoadCredential = [
        "invidious-db-password:${config.lanbat.secrets.invidious-db-pass.path}"
      ];
      # The companion settings file below lives here, owned by the service.
      RuntimeDirectory = "invidious";
      # Runs as root (the + prefix), after systemd has allocated the dynamic
      # user, so it can hand the file to it.
      ExecStartPre = "+${pkgs.writeShellScript "invidious-companion-settings" ''
        set -euo pipefail
        key="$(< /run/invidious-companion/key)"
        printf '{"invidious_companion_key":"%s"}\n' "$key" \
          > /run/invidious/companion-settings.json
        chown invidious:invidious /run/invidious/companion-settings.json
        chmod 0400 /run/invidious/companion-settings.json
      ''}";
    };
  };

  # ---------------------------------------------------------------------------
  # Invidious companion
  # ---------------------------------------------------------------------------
  # Modern Invidious refuses to play videos without companion: it mints the
  # PO token YouTube asks for and resolves the googlevideo URLs.  Upstream
  # publishes it only as an image; run that, pinned by the immutable tag's
  # digest.  (`deno compile` embeds a V8 snapshot whose bytes differ between
  # builds, so a native package cannot have a fixed-output hash.)
  #
  # The port is published on 127.0.0.1 only; Invidious proxies the streams, so
  # there is no public URL and no Caddy vhost.
  #
  # The shared key is 16 alphanumeric characters and is generated once per
  # boot, so it never reaches the Nix store.  Invidious reads it from its
  # extraSettingsFile, the container from an env file.
  systemd.services.invidious-companion-key = {
    description = "Generate the Invidious companion shared key";
    wantedBy = [ "multi-user.target" ];
    before = [
      "invidious.service"
      "podman-invidious-companion.service"
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      RuntimeDirectory = "invidious-companion";
      RuntimeDirectoryMode = "0700";
    };
    script = ''
      set -euo pipefail
      key=/run/invidious-companion/key
      if [ ! -s "$key" ]; then
        ${pkgs.pwgen}/bin/pwgen 16 1 > "$key"
      fi
      chmod 0600 "$key"
      printf 'SERVER_SECRET_KEY=%s\n' "$(cat "$key")" \
        > /run/invidious-companion/companion.env
      chmod 0600 /run/invidious-companion/companion.env
    '';
  };

  virtualisation.oci-containers.containers."invidious-companion" = {
    # Tag 2026.10.18-fcb0456, the commit behind upstream's image; the digest
    # keeps a moved tag harmless.
    image = "quay.io/invidious/invidious-companion@sha256:5642790b67c2ce655a630dbff1342051bd98674abc8e469551117a3d4b6bedcc";
    environment = {
      # The published port below is the only listener on the host.
      HOST = "0.0.0.0";
      PORT = "8282";
    };
    environmentFiles = [ "/run/invidious-companion/companion.env" ];
    ports = [ "127.0.0.1:8282:8282" ];
    capabilities = {
      ALL = false;
    };
    extraOptions = [ "--security-opt=no-new-privileges" ];
  };

  systemd.services."podman-invidious-companion" = {
    after = [
      "network-online.target"
      "invidious-companion-key.service"
    ];
    wants = [ "network-online.target" ];
    requires = [ "invidious-companion-key.service" ];
  };

  # Set the role's password after the always-on instance has created it. The
  # module's database.passwordFile is a bare value, so this cannot use
  # lanbat.postgresql's POSTGRES_PASSWORD=... file directly.
  systemd.services.invidious-db-password = {
    description = "Set the Invidious PostgreSQL password";
    after = [ alwaysOn.unit ];
    requires = [ alwaysOn.unit ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "postgres";
    };
    script = ''
      set -euo pipefail
      # Via stdin so psql interpolates :'pw' even on older versions.
      echo "ALTER ROLE invidious WITH PASSWORD :'pw';" | \
        ${config.services.postgresql.finalPackage}/bin/psql \
          -h ${alwaysOn.socket} -p ${toString alwaysOn.port} -d postgres \
          -v pw="$(cat ${config.lanbat.secrets.invidious-db-pass.path})"
    '';
  };
}
