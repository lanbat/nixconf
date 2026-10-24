# services/yt-show.nix
#
# YouTube for the Echo Show: a small HTTP helper the assistant router asks for
# a search, and for the one H.264 MP4 the screen plays (pkgs/yt-show,
# docs/techo5.md). Invidious first — its address is on our own domain, which
# the Show already trusts, and it does not expire — and yt-dlp when Invidious
# is off, down, or cannot play that video.
#
# It is the only part of the voice path that needs the internet: the router's
# sandbox admits the loopback only, so resolving a video happens here
# (services/assistant-router.nix).
#
# Many videos have no single-file 720p stream any more (itag 22), only 360p
# (itag 18). For those, the helper downloads a 720p H.264 stream and an m4a
# track and remuxes them into one MP4 (pkgs/yt-show remux.py), keeping it in a
# cache that the screen fetches over https://yt.<domain>. The API itself stays
# on the loopback; the media app is the only thing Caddy proxies.
#
# Always-on: a search is on the voice path, and answering one is quick.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;

  name = "yt-show";
  mediaName = "yt-show-media";
  port = 8102;
  mediaPort = 8103;

  ytShow = pkgs.callPackage ../pkgs/yt-show { };

  hasRouter = config.lanbat.hasService "assistant-router";
  # Screens that take a video at all (techo5Devices.<room>.dlna, on by default).
  screens = lib.filterAttrs (_: d: d.dlna) (config.lanbat.deployment.techo5Devices or { });
  enabled = hasRouter && screens != { };
  invidiousHere = config.lanbat.hasService "invidious";
  domain = config.lanbat.deployment.domain;

  cfg = config.lanbat.services.${name}.settings;

  ytShowSettings.options = {
    remux = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Make a 720p MP4 for a video Invidious has only as 360p: download the
        720p video and m4a audio streams and remux them (stream copy, no
        re-encode). Off: the 360p address plays as it is.
      '';
    };
    maxMinutes = mkOption {
      type = types.numbers.positive;
      default = 15;
      description = ''
        The longest video to remux. A longer one plays as 360p rather than keep
        the screen waiting.
      '';
    };
    remuxSeconds = mkOption {
      type = types.numbers.positive;
      default = 30;
      description = ''
        How long a remux may take before the 360p address is played instead. The
        person is waiting at the screen; raise it only if the link is slow.
      '';
    };
    cacheDays = mkOption {
      type = types.numbers.positive;
      default = 7;
      description = "How long a remuxed file is kept in /var/cache/yt-show.";
    };
    maxCacheMB = mkOption {
      type = types.numbers.positive;
      default = 4096;
      description = "The cache's size limit; the oldest files go first.";
    };
  };

  # yt-dlp runs a JavaScript runtime for YouTube's signature challenges
  # (pkgs/media-feed-bridge does the same), and ffmpeg joins the DASH pair.
  path = lib.makeBinPath [
    pkgs.yt-dlp
    pkgs.nodejs-slim
    pkgs.ffmpeg-headless
  ];
in
{
  config = lib.mkMerge [
    { lanbat.settingsSchema.${name} = ytShowSettings; }

    {
      # The router's API, on the loopback, and the media app Caddy serves to
      # the screens. Both described whenever the module is imported; the ports
      # only while it runs, so a host with no screen publishes nothing.
      lanbat.services.${name} = {
        port = lib.mkIf enabled port;
        tier = "always-on";
        units = lib.optional enabled name;
      };
      lanbat.services.${mediaName} = {
        subdomain = lib.mkIf enabled "yt";
        port = lib.mkIf enabled mediaPort;
        # Cached video files only: the LAN is trusted for media, as for
        # Invidious and Jellyfin, and the API is not on this vhost.
        auth = "none";
      };
    }

    (lib.mkIf enabled {
      systemd.services.${name} = {
        description = "YouTube for the Echo Show: a search, and the MP4 it can play";
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ] ++ lib.optional invidiousHere "invidious.service";
        wants = [ "network-online.target" ] ++ lib.optional invidiousHere "invidious.service";
        serviceConfig = {
          DynamicUser = true;
          # The remuxed files; the media app serves them from here.
          CacheDirectory = name;
          ExecStart = lib.concatStringsSep " " (
            [
              (lib.getExe ytShow)
              "serve"
              "--host 127.0.0.1"
              "--port ${toString port}"
              "--media-host 127.0.0.1"
              "--media-port ${toString mediaPort}"
              "--media-url-base https://yt.${domain}"
              "--cache-dir /var/cache/${name}"
              "--max-minutes ${toString cfg.maxMinutes}"
              "--remux-seconds ${toString cfg.remuxSeconds}"
              "--cache-days ${toString cfg.cacheDays}"
              "--max-cache-mb ${toString cfg.maxCacheMB}"
              "--ytdlp ${lib.getExe pkgs.yt-dlp}"
              "--ffmpeg ${lib.getExe pkgs.ffmpeg-headless}"
            ]
            ++ lib.optional (!cfg.remux) "--no-remux"
            # Invidious is on this host (the server takes every service), so
            # the loopback address is enough to ask it. The Show fetches the
            # stream from its own https domain: it trusts that certificate, and
            # a loopback address would reach the Show's own machine.
            ++ lib.optionals invidiousHere [
              "--invidious http://127.0.0.1:${toString config.lanbat.services.invidious.port}"
              "--invidious-public https://${config.lanbat.services.invidious.subdomain}.${domain}"
            ]
          );
          Environment = [ "PATH=${path}" ];
          Restart = "always";
          RestartSec = "2s";
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateDevices = true;
          PrivateTmp = true;
          NoNewPrivileges = true;
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
            "AF_UNIX"
          ];
        };
      };
    })
  ];
}
