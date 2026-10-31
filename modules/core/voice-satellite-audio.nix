# modules/core/voice-satellite-audio.nix
#
# The audio a Linux Voice Assistant satellite needs on any host (backend
# "lva"): a system-wide PipeWire with its PulseAudio server, a fixed mono
# capture node for the microphone, and optionally WebRTC echo cancellation.
#
# LVA's audio library (soundcard) talks to a PulseAudio server and nothing
# else, so a host without a sound server (the server) gets PipeWire here; the
# Pis already run one for Snapcast and the TV (modules/pi/audio.nix), and this
# adds to it. Clients need the pipewire group; LVA's account has it.
#
# Microphone
# ----------
# The microphone's capture node is renamed lanbat_ps_eye_capture and keeps the
# card's own channels (four on the PlayStation Eye), so the satellite's
# direction-of-arrival listener (modules/pi/stackchan.nix) can read every mic;
# LVA opens it by a stable name rather than by whatever the card calls itself
# and asks for one channel, which PipeWire mixes down. WirePlumber's device
# properties carry the USB IDs but don't reach the node, which has only ALSA's
# "USBvvvv:pppp" components string; the node rule matches that.
#
# Echo cancellation (echoCancellation.enable)
# -------------------------------------------
# libpipewire-module-echo-cancel records from the microphone node and offers
# the cancelled signal as lanbat_aec_mic, using what is played into its sink,
# lanbat_aec_playback, as the reference. LVA records from the one and plays
# its replies into the other, so its own voice is cancelled. Snapcast plays
# straight to the speaker, so music is not in the reference and is not
# cancelled, unless echoCancellation.includeMusic sends it through the sink too
# (an experiment: it costs CPU for every second of music). On a TV box Kodi
# follows includeMusic too (modules/storage/tv-box.nix).
#
# Watchdog (microphone.watchdog)
# ------------------------------
# The PlayStation Eye runs at 16 kHz on its own USB clock and follows the
# speaker's (a USB speaker at 48 kHz on the Pi 5). Now and then PipeWire's
# reads of it start failing ("snd_pcm_mmap_commit error ... Broken pipe"), a
# few a minute at first, then every one, 25 a second, for hours, until it
# resyncs by itself; meanwhile the wake word is never heard. pkgs/mic-watchdog
# reads PipeWire's log and, at 30 failures in a minute, turns the card's profile
# off and on, which reopens it cleanly, and restarts LVA, whose recording the
# profile change moves off the echo-cancelled microphone.
#
# The card can also stall without any error: it stays open and PipeWire hands
# out digital silence. The watchdog listens to the capture node every 30 s;
# three silent checks in a row reopen the card, three more enumerate the USB
# device again, and after that it says in the journal that only unplugging the
# microphone will help.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.lanbat.voiceSatellite;
  usbId = lib.splitString ":" cfg.microphone.usbId;
  captureNode = "lanbat_ps_eye_capture";
  lvaSnapcastDuck = pkgs.callPackage ../../pkgs/lva-snapcast-duck { };
  micWatchdog = pkgs.callPackage ../../pkgs/mic-watchdog { };
  ducking = cfg.lva.snapcastDucking.enable && config.lanbat.snapclient.enable;
  # The microphone LVA records from (voice-satellite-lva.nix): the echo
  # cancelled one when there is one, so the satellite's own voice isn't
  # counted as the room.
  roomMic =
    if !cfg.lva.adaptiveVolume.enable then
      null
    else if cfg.lva.audioInputDevice != null then
      cfg.lva.audioInputDevice
    else if cfg.echoCancellation.enable then
      cfg.echoCancellation.pulseSourceName
    else if cfg.microphone.usbId == "1415:2000" then
      captureNode
    else
      null;
