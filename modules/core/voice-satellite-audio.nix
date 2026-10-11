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

    # Music fades down while the assistant listens and answers, on any host
    # with a satellite and a Snapcast client (pkgs/lva-snapcast-duck).
    systemd.services.lva-snapcast-duck =
      lib.mkIf
        (
          cfg.enable
          && cfg.backend == "lva"
          && cfg.lva.snapcastDucking.enable
          && config.lanbat.snapclient.enable
        )
        {
          description = "Duck Snapcast while Linux Voice Assistant is active";
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
            DUCK_BINARIES = lib.concatStringsSep "," cfg.lva.snapcastDucking.programs;
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
