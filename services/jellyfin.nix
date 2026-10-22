# services/jellyfin.nix
#
# Jellyfin media server.
#
# Why the NixOS module?
#   `services.jellyfin` is well-maintained, handles the user/group, config
#   dir, and service lifecycle cleanly.  No reason to containerize.
#
# Storage split
# -------------
# Server-local:
#   /var/lib/jellyfin/            — config, database, metadata, posters
#   /var/cache/jellyfin/          — transcodes (safe to delete at any time)
#
# Pi-backed via NFS, media split across both drives by folder:
#   /srv/storage/a/media/         — movies, TV, music videos
#   /srv/storage/b/media/         — music, documentaries, books, etc.
#   /srv/storage/b/media/adult/   — private group only; excluded from Jellyfin
#
# NFS dependency: strong.
#   Jellyfin should not run if Pi storage is unavailable — it would
#   write error states into its database and display a broken library.
#   We declare a hard BindsTo dependency so systemd stops Jellyfin when
#   the mount disappears and restarts it when the mount returns.
#
# Discovery
# ---------
# TV and mobile apps find the server via UDP broadcast on port 7359 (not mDNS).
# Caddy serves HTTPS on 443; discovery must advertise that URL so clients do
# not fall back to the blocked local HTTP port 8096.
#
# First-run onboarding, media libraries, plugins, and Authentik SSO are
# completed automatically by jellyfin-bootstrap.
#
# Metadata
# --------
# Jellyfin itself fetches metadata only for movies, TV and music (TMDb, OMDb,
# MusicBrainz, TheAudioDB). jellyfin-bootstrap adds the official Bookshelf
# plugin (Google Books, Comic Vine) for Books and, with settings.imvdb, the
# IMVDb plugin for Music Videos, which needs an API key from imvdb.com in
# secrets/jellyfin-imvdb-env.age. Installing either fetches its library's
# metadata again. Audiobooks get no online metadata here, and a book in several
# files shows as one item per file.
#
# Subtitles
# ---------
# The Open Subtitles plugin is installed by jellyfin-bootstrap. With
# settings.opensubtitles, the account credentials from
# secrets/jellyfin-opensubtitles-env.age (OPENSUBTITLES_USERNAME,
# OPENSUBTITLES_PASSWORD) are provisioned and written into the plugin's
# configuration so Jellyfin can download subtitles automatically.
#
# That bootstrap logs in to Home Assistant and configures OIDC against
# Authentik, so it only exists when the deployment runs both. Without them
# Jellyfin still serves media; it just keeps its own accounts and is not
# registered with Home Assistant.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  bootstrap = pkgs.callPackage ../pkgs/jellyfin-bootstrap { };

  # The bootstrap reads secrets that home-assistant.nix and authentik own, so
  # it can only exist when those services are part of the deployment.
  integrates = config.lanbat.hasService "home-assistant" && config.lanbat.hasService "authentik";

  cfg = config.lanbat.services.jellyfin.settings;

  # Jellyfin 10.11.7 rejects every subtitle it downloads with "Invalid subtitle
  # format: srt": it checks ".srt" against a list of formats kept without the
  # dot. The Open Subtitles plugin still counts each attempt against the
  # account's daily allowance, so the "Download missing subtitles" task uses it
  # all up and saves nothing. Fixed upstream in 10.11.8 (jellyfin/jellyfin#16539);
  # the patch lapses by itself once nixpkgs ships that.
  jellyfin =
    if lib.versionOlder pkgs.jellyfin.version "10.11.8" then
      pkgs.jellyfin.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [
          (pkgs.fetchpatch {
            name = "jellyfin-fix-subtitle-saving.patch";
            url = "https://github.com/jellyfin/jellyfin/commit/f51c63e244436944d5269085a1bed1e56db7a78e.diff";
            hash = "sha256-03twZ67OAM1lHyUldRHh/efGyruHvD6kUxOvdydV6uo=";
          })
        ];
      })
    else
      pkgs.jellyfin;

  jellyfinSettings = {
    options.imvdb = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Fetch music video metadata and artwork from IMVDb. jellyfin-bootstrap
        installs the IMVDb plugin and gives it IMVDB_API_KEY from
        secrets/jellyfin-imvdb-env.age (a free key from imvdb.com).
      '';
    };
    options.opensubtitles = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Configure the Open Subtitles plugin with an opensubtitles.com account.
        jellyfin-bootstrap installs the plugin unconditionally and, when this
        is true, sets its username and password from
        secrets/jellyfin-opensubtitles-env.age (OPENSUBTITLES_USERNAME,
        OPENSUBTITLES_PASSWORD) so Jellyfin can download subtitles
        automatically.
      '';
    };
    options.dlna.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Let Jellyfin play to DLNA speakers and screens on the LAN: "Play on"
        in Jellyfin's apps lists them, and a movie or song starts on the Echo
        Shows (techo5Devices, which switch their DLNA renderer on), or on any
        other renderer in dlna.renderers. jellyfin-bootstrap installs the DLNA
        plugin and turns on its play-to side; false turns it off again (the
        plugin stays installed). The firewall lets only the renderers' own
        addresses reach Jellyfin's discovery and HTTP ports, so the plugin's
        media-server side (it has no switch of its own) is seen by them alone.
      '';
    };
    options.dlna.server = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Also be a DLNA media server for the whole LAN: opens discovery (UDP
        1900) and Jellyfin's plain HTTP port (8096) to every address, so TVs,
        speakers and apps browse and play the libraries without signing in
        (DLNA has no logins; the plugin shows them DefaultUserId's, or the first
        user's, libraries), and anyone on the LAN reaches Jellyfin over HTTP
        without Caddy in front. Off unless you want that; casting to a renderer
        needs none of it.
      '';
    };
    options.dlna.renderers = lib.mkOption {
      type = lib.types.listOf (lib.types.strMatching "[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+");
      default = [ ];
      example = [ "192.168.1.60" ];
      description = ''
        IPv4 addresses of DLNA renderers other than the Echo Shows, which are
        taken from techo5Devices. Jellyfin's discovery and event ports are open
        to these addresses and no others.
      '';
    };
  };

  # The DLNA renderers: the Echo Shows that take part, and any listed.
  dlnaRenderers = lib.unique (
    lib.mapAttrsToList (_: d: d.address) (
      lib.filterAttrs (_: d: d.dlna) (config.lanbat.deployment.techo5Devices or { })
    )
    ++ cfg.dlna.renderers
  );
  # Discovery is SSDP (UDP, multicast and the answers to it), and a renderer
  # reports what it is doing to Jellyfin's own port.
  dlnaRules = lib.concatMap (ip: [
    "INPUT -p udp -s ${ip} -j ACCEPT"
    "INPUT -p tcp -s ${ip} --dport ${toString config.lanbat.services.jellyfin.port} -j ACCEPT"
  ]) (lib.optionals cfg.dlna.enable dlnaRenderers);