in
{
  config = lib.mkIf (cfg.enable && cfg.backend == "lva") {
    assertions = [
      {
        assertion = !cfg.echoCancellation.includeMusic || cfg.echoCancellation.enable;
        message = "lanbat: lanbat.voiceSatellite.echoCancellation.includeMusic needs echoCancellation.enable.";
      }
    ];

    services.pulseaudio.enable = false;
    services.pipewire = {
      enable = true;
      systemWide = true;
      alsa.enable = true;
      pulse.enable = true;

      wireplumber.extraConfig."52-lva-microphone"."monitor.alsa.rules" = [
        {
          matches = [
            {
              "device.vendor.id" = "0x${lib.head usbId}";
              "device.product.id" = "0x${lib.last usbId}";
            }
          ];
          actions.update-props."device.disabled" = false;
        }
        {
          matches = [
            {
              "alsa.components" = "USB${cfg.microphone.usbId}";
              "media.class" = "Audio/Source";
            }
          ];
          actions.update-props = {
            "node.name" = captureNode;
            # The channels stay the card's own (four on the PlayStation Eye),
            # so the direction-of-arrival listener can read every mic. A
            # consumer that wants one (LVA, the echo canceller) asks for mono
            # on its own stream and PipeWire mixes it down.
          };
        }
      ];

      # WirePlumber starts a new output at 40 %; the satellite's replies and
      # the music are controlled by their own volumes, not this one.
      wireplumber.extraConfig."50-voice-satellite-output-volume"."wireplumber.settings" = {
        "device.routes.default-sink-volume" = 1.0;
      };

      extraConfig.pipewire."53-voice-aec" = lib.mkIf cfg.echoCancellation.enable {
        "context.modules" = [
          {
            name = "libpipewire-module-echo-cancel";
            args = {
              "library.name" = "aec/libspa-aec-webrtc";
              # The PlayStation Eye is mixed to mono for the canceller. The
              # module initialises all four of its streams from these two
              # keys (the default is [ FL FR ]); leaving the sink/playback
              # stereo while the capture/source are mono made WebRTC's
              # ProcessStream fail with -9, kBadNumberChannelsError, on every
              # buffer (2026-10-31).
              "audio.channels" = 1;
              "audio.position" = [ "MONO" ];
              # The module's own stream that records the microphone: named for
              # itself, aimed at the microphone node.
              "capture.props" = {
                "node.name" = "lanbat_aec_capture";
                "target.object" = captureNode;
                # The microphone node keeps its four channels; the canceller
                # works on one, so mix them down on this stream.
                "audio.channels" = 1;
                "audio.position" = [ "MONO" ];
              };
              "source.props" = {
                "node.name" = cfg.echoCancellation.pulseSourceName;
                "node.description" = "Echo-cancelled microphone";
                # WebRTC's ProcessStream needs the captured input and the
                # cancelled output to have the same channel count: the capture
                # above is mixed down to mono, so the source must be mono too.
                # A stereo source made every buffer fail with -9,
                # kBadNumberChannelsError (2026-10-31).
                "audio.channels" = 1;
                "audio.position" = [ "MONO" ];
              };
              "sink.props" = {
                "node.name" = cfg.echoCancellation.pulseSinkName;
                "node.description" = "Voice assistant replies (echo-cancel reference)";
              };
              "playback.props"."node.name" = "lanbat_aec_output";
            };
          }
        ];
      };
    };

    # The microphone reopened when PipeWire's reads of it keep failing (above).
    # Root: it reads the journal and restarts LVA.
    systemd.services.lanbat-mic-watchdog = lib.mkIf cfg.microphone.watchdog {
      description = "Reopen the satellite's microphone when PipeWire's reads of it keep failing";
      after = [
        "pipewire.service"
        "linux-voice-assistant.service"
      ];
      wantedBy = [ "multi-user.target" ];
      environment = {
        MIC_USB_ID = cfg.microphone.usbId;
        # The capture node it listens to for a silent stall (see the top).
        MIC_NODE = captureNode;
        PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
        PW_DUMP = "${config.services.pipewire.package}/bin/pw-dump";
        PW_RECORD = "${config.services.pipewire.package}/bin/pw-record";
        WPCTL = "${config.services.pipewire.wireplumber.package}/bin/wpctl";
        JOURNALCTL = "${config.systemd.package}/bin/journalctl";
        SYSTEMCTL = "${config.systemd.package}/bin/systemctl";
      };
      serviceConfig = {
        Type = "simple";
        ExecStart = lib.getExe micWatchdog;
        Restart = "always";
        RestartSec = "10s";
      };
    };

    # Music fades down while the assistant listens and answers, on any host
    # with a satellite and a Snapcast client, and replies speak up in a loud
    # room (adaptiveVolume) (pkgs/lva-snapcast-duck).
    systemd.services.lva-snapcast-duck =
      lib.mkIf (cfg.enable && cfg.backend == "lva" && (ducking || roomMic != null))
        {
          description = "Duck Snapcast while Linux Voice Assistant is active, and speak up in a loud room";
          after = [
            "linux-voice-assistant.service"
            "pipewire.service"
          ];
          wants = [ "linux-voice-assistant.service" ];
          partOf = [ "linux-voice-assistant.service" ];
          # Stopped with LVA (partOf) and started with it again.
          wantedBy = [
            "multi-user.target"
            "linux-voice-assistant.service"
          ];
          environment = {
            PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
            PW_DUMP = "${config.services.pipewire.package}/bin/pw-dump";
            WPCTL = "${config.services.pipewire.wireplumber.package}/bin/wpctl";
            DUCK_VOLUME = cfg.lva.snapcastDucking.volume;
            DUCK_LISTEN_VOLUME = cfg.lva.snapcastDucking.listenVolume;
            # Nothing to duck when ducking is off: the unit runs for the voice alone.
            DUCK_BINARIES = lib.optionalString ducking (
              lib.concatStringsSep "," cfg.lva.snapcastDucking.programs
            );
            ADAPT_MIC_NODE = if roomMic == null then "" else roomMic;
            ADAPT_QUIET_DB = toString cfg.lva.adaptiveVolume.quietDb;
            ADAPT_MAX_BOOST_DB = toString cfg.lva.adaptiveVolume.maxBoostDb;
            LVA_PREFS = "/var/lib/linux-voice-assistant/prefs.json";
            PW_RECORD = "${config.services.pipewire.package}/bin/pw-record";
            # Kept across restarts of the unit (not reboots): a ducker stopped
            # with the music down finds the volumes to put back here.
            STATE_FILE = "/run/lva-snapcast-duck/saved.json";
            FADE_DOWN_SECONDS = toString cfg.lva.snapcastDucking.fadeDown;
            FADE_UP_SECONDS = toString cfg.lva.snapcastDucking.fadeUp;
            LVA_PERIPHERAL_URL = "ws://127.0.0.1:${toString cfg.lva.peripheralPort}";
          };
          serviceConfig = {
            Type = "simple";
            User = "linux-voice-assistant";
            Group = "linux-voice-assistant";
            SupplementaryGroups = [ "pipewire" ];
            ExecStart = "${lib.getExe lvaSnapcastDuck}";
            RuntimeDirectory = "lva-snapcast-duck";
            RuntimeDirectoryPreserve = true;
            Restart = "on-failure";
            RestartSec = "5s";
          };
        };
  };
}
