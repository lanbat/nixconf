# services/music-assistant.nix
#
# Music Assistant — music library controller and multi-room orchestrator.
#
# Why the NixOS module and not a container?
#   `services.music-assistant` exists in pinned nixpkgs (2.7.x) and runs as a
#   native systemd service on the host network stack — no rootless Podman
#   multicast caveats.  CONTRIBUTING.md prefers native modules when available.
#
# Snapcast integration (distribution layer)
# -----------------------------------------
# Snapcast stays declarative in services/snapcast.nix (snapserver on 1704/1705).
# Music Assistant does NOT write to the old FIFO.  When the Snapcast player
# provider is enabled in the MA UI with "Use existing Snapserver", MA connects
# to snapserver's control API (port 1705) and creates per-playback TCP streams
# dynamically via stream_add_stream — see music_assistant/providers/snapcast/
# player.py::_get_or_create_stream in the nixpkgs package source.
#
# Post-deploy: Settings → Player Providers → Snapcast → enable "Use existing
# Snapserver", host 127.0.0.1, control port 1705.  Do NOT use MA's built-in
# snapserver (it would bind the same ports).
#
# Sendspin (builtin WebRTC player provider)
# -----------------------------------------
# MA 2.7.x treats "sendspin" as builtin (builtin=true, allow_disable=false).
# It runs an internal WebRTC signalling server on loopback port 8927 that the
# webserver's sendspin_proxy forwards to from the public MA port (8095/8097).
# Without "sendspin" in the providers list aiosendspin is not installed and the
# provider fails to load at startup, breaking the web UI's browser audio player
# and logging a RuntimeError on every restart.  No extra firewall rules are
# needed: external clients connect through the existing 8095/8097 ports.
#
# Local music library
# -------------------
# The filesystem_local provider reads the music library over NFS; its path is
# set in Music Assistant's UI (docs/deployment-checklist.md), not here. That
# path is Pi-backed (automount, not workload-gated).  MA is always-on:
# we deliberately avoid lanbat.services.*.nfs.drives (which would stop MA when
# the Pi disappears).  The service starts without the mount; library scans fail
# gracefully until NFS is available.
#
# Auth with Home Assistant
# ----------------------
# Browser access to music.<domain> is gated by Caddy forward-auth (Authentik).
# Music Assistant has no Authentik header passthrough like Home Assistant, so
# users sign in via "Login with Home Assistant" (HA OAuth) after Authentik.
# music-assistant-setup provisions the hass plugin, base URL, self-registration,
# and the bidirectional long-lived tokens for the HA integration. It reaches
# both services on the loopback ports, and names them by the subdomains, of
# their descriptions.
#
# Audio sources
# -------------
# music-assistant-sources (after music-assistant-setup, which creates the admin
# it signs in as) makes Music Assistant match the settings below; it is safe to
# run again, so it also repairs a provider removed in the UI:
#   - Radio: the RadioBrowser provider (tens of thousands of stations worldwide,
#     Bulgarian ones included), Radio Paradise, BBC Sounds (live BBC radio and
#     on-demand shows, no login needed) and, with settings.tuneinUsername,
#     TuneIn. settings.stations puts stations in the library by name, found
#     through RadioBrowser or TuneIn, or by stream URL.
#   - Podcasts: iTunes Podcast Search (the settings.podcastCountry charts and
#     search) and one RSS subscription per settings.podcasts URL.
#   - YouTube, PeerTube, LBRY/Odysee, Vimeo: Music Assistant has no provider
#     for them, so media-feed-bridge (pkgs/media-feed-bridge) turns each
#     settings.channels entry into an audio podcast feed on loopback, with
#     yt-dlp, and the sources step subscribes the RSS provider to it. Any
#     channel, playlist or user URL yt-dlp understands works. YouTube Music
#     (Settings → Music Providers) needs a login cookie, so it is left to the UI.
#   - Audiobookshelf: Audiobookshelf is workload-gated and Music Assistant will
#     not add a provider whose server is unreachable, so
#     music-assistant-audiobookshelf is one of Audiobookshelf's gated units: at
#     each unlock it adds the provider, signed in as the owner account that
#     audiobookshelf-bootstrap creates, or reloads it.
# All of it needs the Home Assistant integration (the owner account).
#
# Always-on: yes.  State in /var/lib/music-assistant (provider config, playlists).
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  inherit (config.lanbat.services) music-assistant home-assistant;
  setup = pkgs.callPackage ../pkgs/music-assistant-setup {
    inherit pkgs;
  };

  # The setup step registers Music Assistant with Home Assistant and configures
  # OAuth login against it. Without Home Assistant there is nothing to register
  # with, and Music Assistant still plays music.
  integrates = config.lanbat.hasService "home-assistant";

  # The service's opt-in settings, rendered by this module.
  cfg = config.lanbat.services.music-assistant.settings;

  sources = pkgs.callPackage ../pkgs/music-assistant-sources { };
  bridge = pkgs.callPackage ../pkgs/media-feed-bridge { };
  # The BBC Sounds provider's library, which nixpkgs does not package.
  auntieSounds = pkgs.callPackage ../pkgs/auntie-sounds { };
  maPackage = config.services.music-assistant.package.override {
    inherit (config.services.music-assistant) providers;
  };

  # Audiobookshelf's bootstrap creates the owner account that the provider
  # signs in as, and exists only with Home Assistant and Authentik.
  audiobookshelfLinked =
    integrates && config.lanbat.hasService "audiobookshelf" && config.lanbat.hasService "authentik";
  audiobookshelfUnit = "music-assistant-audiobookshelf";

  # The bridge and its yt-dlp updater share a state directory (the downloaded
  # yt-dlp) by running as the same dynamic user.
  bridgeSandbox = {
    DynamicUser = true;
    User = "media-feed-bridge";
    StateDirectory = "media-feed-bridge";
    # yt-dlp runs untrusted-site extractors and a JavaScript runtime: keep it
    # to the network, with nothing of the host to read or write.
    NoNewPrivileges = true;
    CapabilityBoundingSet = "";
    ProtectSystem = "strict";
    ProtectHome = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectControlGroups = true;
    RestrictAddressFamilies = [
      "AF_INET"
      "AF_INET6"
    ];
    RestrictNamespaces = true;
    LockPersonality = true;
    RestrictSUIDSGID = true;
    SystemCallArchitectures = "native";
    MemoryMax = "1G";
  };

  # media-feed-bridge serves the video channels on loopback (a claim, below,
  # so that no other service takes the port).
  bridgePort = 8101;
  bridgeFeeds = "http://127.0.0.1:${toString bridgePort}/feed/";
  bridgeConfig = builtins.toFile "media-feed-bridge.json" (
    builtins.toJSON {
      port = bridgePort;
      limit = cfg.channelEpisodes;
      feeds = lib.mapAttrs (_: channel: channel.url) cfg.channels;
    }
  );

  # Providers Music Assistant gets an instance of, with the values that need
  # setting. Others are added in its UI.
  sourceProviders = {
    radiobrowser = { };
    itunes_podcasts.locale = cfg.podcastCountry;
  }
  // lib.optionalAttrs cfg.bbcSounds { bbc_sounds = { }; }
  // lib.optionalAttrs cfg.radioParadise { radioparadise = { }; }
  // lib.optionalAttrs (cfg.tuneinUsername != null) { tunein.username = cfg.tuneinUsername; };

  sourcesConfig = builtins.toFile "music-assistant-sources.json" (
    builtins.toJSON (
      {
        providers = sourceProviders;
        inherit (cfg) podcasts stations;
        bridge = {
          prefix = bridgeFeeds;
          feeds = lib.attrNames cfg.channels;
        };
      }
      // lib.optionalAttrs audiobookshelfLinked {
        audiobookshelf.url = "http://127.0.0.1:${toString config.lanbat.services.audiobookshelf.port}";
      }
    )
  );

  # Music Assistant reaches the snapserver over loopback (SNAPSERVER_HOST in
  # pkgs/music-assistant-setup defaults to 127.0.0.1), and snapserver pulls each
  # playback's audio back from it, so the two must share a host until #117.
  snapcastHosts = (config.lanbat.endpoints.snapcast or { hosts = [ ]; }).hosts;