in
{
  # The schema is merged into lanbat.services.jellyfin.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.jellyfin = jellyfinSettings;

  lanbat.services.jellyfin = {
    subdomain = "media";
    port = 8096;
    extraPorts = [ 7359 ]; # UDP auto-discovery for TV and mobile apps
    apiClients = true; # TV and mobile apps
    # The SSO Authentication plugin, configured by jellyfin-bootstrap.
    oidc.redirectPaths = [ "/sso/OID/redirect/authentik" ];
    # jellyfin-bootstrap takes the owner account from Home Assistant's
    # bootstrap secret and its OIDC client secret from Authentik's.
    readsSecrets = lib.optionals integrates [
      "hass-bootstrap-env"
      "authentik-oidc-secrets"
    ];
    # IMVDB_API_KEY, for jellyfin-bootstrap (root).
    secrets.jellyfin-imvdb-env.enable = cfg.imvdb && integrates;
    # OPENSUBTITLES_USERNAME and OPENSUBTITLES_PASSWORD, for jellyfin-bootstrap.
    secrets.jellyfin-opensubtitles-env.enable = cfg.opensubtitles && integrates;
    tier = "workload";
    state = [ "jellyfin" ];
    units = [
      "jellyfin"
    ]
    ++ lib.optional integrates "jellyfin-bootstrap";
    consumes = lib.optionals integrates [
      "home-assistant"
      "authentik"
    ];
    workloadDirs."jellyfin".user = "jellyfin";
    nfs.drives = [
      "a"
      "b"
    ];
    account = {
      uid = 992;
      extraGroups = [ "media" ];
    };
    dashboard = {
      group = "Media";
      name = "Jellyfin";
      description = "Media server";
      widget = {
        type = "jellyfin";
        key = {
          _secret = "JELLYFIN_API_KEY";
        };
      };
    };
  };

  services.jellyfin = {
    enable = true;
    package = jellyfin;
    openFirewall = false; # Caddy handles exposure.
  };

  systemd.tmpfiles.rules = [
    "d /var/cache/jellyfin    0750 jellyfin jellyfin -"
  ];

  systemd.services.jellyfin = {
    serviceConfig = {
      Restart = "on-failure";
      RestartSec = "15s";
      Environment = [
        "JELLYFIN_PublishedServerUrl=https://media.${domain}"
        # The SSO plugin fetches Authentik's discovery document and tokens
        # from https://auth.<domain>; trust the internal Caddy CA (global
        # environment.variables do not reach units).
        "SSL_CERT_FILE=/var/lib/caddy-local-ca/ca-certificates.crt"
      ];
    };
  };

  networking.firewall.allowedUDPPorts = [
    7359
  ]
  ++ lib.optional (cfg.dlna.enable && cfg.dlna.server) 1900;
  networking.firewall.allowedTCPPorts = lib.optional (
    cfg.dlna.enable && cfg.dlna.server
  ) config.lanbat.services.jellyfin.port;
  networking.firewall.extraCommands = lib.concatLines (map (r: "iptables -I ${r}") dlnaRules);
  networking.firewall.extraStopCommands = lib.concatLines (
    map (r: "iptables -D ${r} 2>/dev/null || true") dlnaRules
  );

  systemd.services.jellyfin-bootstrap = lib.mkIf integrates {
    description = "Complete Jellyfin setup (wizard, libraries, plugins, SSO)";
    wantedBy = [ "multi-user.target" ];
    after = [ "jellyfin.service" ];
    wants = [ "jellyfin.service" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "root";
      Restart = "on-failure";
      RestartSec = "30s";
    };

    path = [ bootstrap ];

    script = ''
      set -a
      . ${config.lanbat.secrets.hass-bootstrap-env.path}
      . ${config.lanbat.secrets.authentik-oidc-secrets.path}
      ${lib.optionalString cfg.imvdb ". ${config.lanbat.secrets.jellyfin-imvdb-env.path}"}
      ${lib.optionalString cfg.opensubtitles ". ${config.lanbat.secrets.jellyfin-opensubtitles-env.path}"}
      set +a
      export JELLYFIN_URL="http://127.0.0.1:8096"
      export EXTERNAL_URL="https://media.${domain}"
      export AUTH_DOMAIN="${domain}"
      export DLNA_ENABLE="${if cfg.dlna.enable then "1" else "0"}"
      exec jellyfin-bootstrap
    '';
  };
}
