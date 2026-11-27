# modules/server/lighting.nix
#
# Lighting routines: lights that come on when it gets dark and off in the early
# morning, room lights that follow a presence sensor, and hallway lights that follow someone from the entrance into the
# hall and switch off when everyone is gone. Enabled through the
# lanbat-lighting plugin; configures nothing unless
# lanbat.homeAssistant.lighting.enable is set.
#
# It acts through Home Assistant automations, template sensors and helpers
# generated from these settings (lib/lighting.nix). It switches only the lights
# listed per routine.
#
# Motion and presence are hints, never authority: unknown or unavailable
# sensors are not "absent", and with dryRun (the default) every decision is only
# written to the logbook.
{ config, lib, ... }:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.homeAssistant.lighting;

  time = types.strMatching "([01][0-9]|2[0-3]):[0-5][0-9]";
  seconds = types.ints.positive;
  lights = types.listOf (types.strMatching "(light|switch)\\.[a-z0-9_]+");
  sensors = types.listOf (types.strMatching "binary_sensor\\.[a-z0-9_]+");

  lightingLib = import ../../lib/lighting.nix { inherit lib; };
  generated = lightingLib.mkLighting { inherit cfg; };
in
{
  options.lanbat.homeAssistant.lighting = {
    enable = mkOption {
      type = types.bool;
      default = false;
      description = "Lighting routines: dusk lights and hallway approach lights.";
    };

    dryRun = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Only write what lighting would do to Home Assistant's logbook ("Lighting
        would ..."); switch nothing. Turn it off once the log looks right.
      '';
    };

    sunBelow = mkOption {
      type = types.number;
      default = -1;
      description = "It is dark outside when the sun's elevation is below this many degrees.";
    };

    duskRoutines = mkOption {
      default = { };
      description = ''
        Lights that come on when it gets dark and off in the early morning, by
        key. The key becomes part of the entity and automation ids.
      '';
      type = types.attrsOf (
        types.submodule {
          options = {
            lights = mkOption {
              type = lights;
              default = [ ];
              example = [ "light.livingroom_fairylights" ];
              description = "The lights to switch, as entity ids.";
            };
            offAt = mkOption {
              type = time;
              example = "04:00";
              description = "Turn the lights off at this time (HH:MM), while it is still dark.";
            };
          };
        }
      );
    };

    rooms = mkOption {
      default = { };
      description = ''
        Rooms whose lights follow their presence or motion sensors, by key. The
        lights come on when a sensor reports someone and it is dark outside, and
        go off once every sensor has reported nobody for `offAfterSeconds`. The
        key becomes part of the entity and automation ids.
      '';
      type = types.attrsOf (
        types.submodule {
          options = {
            sensors = mkOption {
              type = sensors;
              example = [ "binary_sensor.kitchen_presence_occupancy" ];
              description = "The room's presence or motion sensors. Any of them on means someone is there.";
            };
            lights = mkOption {
              type = lights;
              example = [ "light.kitchen_ceiling" ];
              description = "The lights to switch, as entity ids. Only these are ever switched.";
            };
            offAfterSeconds = mkOption {
              type = seconds;
              default = 300;
              description = "How long every sensor must report nobody before the lights go off.";
            };
          };
        }
      );
    };

    approach = mkOption {
      default = { };
      description = ''
        Hallway lights that follow someone from the entrance into the hall, by
        key. The key becomes part of the entity and automation ids.
      '';
      type = types.attrsOf (
        types.submodule {
          options = {
            approach = mkOption {
              type = sensors;
              default = [ ];
              example = [ "binary_sensor.c1_person_occupancy" ];
              description = "The sensors that report someone approaching the entrance.";
            };
            presence = mkOption {
              type = sensors;
              default = [ ];
              example = [ "binary_sensor.hall_occupancy" ];
              description = "The sensors that report presence in the hall.";
            };
            distance = mkOption {
              type = types.listOf (types.strMatching "sensor\\.[a-z0-9_]+");
              default = [ ];
              example = [ "sensor.alex_phone_distance" ];
              description = ''
                Sensors that report a person's distance from the house in
                metres (Home Assistant's proximity integration, or a scanner's
                range). Someone within `nearMeters`, and not already inside,
                counts as approaching.
              '';
            };
            trackers = mkOption {
              type = types.listOf (types.strMatching "device_tracker\\.[a-z0-9_]+");
              default = [ ];
              example = [ "device_tracker.alex_phone" ];
              description = ''
                Phones (Companion app `device_tracker` entities) whose GPS
                distance to `zone.home` is compared with `nearMeters`. GPS is
                only accurate to a few metres outdoors, so keep `nearMeters`
                generous.
              '';
            };
            nearMeters = mkOption {
              type = types.number;
              default = 10;
              description = "Within this many metres of the house counts as approaching.";
            };
            inside = mkOption {
              type = types.listOf (types.strMatching "sensor\\.[a-z0-9_]+");
              default = [ ];
              example = [ "sensor.lanbat_people_where_alex" ];
              description = ''
                "Where is <person>" sensors. A person whose sensor shows a room
                or "home" is inside the house and does not count as approaching,
                so being indoors never lights the porch.
              '';
            };
            right = mkOption {
              type = lights;
              default = [ ];
              example = [ "light.hall_right" ];
              description = "The right (porch) light(s) or switch(es), turned on when someone approaches.";
            };
            left = mkOption {
              type = lights;
              default = [ ];
              example = [ "light.hall_left" ];
              description = "The left hallway light(s), turned on when someone is in the hall.";
            };
            offAfterSeconds = mkOption {
              type = seconds;
              default = 180;
              description = "How long both the entrance and the hall must be empty before the lights go off.";
            };
          };
        }
      );
    };
  };

  config = lib.mkIf cfg.enable {
    services.home-assistant.config = {
      sun = { };
      inherit (generated)
        input_boolean
        template
        automation
        ;
    };

    assertions = [
      {
        # Every decision is written with logbook.log; without the integration
        # set up, that service does not exist and each rule aborts at its first
        # action. services/home-assistant.nix sets it up.
        assertion = config.services.home-assistant.config ? logbook;
        message = "lighting: Home Assistant's logbook integration must be set up; every decision is written with logbook.log";
      }
    ];
  };
}
