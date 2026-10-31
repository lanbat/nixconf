# modules/pi/audio.nix
#
# One PipeWire for the whole Pi, owning the HDMI output.
#
# Snapcast's client, the voice satellite and the TV sessions all play through
# it. The HDMI device only takes IEC958 frames, so ALSA's own mixing (dmix)
# can't share it; without a sound server, whichever program opened it first
# kept the others out, and a spoken reply failed while music played.
#
# The system-wide instance listens on /run/pipewire/pipewire-0 and, for
# PulseAudio clients, /run/pulse/native. Clients need the pipewire group.
# The Linux Voice Assistant's microphone and echo cancellation are set up for
# any host in modules/core/voice-satellite-audio.nix.
#
# Voice satellite
# ---------------
# Replies play through PipeWire, so they mix with the music. While the
# assistant listens and answers, Snapcast's volume drops, which also keeps
# the music from drowning out the speaker in the microphone. The microphone
# stays a direct ALSA capture: WirePlumber leaves that device alone.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  satellite = config.lanbat.voiceSatellite;
  runtimeDir = "/run/pipewire";

  lvaWakeupChime = pkgs.callPackage ../../pkgs/lva-wakeup-chime { };

  # Sets the volume of Snapcast's client streams in PipeWire. The process
  # binary is a property of snapclient's client, not of its stream nodes.
  # pw-dump and wpctl warn on stderr that a system service gets no realtime
  # scheduling; that would fill the satellite's journal on every reply.
  snapcastVolume =
    name: volume:
    pkgs.writeShellScript "snapcast-${name}" ''
      export PIPEWIRE_RUNTIME_DIR=${runtimeDir}
      ids=$(${config.services.pipewire.package}/bin/pw-dump 2>/dev/null | ${lib.getExe pkgs.jq} -r '
        [.[]
          | select(.type == "PipeWire:Interface:Client"
              and .info.props["application.process.binary"] == "snapclient")
          | .id] as $clients
        | .[]
        | select(.type == "PipeWire:Interface:Node"
            and (.info.props["client.id"] as $client | $clients | index($client)) != null)
        | .id')
      for id in $ids; do
        ${config.services.pipewire.wireplumber.package}/bin/wpctl set-volume "$id" ${volume} 2>/dev/null
      done
    '';
  # Wyoming's ducking; LVA's fades (pkgs/lva-snapcast-duck).
  duck = snapcastVolume "duck" "0.25";
  restore = snapcastVolume "restore" "1.0";

  usbId = lib.splitString ":" satellite.microphone.usbId;
in
{
  services.pulseaudio.enable = false;
  services.pipewire = {
    enable = true;
    systemWide = true;
    alsa.enable = true;
    pulse.enable = true;

    # WirePlumber starts new outputs at 40%; the TV's own volume is the control.
    wireplumber.extraConfig."50-full-output-volume"."wireplumber.settings" = {
      "device.routes.default-sink-volume" = 1.0;
    };

    wireplumber.extraConfig."51-voice-satellite-microphone" =
      lib.mkIf (satellite.enable && satellite.backend == "wyoming")
        {
          "monitor.alsa.rules" = [
            {
              matches = [
                {
                  "device.vendor.id" = "0x${lib.head usbId}";
                  "device.product.id" = "0x${lib.last usbId}";
                }
              ];
              actions.update-props."device.disabled" = true;
            }
          ];
        };
  };

  # For login sessions (the TV sessions).
  environment.sessionVariables = {
    PIPEWIRE_RUNTIME_DIR = runtimeDir;
    PULSE_SERVER = "unix:/run/pulse/native";
  };

  # The Pi shares its audio device with Snapcast, the voice satellite and Kodi.
  # A system-wide PipeWire gets no rtkit, so without these limits module-rt
  # cannot raise its data loop above ordinary scheduling: a missed 5 ms
  # deadline on the USB speaker is an audible click. Let it use real-time
  # priority and lock its buffers.
  systemd.services.pipewire.serviceConfig = {
    LimitRTPRIO = 95;
    LimitMEMLOCK = lib.mkForce "infinity";
    Nice = -10;
  };

  lanbat.voiceSatellite.speaker = "pipewire";
  lanbat.voiceSatellite.awakeSound = lib.mkIf satellite.enable (
    lib.mkDefault (
      if satellite.backend == "lva" then
        "${lvaWakeupChime}/wakeup.flac"
      else
        "${pkgs.callPackage ../../pkgs/voice-satellite-awake-chime { }}/awake.wav"
    )
  );

  services.wyoming.satellite.extraArgs =
    lib.mkIf (satellite.enable && satellite.backend == "wyoming")
      [
        # Home Assistant heard the wake word.
        "--detection-command"
        "${duck}"
        "--tts-played-command"
        "${restore}"
        "--error-command"
        "${restore}"
        "--disconnected-command"
        "${restore}"
        "--startup-command"
        "${restore}"
      ];

  systemd.services.wyoming-satellite = lib.mkIf (satellite.enable && satellite.backend == "wyoming") {
    environment.PIPEWIRE_RUNTIME_DIR = runtimeDir;
    serviceConfig.SupplementaryGroups = [ "pipewire" ];
  };
}