in
{
  assertions = [
    {
      assertion = snapcastHosts == [ ] || lib.elem config.lanbat.hostKey snapcastHosts;
      message =
        "music-assistant runs on ${config.lanbat.hostKey}, but snapcast runs on "
        + "${lib.concatStringsSep ", " snapcastHosts}: Music Assistant only works with the "
        + "snapserver on its own host (lanbat/nixconf#117).";
    }
    {
      assertion = lib.all (name: builtins.match "[a-z0-9-]+" name != null) (lib.attrNames cfg.channels);
      message =
        "music-assistant: the names in settings.channels become feed URLs and may only hold "
        + "lowercase letters, digits and dashes, but there are: "
        + lib.concatStringsSep ", " (lib.attrNames cfg.channels);
    }
    {
      assertion = integrates || (cfg.stations == [ ] && cfg.podcasts == [ ] && cfg.channels == { });
      message =
        "music-assistant: settings.stations, settings.podcasts and settings.channels are added "
        + "through the admin account that music-assistant-setup creates, which needs Home "
        + "Assistant on the host.";
    }
  ];

  # Opt-in settings, declared as typed keys of lanbat.services.music-assistant.settings.
  lanbat.settingsSchema.music-assistant = {
    options.stations = lib.mkOption {
      type = lib.types.listOf (
        lib.types.submodule {
          options = {
            name = lib.mkOption {
              type = lib.types.str;
              example = "BG Radio";
              description = ''
                The station's name. Without a url it is looked up in RadioBrowser
                (then TuneIn) and must match a station's name exactly, ignoring case.
              '';
            };
            country = lib.mkOption {
              type = lib.types.nullOr (lib.types.strMatching "[A-Za-z]{2}");
              default = null;
              example = "BG";
              description = ''
                A two-letter country code, for a name several countries share
                ("Magic FM", "N-JOY"): RadioBrowser is then asked for that country's
                most popular station of that name. Without it, the most popular
                match in the world wins, which is rarely the one you meant.
              '';
            };
            url = lib.mkOption {
              type = lib.types.nullOr lib.types.str;
              default = null;
              example = "https://playerservices.streamtheworld.com/api/livestream-redirect/BG_RADIOAAC_L.aac";
              description = ''
                The stream's URL, for a station the radio directories lack or list
                under another name. Music Assistant names it from the stream's own
                metadata.
              '';
            };
          };
        }
      );
      default = [ ];
      description = ''
        Radio stations to have in Music Assistant's library, besides browsing and
        searching RadioBrowser, TuneIn and BBC Sounds. A station already in the
        library is left alone.
      '';
    };

    options.podcasts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "https://feeds.bbci.co.uk/programmes/p02nrsln/podcasts.rss" ];
      description = ''
        RSS feeds of podcasts to subscribe to. PeerTube channels and LBRY/Odysee
        publish ones of their own; for YouTube and Vimeo, which do not, see
        `channels`.
      '';
    };

    options.channels = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule {
          options.url = lib.mkOption {
            type = lib.types.strMatching "https?://.+";
            example = "https://www.youtube.com/@NixOS/videos";
            description = ''
              A channel, user or playlist page that yt-dlp can list: YouTube, a
              PeerTube instance, Odysee (LBRY), Vimeo, and the many other sites it
              supports.
            '';
          };
        }
      );
      default = { };
      example = {
        nixos.url = "https://www.youtube.com/@NixOS/videos";
      };
      description = ''
        Video channels to listen to as podcasts, by a short name (lowercase
        letters, digits and dashes). Music Assistant has no provider for these
        sites, so media-feed-bridge serves each one's newest videos as an audio
        podcast feed on loopback, resolving a video's audio only when it plays.
        An entry removed from here is unsubscribed.
      '';
    };

    options.channelEpisodes = lib.mkOption {
      type = lib.types.ints.between 1 500;
      default = 50;
      description = "How many of a channel's newest videos its feed lists.";
    };

    options.updateYtDlp = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Keep yt-dlp, which `channels` depend on, working: sites break it every few
        weeks, long before nixpkgs carries the fix. The bridge looks daily for a
        newer release on GitHub, checks the download against the release's
        SHA2-256SUMS, and installs it only if it is an improvement: tried again
        beside the yt-dlp in use on the configured `channels` (list each, resolve
        its newest video's audio, download the start of it), it must play every
        channel the old one does and at least one more. Otherwise the old one
        stays, so a working yt-dlp is never swapped for an equal or a different
        one. The packaged yt-dlp is used until there is a download. Turn it off to
        use only the packaged version.
      '';
    };

    options.podcastCountry = lib.mkOption {
      type = lib.types.strMatching "[a-z]{2}";
      default = "us";
      example = "bg";
      description = "Country (two-letter, lowercase) of the iTunes podcast charts and search.";
    };

    options.bbcSounds = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Add the BBC Sounds provider: live BBC radio, shows and podcasts.";
    };

    options.radioParadise = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Add the Radio Paradise provider (ad-free, human-curated).";
    };

    options.tuneinUsername = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        A TuneIn username, which adds the TuneIn provider (global radio, sports and
        podcasts) with that account's presets. Null leaves TuneIn out; RadioBrowser
        covers international radio without an account.
      '';
    };

    options.fanartTvVip = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Apply a Fanart.tv VIP API key so the fanart.tv metadata provider is not
        rate-limited. music-assistant-setup gives it the key from
        secrets/ma-fanarttv-key.age (a single line holding the key).
      '';
    };
  };

  lanbat.services.music-assistant = {
    subdomain = "music";
    # music-assistant-setup signs in with Home Assistant's owner account.
    readsSecrets = lib.optional integrates "hass-bootstrap-env";
    # Fanart.tv VIP key, read by the root music-assistant-setup unit.
    secrets.ma-fanarttv-key = {
      enable = cfg.fanartTvVip && integrates;
      owner = "root";
    };
    port = 8095;
    extraPorts = [
      8097 # MA stream server (players / imageproxy)
      bridgePort # media-feed-bridge, loopback only
    ];
    auth = "forward-auth";
    consumes = lib.optional integrates "home-assistant";
    # The web UI probes /info and opens /ws before Music Assistant's own login.
    # Static assets and API paths must also bypass Authentik or the SPA shows
    # "Connect" / "Connection Lost" after the shell page loads.
    caddy.authBypassPaths = [
      "/info"
      "/ws"
      "/setup"
      "/auth/*"
      "/api"
      "/api/*"
      "/assets/*"
      "/resources/*"
      "/favicon.ico"
      "/manifest.json"
      "/logo.png"
      "/sw.js"
      "/workbox-*"
    ];
    caddy.proxyOptions = ''
      transport http {
        keepalive 24h
      }
    '';
    account = {
      uid = 964;
      extraGroups = [ "media" ];
    };
    dashboard = {
      group = "Media";
      name = "Music Assistant";
      description = "Multi-room music controller";
    };
  };

  services.music-assistant = {
    enable = true;
    openFirewall = false; # Caddy handles the web UI; 8095 stays closed on the firewall.
    providers = [
      "snapcast"
      "filesystem_local"
      "sendspin" # builtin WebRTC player; see comment above
      # Audio sources (see above); instances are added by music-assistant-sources.
      "radiobrowser"
      "tunein"
      "radioparadise"
      "podcastfeed"
      "itunes_podcasts"
    ]
    ++ lib.optional cfg.bbcSounds "bbc_sounds"
    ++ lib.optional audiobookshelfLinked "audiobookshelf";
  };

  # Upstream uses DynamicUser; override to a pinned account in the media group
  # so filesystem_local can read NFS-mounted tracks (0750, group media).
  systemd.services.music-assistant = {
    # The module sets PYTHONPATH to the providers' libraries; add BBC Sounds's.
    environment.PYTHONPATH = lib.mkIf cfg.bbcSounds (
      lib.mkForce "${maPackage.pythonPath}:${pkgs.python3Packages.makePythonPath [ auntieSounds ]}"
    );
    after = [
      "network-online.target"
      "snapserver.service"
    ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "music-assistant";
      Group = "music-assistant";
      SupplementaryGroups = [ "media" ];
      # HA OAuth token exchange calls https://ha.<domain> server-side; trust the
      # internal Caddy CA (global environment.variables do not reach systemd units).
      Environment = [
        "SSL_CERT_FILE=/var/lib/caddy-local-ca/ca-certificates.crt"
        "REQUESTS_CA_BUNDLE=/var/lib/caddy-local-ca/ca-certificates.crt"
      ];
      # Do not bind the NFS library path here — systemd fails to start when
      # the Pi automount is not yet available.  Scans fail gracefully instead.
    };
  };

  systemd.services.music-assistant-setup = lib.mkIf integrates {
    description = "Configure Music Assistant Home Assistant integration and OAuth login";
    wantedBy = [ "multi-user.target" ];
    after = [
      "music-assistant.service"
      "home-assistant.service"
      "home-assistant-bootstrap.service"
    ];
    wants = [
      "music-assistant.service"
      "home-assistant.service"
    ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "root";
    };

    path = with pkgs; [
      setup
      sudo
      config.services.home-assistant.package
    ];

    script = ''
      set -a
      . ${config.lanbat.secrets.hass-bootstrap-env.path}
      set +a
      export MA_URL="http://127.0.0.1:${toString music-assistant.port}"
      export MA_PUBLIC_URL="https://${music-assistant.subdomain}.${domain}"
      export HA_INTERNAL_URL="http://127.0.0.1:${toString home-assistant.port}"
      export HA_PUBLIC_URL="https://${home-assistant.subdomain}.${domain}"
      export HASS_BIN="${config.services.home-assistant.package}/bin/hass"
      export HASS_CONFIG="/var/lib/hass"
      # Music Assistant's Snapcast players come from the snapserver (services/snapcast.nix).
      export SNAPSERVER_CONTROL_PORT="${toString config.services.snapserver.settings.tcp-control.port}"
      ${lib.optionalString (
        cfg.fanartTvVip && integrates
      ) "export MA_FANARTTV_KEY=\"$(cat ${config.lanbat.secrets.ma-fanarttv-key.path})\""}
      exec music-assistant-setup
    '';
  };

  # Video channels as audio podcast feeds (pkgs/media-feed-bridge).
  systemd.services.media-feed-bridge = lib.mkIf (cfg.channels != { }) {
    description = "Video channels as audio podcast feeds for Music Assistant";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = bridgeSandbox // {
      ExecStart = "${bridge}/bin/media-feed-bridge ${bridgeConfig}";
      Restart = "on-failure";
      RestartSec = "10s";
    };
  };

  # The newest yt-dlp release, once it does as well on these channels as the one
  # in use. The bridge runs yt-dlp afresh for every request, so a release that
  # was installed is used at once, without a restart.
  systemd.services.media-feed-bridge-update-yt-dlp =
    lib.mkIf (cfg.channels != { } && cfg.updateYtDlp)
      {
        description = "Update yt-dlp for media-feed-bridge, if the new release plays more of the channels";
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        serviceConfig = bridgeSandbox // {
          Type = "oneshot";
          ExecStart = "${bridge}/bin/media-feed-bridge-update-yt-dlp ${bridgeConfig}";
          TimeoutStartSec = "20min"; # a few yt-dlp runs per channel, twice
        };
      };
  systemd.timers.media-feed-bridge-update-yt-dlp = lib.mkIf (cfg.channels != { } && cfg.updateYtDlp) {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitInactiveSec = "1d";
      RandomizedDelaySec = "30min";
    };
  };

  # Radio, podcasts and video channels (see "Audio sources" above).
  systemd.services.music-assistant-sources = lib.mkIf integrates {
    description = "Connect Music Assistant to radio, podcasts and video channels";
    wantedBy = [ "multi-user.target" ];
    after = [
      "music-assistant-setup.service"
      "network-online.target"
    ]
    ++ lib.optional (cfg.channels != { }) "media-feed-bridge.service";
    requires = [ "music-assistant-setup.service" ];
    wants = [
      "network-online.target"
    ]
    ++ lib.optional (cfg.channels != { }) "media-feed-bridge.service";
    environment = {
      MA_URL = "http://127.0.0.1:${toString music-assistant.port}";
      MA_SOURCES_CONFIG = "${sourcesConfig}";
    };
    path = [ sources ];
    serviceConfig = {
      # Not oneshot: the first run adds every station, which takes minutes, and
      # a deploy should not wait for it.
      Type = "simple";
      RemainAfterExit = true;
      User = "root";
      # The radio directories may be unreachable at boot; everything else was
      # done by then, so trying again is cheap.
      Restart = "on-failure";
      RestartSec = "5min";
    };
    script = ''
      set -a
      . ${config.lanbat.secrets.hass-bootstrap-env.path}
      set +a
      exec music-assistant-sources sources
    '';
  };

  # Audiobookshelf's provider: one of Audiobookshelf's gated units, so it runs
  # at each unlock (see lanbat.services.audiobookshelf.units).
  systemd.services.${audiobookshelfUnit} = lib.mkIf audiobookshelfLinked {
    description = "Connect Music Assistant to Audiobookshelf";
    after = [
      "music-assistant-setup.service"
      "audiobookshelf-bootstrap.service"
    ];
    wants = [ "music-assistant-setup.service" ];
    requires = [ "audiobookshelf.service" ];
    environment = {
      MA_URL = "http://127.0.0.1:${toString music-assistant.port}";
      MA_SOURCES_CONFIG = "${sourcesConfig}";
    };
    path = [ sources ];
    serviceConfig = {
      # Not oneshot: the unlock does not wait for it.
      Type = "simple";
      RemainAfterExit = true;
      User = "root";
      Restart = "on-failure";
      RestartSec = "1min";
    };
    script = ''
      set -a
      . ${config.lanbat.secrets.hass-bootstrap-env.path}
      set +a
      exec music-assistant-sources audiobookshelf
    '';
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/music-assistant/.lanbat-setup 0700 music-assistant music-assistant -"
  ];
}
