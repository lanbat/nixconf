# modules/pi/stackchan.nix
#
# A Stack-chan robot (M5Stack's StackChan kit: a CoreS3 with two servos, a
# camera, head touch and LEDs) as the face of this host's voice satellite,
# plugged in over USB. The Pi keeps the voice (wake word, microphone,
# speaker); the robot shows it: listening, thinking, a mouth that follows the
# reply, captions, timers, and it looks at the people it sees. A tap on its
# head starts a conversation. See docs/stackchan.md.
#
# The robot runs firmware/stackchan; pkgs/lva-stackchan follows LVA's
# peripheral WebSocket on the loopback and drives it. Nothing leaves the host:
# the camera is processed on the robot and no picture is ever sent.
#
# With `wifi.enable` the robot works unplugged too: it joins the network and
# dials the bridge over TLS (port 8771, open to `wifi.allowFrom` only), with
# the network's details and the bridge's certificate fingerprint and token
# handed over USB the first time it is plugged in.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.stackchan;
  satellite = config.lanbat.voiceSatellite;
  bridge = pkgs.callPackage ../../pkgs/lva-stackchan { };
  doaOrder = pkgs.callPackage ../../pkgs/doa-order { };
  flag = b: if b then "1" else "0";
  endpointLib = import ../../lib/endpoints.nix { inherit lib; };

  # The assistant router, when the profile runs it and this satellite has a
  # room: the robot tells it what it sees and gets moods, gestures and body
  # commands back (pkgs/assistant-router body.py).
  # From deploy data, not the endpoint table: this decides `consumes`, which
  # that table is built from.
  hostLib = import ../../lib/host.nix { inherit lib; };
  routerHere =
    cfg.router
    && satellite.room != null
    && hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);
  routerHost = endpointLib.soleHost {
    endpoints = config.lanbat.endpoints;
    name = "assistant-router";
    consumer = "lva-stackchan on ${config.lanbat.hostKey}";
  };
  routerAddress = config.lanbat.endpointHost "assistant-router" routerHost;
  routerUrl = "ws://${routerAddress}:${toString config.lanbat.endpoints.assistant-router.endpoint.port}/v1/body";
  hhmm = types.strMatching "([01][0-9]|2[0-3]):[0-5][0-9]";
  wifi = cfg.wifi;

  # Whose phones the robot looks for: the people's beacons, and the iPhone
  # keys where room-presence has them on this host (the same condition as its
  # secret requirement, modules/pi/room-presence.nix).
  people = config.lanbat.deployment.people or { };
  phonesHere = cfg.phones && routerHere && people != { };
  beacons = lib.concatStringsSep "," (
    lib.mapAttrsToList (key: p: "${key}=${p.bleBeacon}") (
      lib.filterAttrs (_: p: (p.bleBeacon or null) != null) people
    )
  );
  roomPresence = config.lanbat.roomPresence or { };
  irksHere = phonesHere && (roomPresence.enable or false) && (roomPresence.irks or false);
  wifiPort = toString wifi.port;
  wifiRules = map (net: "INPUT -p tcp --dport ${wifiPort} -s ${net} -j ACCEPT") wifi.allowFrom;
