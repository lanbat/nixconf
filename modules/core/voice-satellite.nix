# modules/core/voice-satellite.nix
#
# Voice satellite for Home Assistant Assist on any host: the server, a Pi 3, 4
# or 5. Two backends:
#
#   wyoming — streams audio to Home Assistant; wake word and STT run on the
#     server (services/wyoming.nix). Supports room replies via voice_reply.
#
#   lva — Linux Voice Assistant (OHF-Voice): ESPHome protocol on port 6053,
#     local wake word and continued conversation after a question. Registered
#     in Home Assistant as an ESPHome device (home-assistant-post-setup).
#
# Microphone and speaker behaviour match the Wyoming path unless noted in
# docs/pi3-satellite.md.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;

  cfg = config.lanbat.voiceSatellite;

  satellitePort =
    if cfg.backend == "lva" then cfg.lva.port else lib.toInt (lib.last (lib.splitString ":" cfg.uri));
in
{
  imports = [
    ./voice-satellite-wyoming.nix
    ./voice-satellite-lva.nix
    ./voice-satellite-diagnostics.nix
    ./voice-satellite-audio.nix
  ];

  options.lanbat.voiceSatellite = {
    enable = lib.mkEnableOption "a voice satellite for Home Assistant Assist";

    backend = mkOption {
      type = types.enum [
        "wyoming"
        "lva"
      ];
      default = "wyoming";
      description = ''
        Which satellite implementation to run. Only one runs per host.

        "wyoming" keeps today's behaviour: wyoming-satellite, Home Assistant
        detects the wake word on the server, port 10700.

        "lva" runs Linux Voice Assistant: local wake word, ESPHome on port
        6053, follow-up listening after a question. Home Assistant still uses
        the same Voice pipeline for speech-to-text, conversation and TTS.
      '';
    };

    name = mkOption {
      type = types.str;
      example = "Kitchen";
      description = "Name of the satellite's device in Home Assistant.";
    };

    uri = mkOption {
      type = types.str;
      example = "tcp://0.0.0.0:10700";
      description = ''
        Address the Wyoming satellite listens on (backend "wyoming" only). Home
        Assistant connects to it.
      '';
    };

    lva = {
      port = mkOption {
        type = types.port;
        default = 6053;
        description = "TCP port Linux Voice Assistant listens on (ESPHome API).";
      };

      networkInterface = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "eth0";
        description = ''
          Interface LVA advertises and binds on (--network-interface). Null
          uses the interface with this host's LAN address (lanbat.network.lanInterface).
        '';
      };

      wakeModels = mkOption {
        type = types.addCheck (types.listOf types.str) (l: l != [ ] && lib.length l <= 2) // {
          description = "list of one or two wake word ids";
        };
        default = [ "okay_nabu" ];
        example = [
          "okay_nabu"
          "hey_jarvis"
        ];
        description = ''
          The wake words LVA listens for: one or two model ids (LVA's limit).
          The first is also --wake-model. They are written into LVA's
          prefs.json on every start, so this setting wins over a choice made
          in Home Assistant's "Wake word" selects, which lasts until the next
          restart.

          The default ``okay_nabu`` is Home Assistant's own wake word, one of
          the microWakeWord models bundled with LVA (``hey_jarvis``,
          ``hey_mycroft``, ``alexa``, ...). Each costs about 7 % of one Pi 3
          core.

          ``hey_nabu`` exists only as an openWakeWord model (the Wyoming
          pipeline's, pkgs/lva-wakewords-hey-nabu). On the Pi 3 it took 109 %
          of a core and ran at 0.9x realtime, falling behind the microphone
          (measured 2026-10-06), so hosts/pi3/hardware.nix warns against it;
          a faster CPU runs it.
        '';
      };

      wakeModel = mkOption {
        type = types.nullOr types.str;
        default = null;
        visible = false;
        description = "Deprecated: one wake word id; sets wakeModels to that one word.";
      };

      extraWakeWordDir = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Extra directory of wake word .tflite and .json manifests
          (--wake-word-dir). When ``wakeModels`` lists ``hey_nabu``, the module
          adds pkgs/lva-wakewords-hey-nabu automatically unless you override
          this.
        '';
      };

      continueConversationDelay = mkOption {
        type = types.float;
        default = 0.65;
        description = ''
          Seconds after TTS finishes before the microphone reopens for a
          follow-up when Home Assistant sets ``continue_conversation``
          (--continue-conversation-delay). Increase slightly if the mic picks
          up the tail of the assistant's reply; decrease for snappier turn-taking.
        '';
      };

      listenDuringWakeSound = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Start streaming speech to Home Assistant while the wake chime still
          plays (--listen-during-wake-sound). Works best with echo cancellation
          enabled so the chime is not transcribed as speech.
        '';
      };

      stopWord = {
        model = mkOption {
          type = types.str;
          default = "stop";
          description = ''
            Stop-word model id bundled with LVA (--stop-model). While TTS or a
            timer is playing, saying this word stops playback (barge-in).
          '';
        };
      };

      adaptiveVolume = {
        enable = mkOption {
          type = types.bool;
          default = true;
          description = ''
            Speak up in a loud room: the satellite takes a one-second level of
            its microphone every 30 s while idle, and a reply is played louder
            the further the room is above quiet (half a dB per dB, at most
            ``maxBoostDb``), never past full scale. In a quiet room replies are
            at Home Assistant's volume for the satellite, so set that for a
            quiet room. Only levels are kept, never audio. Needs a microphone
            node (the PlayStation Eye, or echo cancellation).
          '';
        };

        quietDb = mkOption {
          type = types.int;
          default = -55;
          description = "The microphone level, in dBFS, of a quiet room: no boost at or below it.";
        };

        maxBoostDb = mkOption {
          type = types.int;
          default = 8;
          description = "The most a reply is raised above Home Assistant's volume, in dB (also capped at full scale).";
        };
      };

      snapcastDucking = {
        enable = mkOption {
          type = types.bool;
          default = true;
          description = ''
            Fade Snapcast down while the assistant listens or speaks, through a
            follow-up turn, and back up when the conversation ends (Pi hosts
            with PipeWire and snapclient). Follows LVA's peripheral WebSocket
            (``peripheralPort``); see ``disablePeripheralApi``.
          '';
        };

        volume = mkOption {
          type = types.str;
          default = "0.25";
          example = "0.25";
          description = "Snapcast stream volume while the assistant thinks and answers, as a fraction of the volume before.";
        };

        listenVolume = mkOption {
          type = types.str;
          default = "0.05";
          example = "0.1";
          description = ''
            Snapcast stream volume while the microphone is open (after the wake
            word, and in a follow-up turn), as a fraction of the volume before.
            Near silence on purpose: a speech station at a quarter of its
            volume is still clear speech a metre from the speaker, and ends up
            in the transcript with the command.
          '';
        };

        fadeDown = mkOption {
          type = types.float;
          default = 0.2;
          description = "Seconds the music takes to fade down when the wake word is heard.";
        };

        fadeUp = mkOption {
          type = types.float;
          default = 0.8;
          description = "Seconds the music takes to fade back up when the conversation ends.";
        };

        programs = mkOption {
          type = types.listOf types.str;
          default = [ "snapclient" ];
          example = [
            "snapclient"
            "kodi.bin"
          ];
          description = ''
            Programs whose PipeWire playback streams fade, by their process
            binary name. A TV box adds Kodi (modules/storage/tv-box.nix).
          '';
        };
      };

      peripheralPort = mkOption {
        type = types.port;
        default = 6055;
        description = ''
          Port of LVA's peripheral WebSocket (--peripheral-port), which the
          Snapcast ducking follows. It listens on the loopback only: anyone who
          reaches it can start listening, mute the microphone or stop a reply.
        '';
      };

      volume = mkOption {
        type = types.nullOr (types.numbers.between 0 1);
        default = null;
        example = 0.4;
        description = ''
          Volume of the replies, chime and timers (0–1), set before every start.
          Home Assistant's volume for the satellite changes it until the next
          restart. Replies are mastered near full scale, well above a film's
          dialogue, so a TV box wants it lower. Null keeps whatever volume was
          last set (LVA starts at 1).
        '';
      };

      micVolume = mkOption {
        type = types.ints.between 1 100;
        default = lib.min 100 (lib.max 1 (lib.floor (cfg.microphone.volumeMultiplier * (100.0 / 6.0))));
        defaultText = lib.literalExpression "min 100 (max 1 (floor (microphone.volumeMultiplier * (100.0 / 6.0))))";
        description = "Microphone volume for LVA (--mic-volume, 1–100).";
      };

      micAutoGain = mkOption {
        type = types.ints.between 0 31;
        default = if cfg.microphone.usbId == "1415:2000" then 1 else 0;
        defaultText = lib.literalExpression ''if microphone.usbId == "1415:2000" then 1 else 0'';
        description = "WebRTC auto gain for LVA (--mic-auto-gain).";
      };

      micNoiseSuppression = mkOption {
        type = types.ints.between 0 4;
        default = if cfg.microphone.usbId == "1415:2000" then 2 else 0;
        defaultText = lib.literalExpression ''if microphone.usbId == "1415:2000" then 2 else 0'';
        description = "WebRTC noise suppression for LVA (--mic-noise-suppression, 0–4).";
      };

      audioInputDevice = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "Sony Playstation Eye Analog Surround 4.0";
        description = ''
          Pulse/ALSA input name for LVA (--audio-input-device). Null uses the
          PlayStation Eye name when microphone.usbId is 1415:2000, otherwise
          LVA picks the default device.
        '';
      };

      audioOutputDevice = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "pulse/alsa_output.platform-sound.stereo-fallback";
        description = ''
          Output device for replies (--audio-output-device). Null uses LVA's
          default. On Pis with PipeWire, null is usually enough.
        '';
      };

      audioInputChannels = mkOption {
        type = types.enum [
          1
          2
        ];
        default = 1;
        description = "Mic channels to capture (--audio-input-channels). Use 1 on the Pi 3.";
      };

      startListeningSound = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Sound when manual listen starts (--start-listening-sound). Null keeps LVA's default.";
      };

      disablePeripheralApi = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Disable LVA's peripheral WebSocket (``peripheralPort``, loopback
          only). Set false to attach HAT buttons or LEDs. Snapcast ducking
          and the services in ``peripheralApiUsers`` keep the API enabled
          even when this is true.
        '';
      };

      peripheralApiUsers = mkOption {
        type = types.listOf types.str;
        default = [ ];
        internal = true;
        description = "Units on this host that follow LVA's peripheral WebSocket (the TV box's lva-kodi-companion); any keeps it enabled.";
      };
    };

    echoCancellation = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Route the microphone through PipeWire/PulseAudio WebRTC echo
          cancellation before LVA captures it. Lets the wake and stop words
          work during playback and supports ``listenDuringWakeSound``. Needs
          system-wide PipeWire (``modules/pi/audio.nix``). On a Pi 3, expect
          roughly 10–20% extra CPU; disable here if the device becomes sluggish.
        '';
      };

      pulseSourceName = mkOption {
        type = types.str;
        default = "lanbat_aec_mic";
        example = "lanbat_aec_mic";
        description = ''
          Pulse/ALSA name of the echo-cancelled capture source LVA should use
          as ``lva.audioInputDevice`` when ``echoCancellation.enable`` is true.
        '';
      };

      pulseSinkName = mkOption {
        type = types.str;
        default = "lanbat_aec_playback";
        example = "lanbat_aec_playback";
        description = ''
          PipeWire echo-cancel virtual sink (``sink.props`` node name). When
          ``echoCancellation.enable`` is true, LVA plays replies here so WebRTC
          AEC gets a reference of assistant TTS. Snapcast and other apps keep
          the normal default sink unless you route them too.
        '';
      };

      includeMusic = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Experimental: play Snapcast (and Kodi, on a TV box) into the
          echo-cancel sink as well, so the music and films are part of the
          reference and WebRTC AEC takes them out of the microphone too, not
          only the assistant's own voice. They then play only while the
          microphone is plugged in, since the canceller runs off it. Costs CPU on
          every second of music (measure it on a Pi 3 first). Needs
          ``echoCancellation.enable``.
        '';
      };
    };

    microphone.usbId = mkOption {
      type = types.strMatching "[0-9a-f]{4}:[0-9a-f]{4}";
      default = "1415:2000";
      description = "USB vendor:product ID of the microphone, as lsusb shows it. The default is the PlayStation Eye.";
    };

    microphone.watchdog = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Backend "lva": reopen the microphone when PipeWire's reads of it keep
        failing. A USB microphone that follows another card's clock (the
        PlayStation Eye behind a USB speaker) can fall into a loop of failed
        reads for hours, and the wake word goes unheard; 30 failures within a
        minute turn its card off and on again and restart the voice assistant,
        at most every five minutes. It also catches a microphone that goes
        silent without an error (only zero samples, checked every 30 s):
        three checks reopen the card, three more enumerate the USB device
        again, and then it logs that the microphone needs unplugging
        (pkgs/mic-watchdog).
      '';
    };

    microphone.volumeMultiplier = mkOption {
      type = types.numbers.positive;
      default = if cfg.microphone.usbId == "1415:2000" then 6.0 else 1.0;
      defaultText = lib.literalExpression ''if microphone.usbId == "1415:2000" then 6.0 else 1.0'';
      example = 6.0;
      description = ''
        Gain the Wyoming satellite applies to the microphone audio
        (--mic-volume-multiplier). For backend "lva", this value is mapped to
        lva.micVolume unless you set lva.micVolume yourself. The PlayStation
        Eye's speech is quiet; 6.0 is the default for it.
      '';
    };

    speaker = mkOption {
      type = types.str;
      example = "plughw:CARD=PCH,DEV=0";
      description = "ALSA device that plays Wyoming replies (backend \"wyoming\"). aplay -L lists them.";
    };

    mixer = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "-c PCH sset Master 80% unmute" ];
      description = "amixer arguments applied before the satellite starts (either backend).";
    };

    playMusic = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Whether this host's speaker also plays music: a Snapcast client
        (lanbat.snapclient.enable) makes it a Music Assistant player in the
        satellite's room. False keeps it to spoken replies. The Pi roles run
        the client regardless.
      '';
    };

    haTokenWanted = mkOption {
      type = types.bool;
      default = false;
      internal = true;
      description = ''
        Something else on this host reads the satellite's Home Assistant token
        (ha-voice-token), so it is required whatever the backend: the Echo Shows'
        bridge (services/techo5-bridge.nix).
      '';
    };

    room = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "Living Room";
      description = ''
        Home Assistant area the satellite is in. With backend "wyoming", replies
        can play on the area's Music Assistant players (see alwaysPlayLocally).
        LVA plays TTS on the device; room hand-off via voice_reply is not wired
        for backend "lva".
      '';
    };

    alwaysPlayLocally = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Wyoming only: play Piper audio on this satellite immediately instead of
        handing the reply to voice_reply for Music Assistant room speakers.
      '';
    };

    awakeSound = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/nix/store/…-voice-satellite-awake-chime/awake.wav";
      description = ''
        Short sound when listening starts. Wyoming: --awake-wav (22.05 kHz mono
        WAV). LVA: --wakeup-sound (bundled FLAC by default).
      '';
    };

    homeAssistant = {
      url = mkOption {
        type = types.str;
        example = "https://ha.example.com";
        description = "Home Assistant's address (Wyoming room replies).";
      };

      caFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "CA certificate of Home Assistant's HTTPS address, when a public CA didn't issue it.";
      };
    };
  };

  config = lib.mkMerge [
    {
      lanbat.settingsSchema.voice-satellite = {
        options = {
          backend = mkOption {
            type = types.enum [
              "wyoming"
              "lva"
            ];
            default = "wyoming";
            description = "Satellite backend on this host; copied from lanbat.voiceSatellite.backend for Home Assistant registration.";
          };
          displayName = mkOption {
            type = types.str;
            default = "Voice satellite";
            description = "Device name Home Assistant shows; copied from lanbat.voiceSatellite.name.";
          };
        };
      };
    }
    (lib.mkIf (cfg.lva.wakeModel != null) {
      lanbat.voiceSatellite.lva.wakeModels = [ cfg.lva.wakeModel ];
      warnings = [
        "lanbat.voiceSatellite.lva.wakeModel is deprecated; set lanbat.voiceSatellite.lva.wakeModels = [ \"${cfg.lva.wakeModel}\" ] instead."
      ];
    })
    (lib.mkIf cfg.enable {
      lanbat.services.voice-satellite.secrets.ha-voice-token = {
        enable = (cfg.room != null && cfg.backend == "wyoming") || cfg.haTokenWanted;
        owner = "root";
      };

      lanbat.services.voice-satellite.settings = {
        backend = lib.mkDefault cfg.backend;
        displayName = lib.mkDefault cfg.name;
      };

      lanbat.services.voice-satellite.endpoint = {
        scheme = "tcp";
        port = satellitePort;
      };
    })
  ];
}
