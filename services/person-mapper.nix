# services/person-mapper.nix
#
# The frigate person mapper: turns Frigate's central face recognition into room
# presence. Frigate runs its local face model on the server and announces each
# face it sees over MQTT (frigate/tracked_object_update, frigate/events and the
# per-camera detect status); this service keeps per-room state and, on each
# change, tells the assistant router who is in each room (one face-source socket
# per room on the router's body port) and publishes the room's people to Home
# Assistant (retained entities plus an event). The parsing, state and payload
# logic are pure and unit-tested in pkgs/frigate-person-mapper; this module is
# the wiring.
#
# It names whoever Frigate's face library names: an entry's name is a person
# key, the profile's (lanbat.deployment.people) or one the assistant router
# added at runtime (a login, a guest, someone met by voice); the router drops
# keys it doesn't know. The cameras it watches are settings.cameras, a map of
# room -> Frigate camera name; only a face seen on a room's camera counts for
# that room. A "room" may be a place with no robot, the drive outside: "Where
# is" then says so while the face is there.
#
# Always-on: it is in the voice path (the assistant needs to know who is in the
# room). It runs only when Frigate, the broker and the router are all present and
# at least one camera is configured; otherwise the module contributes its
# description and the account is dropped, so nothing runs and nothing is wired.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;

  name = "person-mapper";
  mapper = pkgs.callPackage ../pkgs/frigate-person-mapper { };

  cfg = config.lanbat.services.${name}.settings;

  # It reads Frigate's faces, says who is where on the router's body socket, and
  # publishes to Home Assistant over the broker: all three must be present, and
  # there must be at least one camera to watch.
  hasMqtt = config.lanbat.hasService "mosquitto";
  hasRouter = config.lanbat.hasService "assistant-router";
  hasFrigate = config.lanbat.hasService "frigate";
  enabled = hasMqtt && hasRouter && hasFrigate && cfg.cameras != { };

  # The cameras it watches, room -> Frigate camera name.
  camerasFile = pkgs.writeText "person-mapper-cameras.json" (builtins.toJSON cfg.cameras);

  personMapperSettings.options.cameras = mkOption {
    type = types.attrsOf types.str;
    default = { };
    description = ''
      The rooms to watch, as room -> Frigate camera name. A face counts for a
      room only when Frigate sees it on that room's camera. The camera names must
      match the names Frigate's configuration gives them (settings.cameras in
      services/frigate.nix).
    '';
  };
in
{
  config = lib.mkMerge [
    { lanbat.settingsSchema.${name} = personMapperSettings; }

    # The description. Present whenever the module is imported, so the broker can
    # wire its user and the firewall can admit this host before it is enabled; the
    # fields it gates on (the account, consumes, readsSecrets, units) follow the
    # same `enabled`, so a profile with no cameras wires nothing.
    {
      lanbat.services.${name} = {
        tier = "always-on";
        consumes = lib.optionals enabled [
          "mosquitto"
          "assistant-router"
        ];
        # The broker's password, declared by mosquitto.nix.
        readsSecrets = lib.optional (enabled && hasMqtt) "mosquitto-person-mapper-pass";
        units = lib.optional enabled "person-mapper";
        account = if enabled then { uid = 996; } else null;
      };
    }

    (lib.mkIf enabled {
      systemd.services.${name} = {
        description = "Frigate face events -> room presence for the assistant and Home Assistant";
        wantedBy = [ "multi-user.target" ];
        after = [
          "network-online.target"
        ]
        ++ lib.optional hasMqtt "mosquitto.service"
        ++ lib.optional hasRouter "assistant-router.service";
        wants = [ "network-online.target" ];
        serviceConfig = {
          User = name;
          Group = name;
          ExecStartPre = [
            # Runs as root (+ prefix) even though the service runs as person-mapper:
            # the broker's password is owned by the mosquitto user, so root copies it
            # into the service's runtime dir and hands it to person-mapper. The
            # process reads it at start via MQTT_PASSWORD_FILE; systemd's
            # EnvironmentFile= would be read before this runs, so the process must
            # read the file itself.
            "+${pkgs.writeShellScript "person-mapper-write-secret" ''
              set -euo pipefail
              install -m 0600 -o person-mapper -g person-mapper \
                ${config.lanbat.secrets.mosquitto-person-mapper-pass.path} \
                /run/person-mapper/mqtt.pass
            ''}"
          ];
          ExecStart = lib.getExe mapper;
          Environment = [
            "MQTT_HOST=127.0.0.1"
            "MQTT_PORT=1883"
            "MQTT_USER=${name}"
            "MQTT_PASSWORD_FILE=/run/person-mapper/mqtt.pass"
            "ROUTER_URL=ws://127.0.0.1:8770/v1/body"
            "CAMERAS_FILE=${camerasFile}"
          ];
          Restart = "always";
          RestartSec = "2s";
          RuntimeDirectory = "person-mapper";
          RuntimeDirectoryMode = "0700";
          # Nothing to read or write but the runtime secret dir and the Nix store.
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateDevices = true;
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
