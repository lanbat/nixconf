# services/voice-id.nix
#
# Speaker identification for the voice assistant, in Home Assistant's
# pipeline as its speech-to-text provider: a Wyoming proxy in front of
# faster-whisper (services/wyoming.nix). Every utterance goes through it; it
# streams the audio to faster-whisper as it arrives and returns the transcript
# unchanged, so it works for every satellite and adds nothing to the Pis.
#
# Beside that (pkgs/voice-id), as soon as an utterance's audio ends it works
# out who it sounds like (a CAM++ speaker embedding, in a thread, in parallel
# with speech-to-text) and tells the assistant router, which weighs it with
# who else is in the room (docs/people.md). The router calls back on apiPort
# to enrol a voice ("remember me"), refine it from everyday use and forget it.
#
# Voiceprints (embeddings only, never audio) live on the encrypted workload
# layer, in /mnt/workload/voice-id. voice-id itself stays always-on, since it
# is the speech-to-text path: while the layer is locked it knows nobody and
# keeps nothing, and it restarts when the layer comes online. They are left
# out of backups on purpose: re-enrolling takes a minute.
#
# If it is down, the pipeline has no speech-to-text: it restarts on failure,
# and the faster-whisper entry stays in Home Assistant for a manual switch
# back (docs/failure-modes.md).
#
# Listens on the loopback only.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  port = 10303;
  apiPort = 10304;
  storeDir = "/mnt/workload/voice-id";
  upstream = config.services.wyoming.faster-whisper.servers.main.uri;
  package = pkgs.callPackage ../pkgs/voice-id { };
  hostLib = import ../lib/host.nix { inherit lib; };
  routerHere =
    hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null)
    && config.lanbat.hasService "assistant-router";
  routerPort = config.lanbat.services.assistant-router.port;
in
{
  lanbat.services.voice-id = {
    extraPorts = [
      port
      apiPort
    ];
    account.uid = 959;
    workloadDirs."voice-id" = {
      user = "voice-id";
      group = "voice-id";
      mode = "0700";
    };
  };

  systemd.services.voice-id = {
    description = "Speaker identification in front of speech-to-text (Wyoming proxy)";
    wantedBy = [ "multi-user.target" ];
    after = [ "wyoming-faster-whisper-main.service" ];
    wants = [ "wyoming-faster-whisper-main.service" ];
    serviceConfig = {
      ExecStart = lib.concatStringsSep " " (
        [
          (lib.getExe package)
          "--uri tcp://127.0.0.1:${toString port}"
          "--upstream ${upstream}"
          "--model ${package.model}"
          "--store-dir ${storeDir}"
          "--api-port ${toString apiPort}"
        ]
        ++ lib.optionals routerHere [
          "--router-url http://127.0.0.1:${toString routerPort}/v1/voice"
        ]
      );
      User = "voice-id";
      Group = "voice-id";
      Restart = "always";
      RestartSec = "2s";
      # The sockets, and the voiceprints once the workload layer is open ("-":
      # absent while it is locked, which is no reason not to start).
      ProtectSystem = "strict";
      ReadWritePaths = [ "-${storeDir}" ];
      ProtectHome = true;
      PrivateDevices = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      IPAddressDeny = "any";
      IPAddressAllow = "localhost";
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
      ];
    };
  };

  # The voiceprints' directory appears when the workload layer is unlocked; a
  # restart lets voice-id see it (ReadWritePaths is fixed at start).
  systemd.services.voice-id-unlocked = {
    description = "Let voice-id see its voiceprints once the workload layer is online";
    wantedBy = [ "workload-online.target" ];
    after = [ "workload-init.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${config.systemd.package}/bin/systemctl try-restart voice-id.service";
    };
  };
}
