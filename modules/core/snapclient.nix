# modules/core/snapclient.nix
#
# Snapcast client — receives and plays the audio stream from the server.
#
# The server-side snapserver is in services/snapcast.nix.
#
# Audio
# -----
# Snapclient plays through the host's system-wide PipeWire (modules/pi/audio.nix
# on the Pis, modules/core/voice-satellite-audio.nix on any voice satellite),
# which mixes it with the TV sessions and the voice satellite's replies. The
# satellite turns it down while the voice assistant listens and answers.
#
# Which hosts run it (lanbat.snapclient.enable): the Pi roles bundle it
# (modules/pi/snapclient.nix; roleModules.snapclient = null drops it), and any
# other host with a Linux Voice Assistant satellite runs it too, so every room
# with a satellite and a speaker is also a Music Assistant player in that room,
# and "play ..." plays where it was asked. lanbat.voiceSatellite.playMusic =
# false keeps a satellite to spoken replies.
#
# Snapserver is wherever the profile runs snapcast: snapclient consumes it and
# takes its host from the profile-wide endpoint table rather than assuming the
# server. The port is snapserver's streaming port, which is not the endpoint
# snapcast publishes (that is its web UI), so it stays written out here.
#
# No inbound firewall changes needed — snapclient only makes outbound
# connections to snapserver on port 1704.
#
# Watchdog
# --------
# snapclient 0.35 can hang when its connection drops (a snapserver restart,
# or the server's firewall reloading during a deploy): it logs "Reconnecting",
# stops its PipeWire player and then never connects again, while systemd sees
# a healthy process, and it ignores SIGTERM. Sometimes the socket stays open
# too: on the Pi 5 the server dropped the client and the process sat for eight
# days with 690 kB unread and no traffic either way. snapclient-watchdog
# (pkgs/snapclient-watchdog) checks every minute and restarts it after two
# checks in a row in which it has no connection to snapserver, the connection
# carries nothing one way for 30 s (a working client exchanges time-sync
# messages about every second), or the socket's receive queue is stuck; the
# stop timeout is short so the hung process is killed quickly.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  endpointLib = import ../../lib/endpoints.nix { inherit lib; };

  snapserverHost = endpointLib.soleHost {
    endpoints = config.lanbat.endpoints;
    name = "snapcast";
    consumer = "snapclient on ${config.lanbat.hostKey}";
  };
  # The address the server's generated rule admits this host from: the overlay
  # name when the edge runs on the overlay, the LAN address otherwise.
  snapserver = config.lanbat.endpointHost "snapcast" snapserverHost;

  # With the voice satellite's echoCancellation.includeMusic, the music plays
  # into the echo-cancel sink (which passes it on to the speaker), so the
  # canceller can take it out of the microphone
  # (modules/core/voice-satellite-audio.nix).
  aec = config.lanbat.voiceSatellite.echoCancellation;
  musicToAec = lib.optionalString (
    config.lanbat.voiceSatellite.enable && aec.enable && aec.includeMusic
  ) " --soundcard ${aec.pulseSinkName}";
  satellite = config.lanbat.voiceSatellite;
  snapclientWatchdog = pkgs.callPackage ../../pkgs/snapclient-watchdog { };
in
{
  options.lanbat.snapclient.enable = lib.mkOption {
    type = lib.types.bool;
    default = satellite.enable && satellite.backend == "lva" && satellite.playMusic;
    defaultText = lib.literalExpression ''voiceSatellite.enable && voiceSatellite.backend == "lva" && voiceSatellite.playMusic'';
    description = ''
      Run a Snapcast client, so this host's speaker is a Music Assistant
      player. The Pi roles turn it on; on other hosts it follows the voice
      satellite.
    '';
  };

  config = lib.mkIf config.lanbat.snapclient.enable {
    lanbat.services.snapclient.consumes = [ "snapcast" ];

    # Snapclient's stream always starts at full volume: Snapserver and Music
    # Assistant own the music's volume, and the satellite only ducks it for a
    # while. WirePlumber otherwise saves a stream's volume under its role
    # (media.role Music) and gives it to the next one, so a stream opened while
    # the music was ducked (new music started by voice, a reconnect) stayed at
    # the duck level: 0.05 measured on the Pi 3, 2026-10-06.
    services.pipewire.wireplumber.extraConfig."51-snapclient-volume"."stream.rules" = [
      {
        matches = [ { "application.name" = "Snapclient"; } ];
        actions.update-props."state.restore-props" = false;
      }
    ];

    # nixos-24.11 has no services.snapclient module — run it manually.
    systemd.services.snapclient = {
      description = "Snapcast client";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network.target"
        "sound.target"
        "pipewire.socket"
      ];
      wants = [ "pipewire.socket" ];
      environment.PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
      serviceConfig = {
        ExecStart = "${pkgs.snapcast}/bin/snapclient --host ${snapserver} --port 1704 --player pipewire${musicToAec}";
        Restart = "on-failure";
        RestartSec = "5s";
        # A client hung on a lost connection ignores SIGTERM (see Watchdog); kill
        # it after 10 s rather than systemd's default 90.
        TimeoutStopSec = "10s";
        User = "snapclient";
        DynamicUser = true;
        SupplementaryGroups = [ "pipewire" ];
      };
    };

    systemd.services.snapclient-watchdog = {
      description = "Restart snapclient when it has lost snapserver";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = lib.getExe snapclientWatchdog;
      };
      environment = {
        SS = "${pkgs.iproute2}/bin/ss";
        SYSTEMCTL = "${config.systemd.package}/bin/systemctl";
        STATE = "/run/snapclient-watchdog";
      };
    };

    systemd.timers.snapclient-watchdog = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "3min";
        OnUnitActiveSec = "1min";
      };
    };
  };
}