in
{
  options.lanbat.stackchan = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = "Drive a Stack-chan robot from the voice satellite (on by default where the plugin is enabled).";
    };
    usbSerial = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "F4:12:FA:00:00:00";
      description = ''
        The robot's USB serial number (`udevadm info /dev/ttyACM0 | grep ID_SERIAL_SHORT`).
        Null matches any ESP32-S3 on its built-in USB port (303a:1001), which is
        enough unless another ESP32-S3 board is plugged in.
      '';
    };
    router = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Connect to the assistant router (services/assistant-router.nix), when
        the profile runs it and the satellite has a room: the assistant then
        knows it has a body and whether someone is in front of it, picks the
        robot's mood and gestures per reply, and obeys "nod", "dance", "go to
        sleep".
      '';
    };
    captions = mkOption {
      type = types.bool;
      default = true;
      description = "Show what was said and the reply on the robot's screen.";
    };
    touchToTalk = mkOption {
      type = types.bool;
      default = true;
      description = "A tap on the robot's head starts a conversation, or stops one.";
    };
    noticeGuests = mkOption {
      type = types.bool;
      default = true;
      description = "Look at, and nod to, a face that appears after nobody was around (camera, processed on the robot).";
    };
    napAfter = mkOption {
      type = types.nullOr types.ints.positive;
      default = 15;
      description = ''
        Minutes without seeing anyone before the robot naps: screen dark, eyes
        closed, LEDs off, head down, the camera slowed to a frame a second. A
        face, a touch or the wake word wakes it. Null keeps it awake (it still
        dozes after 5 minutes).
      '';
    };
    nightHours = {
      start = mkOption {
        type = types.nullOr hhmm;
        default = "23:00";
        description = "When the robot goes to sleep (dimmed and still); null keeps it awake.";
      };
      end = mkOption {
        type = types.nullOr hhmm;
        default = "07:00";
        description = "When it wakes up.";
      };
    };
    brightness = mkOption {
      type = types.ints.between 0 255;
      default = 180;
      description = "Screen brightness by day.";
    };
    autoBrightness = mkOption {
      type = types.bool;
      default = true;
      description = ''
        By day, dim the screen in a dim room and brighten it in a bright one
        (its light sensor, relative to the brightest light it has seen in the
        last half day), between 30 % and `brightness`.
      '';
    };
    nightBrightness = mkOption {
      type = types.ints.between 0 255;
      default = 10;
      description = "Screen brightness while asleep.";
    };
    sadWords = mkOption {
      type = types.nullOr (types.listOf types.str);
      default = null;
      example = [
        "sorry"
        "leider"
      ];
      description = ''
        Words that make a reply sad rather than happy. Null keeps the bridge's
        English list (pkgs/lva-stackchan); set it for another language.
      '';
    };
    soundDirection = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Turn the head toward the voice when the wake word is heard, so the
        camera meets the speaker: direction finding on the satellite's four
        microphones (a PlayStation Eye). A no-op that only retries quietly on
        a host whose microphone is not the Eye; set it to false to stop
        listening. See docs/stackchan.md.
      '';
    };
    soundOffsetDeg = mkOption {
      type = types.str;
      default = "0";
      example = "-15";
      description = ''
        Degrees added to the found angle. The voice as the satellite's mics
        measure it is not always where it is as the robot sees it (the head is
        turned, the mics are not exactly where the geometry says): a
        calibration, docs/stackchan.md.
      '';
    };
    soundTurnMinDeg = mkOption {
      type = types.str;
      default = "15";
      description = ''
        The voice must move this many degrees before the head turns again; the
        estimate wobbles a few degrees between windows, and a turn is not a
        free gesture.
      '';
    };
    soundMicGeometry = mkOption {
      type = types.str;
      default = "-0.03,0;-0.01,0;0.01,0;0.03,0";
      example = "0.03,0;0.01,0;-0.01,0;-0.03,0";
      description = ''
        Where the four microphones sit, as four "x,y" points in metres (y
        forward, x to the robot's right), in the order the card reports them.
        The default is the PlayStation Eye's: a line across its front, 2 cm
        apart. Mics in a line tell left from right but not front from back,
        which is all the head needs. If the head turns away from the voice,
        the camera faces the other way: reverse the order (the example).
        A card with mics in a square gives front and back too; set its points
        here. docs/stackchan.md.
      '';
    };
    phones = mkOption {
      type = types.bool;
      default = true;
      description = ''
        The robot scans Bluetooth for the people's phones too, like the
        room-presence plugin, and tells the assistant router who is near it
        (useful where it stands away from the Pi, on Wi-Fi). Beacons come from
        people.<key>.bleBeacon; phone keys from the router (the phones paired
        once) and from the room-presence-irks secret when room-presence has
        `irks` on this host. Handed to the robot over USB, or over its Wi-Fi
        link to firmware 1.9.7 and later. docs/people.md.
      '';
    };
    wifi = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Let the robot fall back to Wi-Fi when it isn't plugged into this Pi
          (on a charger elsewhere in the room, say). USB wins whenever it is
          there. Needs the stackchan-wifi secret ("ssid=" and "password="
          lines), which the bridge reads as a systemd credential and hands to
          the robot over USB only. See docs/stackchan.md.
        '';
      };
      port = mkOption {
        type = types.port;
        default = 8771;
        description = "The TLS port the robot dials on this Pi.";
      };
      besideMicrophones = mkOption {
        type = types.bool;
        default = false;
        description = ''
          The robot stays beside this Pi's microphones on Wi-Fi too: unplugged
          for its power only (a charger of its own next to the satellite, so
          the Pi's USB doesn't have to feed it). The head then still turns
          toward the voice; otherwise a robot on Wi-Fi is taken to be away from
          these mics, and doesn't. See docs/stackchan.md.
        '';
      };
      address = mkOption {
        type = types.str;
        default = config.lanbat.hosts.${config.lanbat.hostKey}.networking.ip;
        defaultText = lib.literalExpression "config.lanbat.hosts.<this host>.networking.ip";
        description = "Where the robot dials this Pi: an address it can reach from its Wi-Fi.";
      };
      allowFrom = mkOption {
        type = types.listOf types.str;
        default = [ config.lanbat.deployment.lanSubnet ];
        defaultText = lib.literalExpression "[ config.lanbat.deployment.lanSubnet ]";
        example = [ "192.168.20.0/24" ];
        description = ''
          Networks (CIDR) the robot may dial from; the port is closed to
          everything else. Add the Wi-Fi's subnet when it isn't the LAN's.
        '';
      };
    };
    soundSource = mkOption {
      type = types.str;
      default = "lanbat_ps_eye_capture";
      description = ''
        The microphone node to listen on for direction: the satellite capture
        node by default (modules/core/voice-satellite-audio.nix), which keeps
        all four channels for this.
      '';
    };
  };

  config = lib.mkIf (cfg.enable && satellite.enable) {
    assertions = [
      {
        assertion = !wifi.besideMicrophones || wifi.enable;
        message = "lanbat.stackchan.wifi.besideMicrophones is about the robot on Wi-Fi: it needs lanbat.stackchan.wifi.enable.";
      }
      {
        assertion = satellite.backend == "lva";
        message = "lanbat.stackchan follows Linux Voice Assistant's events: set lanbat.voiceSatellite.backend = \"lva\" (docs/stackchan.md), or disable the stackchan plugin.";
      }
    ];

    lanbat.voiceSatellite.lva.peripheralApiUsers = [ "lva-stackchan" ];
    lanbat.services.stackchan = {
      consumes = lib.optional routerHere "assistant-router";
      extraPorts = lib.optional wifi.enable wifi.port;
      # Read by systemd (LoadCredential) for the bridge, which runs as a
      # dynamic user and so cannot own a file.
      secrets.stackchan-wifi = {
        enable = wifi.enable;
        owner = "root";
      };
      # The iPhone keys, for the robot's own scan (LoadCredential below).
      readsSecrets = lib.optional irksHere "room-presence-irks";
    };

    # Only the robot's networks reach the Wi-Fi port; the robot is the only
    # client that gets past its token anyway. Admitted before the nixos-fw
    # chain, which refuses everyone else (as services/snapcast.nix does).
    networking.firewall.extraCommands = lib.mkIf wifi.enable (
      lib.concatMapStringsSep "\n" (r: "iptables -I ${r}") wifiRules
    );
    networking.firewall.extraStopCommands = lib.mkIf wifi.enable (
      lib.concatMapStringsSep "\n" (r: "iptables -D ${r} 2>/dev/null || true") wifiRules
    );

    # The channel-order discovery helper, to find soundMicGeometry (docs/stackchan.md).
    environment.systemPackages = [ doaOrder ];

    services.udev.extraRules = ''
      SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", ${
        lib.optionalString (cfg.usbSerial != null) ''ENV{ID_SERIAL_SHORT}=="${cfg.usbSerial}", ''
      }SYMLINK+="stackchan", GROUP="dialout", MODE="0660"
    '';

    systemd.services.lva-stackchan = {
      description = "Stack-chan: the face of the voice satellite";
      after = [
        "linux-voice-assistant.service"
        "pipewire.service"
      ];
      wants = [ "linux-voice-assistant.service" ];
      partOf = [ "linux-voice-assistant.service" ];
      # Stopped with LVA (partOf) and started with it again. The robot may be
      # plugged in later: the bridge waits for it.
      wantedBy = [
        "multi-user.target"
        "linux-voice-assistant.service"
      ];
      environment = {
        LVA_PERIPHERAL_URL = "ws://127.0.0.1:${toString satellite.lva.peripheralPort}";
        STACKCHAN_DEVICE = "/dev/stackchan";
        PIPEWIRE_RUNTIME_DIR = "/run/pipewire";
        CAPTIONS = flag cfg.captions;
        TOUCH_TO_TALK = flag cfg.touchToTalk;
        NOTICE_GUESTS = flag cfg.noticeGuests;
        NIGHT_START = if cfg.nightHours.start == null then "" else cfg.nightHours.start;
        NIGHT_END = if cfg.nightHours.end == null then "" else cfg.nightHours.end;
        BRIGHTNESS = toString cfg.brightness;
        AUTO_BRIGHTNESS = flag cfg.autoBrightness;
        NIGHT_BRIGHTNESS = toString cfg.nightBrightness;
        NAP_AFTER_S = toString (if cfg.napAfter == null then 0 else cfg.napAfter * 60);
        SOUND_DIRECTION = flag cfg.soundDirection;
        SOUND_OFFSET_DEG = cfg.soundOffsetDeg;
        # The keys of the phones paired once, from the router.
        ROUTER_PHONES = flag (cfg.phones && routerHere);
        SOUND_TURN_MIN_DEG = cfg.soundTurnMinDeg;
        SOUND_MIC_GEOMETRY = cfg.soundMicGeometry;
        SOUND_SOURCE = cfg.soundSource;
      }
      // lib.optionalAttrs routerHere {
        ROUTER_BODY_URL = routerUrl;
        ROOM = satellite.room;
      }
      // lib.optionalAttrs (cfg.sadWords != null) {
        SAD_WORDS = lib.concatStringsSep "," cfg.sadWords;
      }
      // lib.optionalAttrs wifi.enable {
        WIFI_HOST = wifi.address;
        WIFI_PORT = wifiPort;
        SOUND_OVER_WIFI = flag wifi.besideMicrophones;
      }
      // lib.optionalAttrs (phonesHere && beacons != "") {
        BEACONS = beacons;
      };
      serviceConfig = {
        ExecStart = lib.getExe bridge;
        DynamicUser = true;
        # The robot's tty, and the speaker's monitor for the mouth.
        SupplementaryGroups = [
          "dialout"
          "pipewire"
        ];
        Restart = "on-failure";
        RestartSec = "5s";
        # Small next to the satellite, and never in its way on a Pi 3.
        Nice = 5;
        MemoryMax = "96M";
        IPAddressDeny = "any";
        IPAddressAllow = [
          "localhost"
        ]
        ++ lib.optional routerHere routerAddress
        ++ lib.optionals wifi.enable wifi.allowFrom;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        ProtectHome = true;
        ProtectSystem = "strict";
      }
      // lib.optionalAttrs wifi.enable {
        # The Wi-Fi link's certificate, key and token, made on first start.
        StateDirectory = "lva-stackchan";
        StateDirectoryMode = "0700";
      }
      // lib.optionalAttrs (wifi.enable || irksHere) {
        # Root-owned secrets for a dynamic user ($CREDENTIALS_DIRECTORY).
        LoadCredential =
          lib.optional wifi.enable "stackchan-wifi:${config.lanbat.secrets.stackchan-wifi.path}"
          ++ lib.optional irksHere "room-presence-irks:${config.lanbat.secrets.room-presence-irks.path}";
      };
    };
  };
}
