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
# Always-on: a search is on the voice path, and answering one is quick. It
# holds no state: the list a person is choosing from is the router's.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  name = "yt-show";
  port = 8102;
  ytShow = pkgs.callPackage ../pkgs/yt-show { };

  hasRouter = config.lanbat.hasService "assistant-router";
  # Screens that take a video at all (techo5Devices.<room>.dlna, on by default).
  screens = lib.filterAttrs (_: d: d.dlna) (config.lanbat.deployment.techo5Devices or { });
  enabled = hasRouter && screens != { };
  invidiousHere = config.lanbat.hasService "invidious";

  # yt-dlp runs a JavaScript runtime for YouTube's signature challenges
  # (pkgs/media-feed-bridge does the same).
  path = lib.makeBinPath [
    pkgs.yt-dlp
    pkgs.nodejs-slim
  ];
in
{
  config = lib.mkMerge [
    # Described whenever the module is imported (services.nix): the port only
    # while it runs, so a host with no screen publishes no endpoint to consume.
    {
      lanbat.services.${name} = {
        port = lib.mkIf enabled port;
        tier = "always-on";
        units = lib.optional enabled name;
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
          ExecStart = lib.concatStringsSep " " (
            [
              (lib.getExe ytShow)
              "serve"
              "--host 127.0.0.1"
              "--port ${toString port}"
              "--ytdlp ${lib.getExe pkgs.yt-dlp}"
            ]
            # Invidious is on this host (the server takes every service), so
            # the loopback address is enough to ask it. The Show fetches the
            # stream from its own https domain: it trusts that certificate, and
            # a loopback address would reach the Show's own machine.
            ++ lib.optionals invidiousHere [
              "--invidious http://127.0.0.1:${toString config.lanbat.services.invidious.port}"
              "--invidious-public https://${config.lanbat.services.invidious.subdomain}.${config.lanbat.deployment.domain}"
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
