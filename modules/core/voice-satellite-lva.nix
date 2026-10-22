# Linux Voice Assistant backend (ESPHome protocol, modules/core/voice-satellite.nix).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.lanbat.voiceSatellite;
  lva = cfg.lva;

  lvaPackage = pkgs.callPackage ../../pkgs/linux-voice-assistant { };
  heyNabuWakeWords = pkgs.callPackage ../../pkgs/lva-wakewords-hey-nabu { };

  stateDir = "/var/lib/linux-voice-assistant";

  defaultInterface = config.lanbat.network.lanInterface or null;

  networkInterface = if lva.networkInterface != null then lva.networkInterface else defaultInterface;

  # The PlayStation Eye's capture node as modules/pi/audio.nix names it (one
  # channel, downmixed from its four microphones); LVA takes a Pulse source name.
  psEyeInput = "lanbat_ps_eye_capture";

  aecInput = cfg.echoCancellation.pulseSourceName;
  # LVA hands the output device to mpv, which wants it with its driver: a bare
  # sink name is no device to mpv, and the replies play nowhere.
  aecOutput = "pulse/${cfg.echoCancellation.pulseSinkName}";

  audioInputDevice =
    if lva.audioInputDevice != null then
      lva.audioInputDevice
    else if cfg.echoCancellation.enable && (config.services.pipewire.enable or false) then
      aecInput
    else if cfg.microphone.usbId == "1415:2000" then
      psEyeInput
    else
      null;

  audioOutputDevice =
    if lva.audioOutputDevice != null then
      lva.audioOutputDevice
    else if cfg.echoCancellation.enable && (config.services.pipewire.enable or false) then
      aecOutput
    else
      null;

  wakeWordDir =
    if lva.extraWakeWordDir != null then
      lva.extraWakeWordDir
    else if lib.elem "hey_nabu" lva.wakeModels then
      heyNabuWakeWords
    else
      null;

  # The wake words, the reply volume ($1) and the wake-word probability
  # cutoff ($2), when this host sets them. An empty $1 keeps a volume Home
  # Assistant last set; an absent $2 keeps the model's own cutoff.
  setPrefs = pkgs.writeShellScript "lva-set-prefs" ''
    prefs=${stateDir}/prefs.json
    [ -s "$prefs" ] || echo '{}' > "$prefs"
    ${lib.getExe pkgs.jq} --argjson words ${lib.escapeShellArg (builtins.toJSON lva.wakeModels)} \
      --arg volume "''${1:-}" \
      --arg threshold "''${2:-}" \
      '.active_wake_words = $words
       | if $volume != "" then .volume = ($volume | tonumber) else . end
       | if $threshold != "" then
           .wake_word_1_sensitivity = ($threshold | tonumber)
           | if ($words | length) > 1 then .wake_word_2_sensitivity = ($threshold | tonumber) else . end
         else . end' \
      "$prefs" > "$prefs.new"
    mv "$prefs.new" "$prefs"
  '';

  # Volume stays $1. A cutoff with no volume still passes an empty $1, so
  # the cutoff is always $2 (systemd treats "" as an empty argument).
  prefsArgs =
    if lva.wakeThreshold != null then
      " ${
         if lva.volume != null then toString lva.volume else "\"\""
       } ${builtins.toJSON lva.wakeThreshold}"
    else
      lib.optionalString (lva.volume != null) " ${toString lva.volume}";

  peripheralApiEnabled =
    !lva.disablePeripheralApi || lva.snapcastDucking.enable || lva.peripheralApiUsers != [ ];

  audioUnits = lib.optionals usesPipewire [
    "pipewire.service"
    "pipewire-pulse.service"
    "wireplumber.service"
  ];

  # LVA runs only while its microphone is plugged in: it starts at boot only if
  # the microphone's sound card is there (ExecCondition: no restart loop on a
  # satellite whose microphone isn't there yet, or was unplugged), and udev
  # starts it when the microphone is plugged in later. A BindsTo= on the
  # card's device unit would stop LVA on every deploy, since udev applies a
  # new rule only to devices it processes again.
  micUsbId = lib.splitString ":" cfg.microphone.usbId;
  micPresent = pkgs.writeShellScript "lanbat-voice-microphone-present" ''
    ${pkgs.gnugrep}/bin/grep -qsx '${cfg.microphone.usbId}' /proc/asound/card*/usbid
  '';

  pulseRuntime = "/run/pulse/native";
  pipewireRuntime = "/run/pipewire";
  usesPipewire = config.services.pipewire.enable or false;

  execStartArgs = [
    "--name"
    cfg.name
    "--wake-model"
    (lib.head lva.wakeModels)
    "--stop-model"
    lva.stopWord.model
    "--audio-input-channels"
    (toString lva.audioInputChannels)
    "--mic-volume"
    (toString lva.micVolume)
    "--mic-auto-gain"
    (toString lva.micAutoGain)
    "--mic-noise-suppression"
    (toString lva.micNoiseSuppression)
    "--continue-conversation-delay"
    (toString lva.continueConversationDelay)
    "--port"
    (toString lva.port)
    "--preferences-file"
    "${stateDir}/prefs.json"
    "--download-dir"
    "${stateDir}/dl"
  ]
  ++ lib.optionals lva.listenDuringWakeSound [
    "--listen-during-wake-sound"
  ]
  ++ lib.optionals (networkInterface != null) [
    "--network-interface"
    networkInterface
  ]
  ++ lib.optionals (audioInputDevice != null) [
    "--audio-input-device"
    audioInputDevice
  ]
  ++ lib.optionals (audioOutputDevice != null) [
    "--audio-output-device"
    audioOutputDevice
  ]
  ++ lib.optionals (cfg.awakeSound != null) [
    "--wakeup-sound"
    (toString cfg.awakeSound)
  ]
  ++ lib.optionals (lva.startListeningSound != null) [
    "--start-listening-sound"
    (toString lva.startListeningSound)
  ]
  ++ lib.optionals (wakeWordDir != null) [
    "--wake-word-dir"
    (toString wakeWordDir)
  ]
  ++ (
    if peripheralApiEnabled then
      [
        "--peripheral-host"
        "127.0.0.1"
        "--peripheral-port"
        (toString lva.peripheralPort)
      ]
    else
      [ "--disable-peripheral-api" ]
  );
in
{
  config = lib.mkIf (cfg.enable && cfg.backend == "lva") {
    users.groups.linux-voice-assistant = { };
    users.users.linux-voice-assistant = {
      isSystemUser = true;
      group = "linux-voice-assistant";
      home = stateDir;
      extraGroups = lib.optionals usesPipewire [
        "pipewire"
        "audio"
      ];
    };

    networking.firewall.allowedTCPPorts = lib.mkAfter [ lva.port ];
    networking.firewall.allowedUDPPorts = lib.mkAfter [ 5353 ];

    services.udev.extraRules = ''
      SUBSYSTEM=="sound", KERNEL=="controlC*", ATTRS{idVendor}=="${lib.head micUsbId}", ATTRS{idProduct}=="${lib.last micUsbId}", TAG+="systemd", ENV{SYSTEMD_WANTS}+="linux-voice-assistant.service"
    '';

    systemd.services.linux-voice-assistant = {
      description = "Linux Voice Assistant voice satellite (ESPHome)";
      wantedBy = [ "multi-user.target" ];
      # Finding the default interface (when none is set) runs `which ip`.
      path = [
        pkgs.which
        pkgs.iproute2
      ];
      after = audioUnits ++ [ "sound.target" ];
      # LVA keeps recording from the stream it opened at start: when
      # WirePlumber or PipeWire restarts (a deploy that changes their config),
      # the microphone's node is recreated and LVA goes deaf without an error
      # (2026-10-06, Pi 3). Restart with them.
      partOf = audioUnits;
      environment = {
        HOME = stateDir;
      }
      # Replies are fetched from Home Assistant over HTTPS, signed by the
      # profile's internal CA (services/home-assistant.nix, internal_url).
      // lib.optionalAttrs ((config.lanbat.deployment.secrets.caCertificate or null) != null) {
        LVA_TLS_CA_FILE = "${config.lanbat.deployment.secrets.caCertificate}";
      }
      // lib.optionalAttrs usesPipewire {
        PULSE_SERVER = "unix:${pulseRuntime}";
        PIPEWIRE_RUNTIME_DIR = pipewireRuntime;
      };
      serviceConfig = {
        Type = "simple";
        User = "linux-voice-assistant";
        Group = "linux-voice-assistant";
        StateDirectory = "linux-voice-assistant";
        StateDirectoryMode = "0750";
        # The mixer settings the host gives its speaker (the server's onboard
        # codec starts muted), as the Wyoming satellite applies them; "-" so a
        # control that isn't there doesn't stop the satellite.
        ExecStartPre =
          map (
            args: "-+${pkgs.alsa-utils}/bin/amixer -q ${lib.replaceStrings [ "%" ] [ "%%" ] args}"
          ) cfg.mixer
          # --wake-model only applies while prefs.json names no wake words, and
          # LVA writes there whatever Home Assistant selects; setting them here
          # on every start keeps this host's configuration in charge. The same
          # goes for the volume and the wake-word cutoff, when this host sets them.
          ++ [ "${setPrefs}${prefsArgs}" ];
        ExecCondition = "${micPresent}";
        ExecStart = "${lvaPackage}/bin/linux-voice-assistant ${lib.escapeShellArgs execStartArgs}";
        Restart = "on-failure";
        RestartSec = "5s";
        # soundcard opens ALSA/Pulse devices directly.
        PrivateDevices = lib.mkForce false;
        DeviceAllow = lib.mkForce [
          "char-alsa rw"
          "char-dri rw"
        ];
        SupplementaryGroups = lib.optionals usesPipewire [ "pipewire" ];
      };
    };
  };
}
