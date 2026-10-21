# modules/server/follow-me.nix
#
# Follow-me: music that follows a person from room to room, lights and music
# that switch off in an empty room, and lights that come on in a dark room in
# the evening. Enabled through the lanbat-follow-me plugin; configures nothing
# unless lanbat.homeAssistant.followMe.enable is set.
#
# It reads the "Where is <person>" sensors the assistant router publishes
# (docs/people.md) and acts through Home Assistant automations, template
# sensors and helpers generated from these settings (lib/follow-me.nix). It
# switches only the lights listed per room and only Music Assistant players.
#
# Presence is a hint, never authority: unknown or unavailable presence is not
# "empty", and with dryRun (the default) every decision is only written to the
# logbook. See docs/superpowers/specs/2026-10-21-follow-me-design.md.
{ config, lib, ... }:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.homeAssistant.followMe;
  deployment = config.lanbat.deployment;

  # The rooms a "Where is" sensor can name: a voice room's area, or a TECHO5
  # screen's room.
  presenceRooms = lib.unique (
    lib.attrNames (deployment.voiceRooms or { })
    ++ lib.mapAttrsToList (_: d: d.room) (deployment.techo5Devices or { })
  );

  time = types.strMatching "([01][0-9]|2[0-3]):[0-5][0-9]";
  seconds = types.ints.positive;

  followMeLib = import ../../lib/follow-me.nix { inherit lib; };
  generated = followMeLib.mkFollowMe {
    inherit cfg;
    people = map (key: {
      inherit key;
      slug = followMeLib.slug key;
    }) cfg.people;
  };
in
{
  options.lanbat.homeAssistant.followMe = {
    enable = mkOption {
      type = types.bool;
      default = false;
      description = "Act on where people are: music, lights, empty rooms.";
    };

    dryRun = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Only write what follow-me would do to Home Assistant's logbook ("Follow-me
        would ..."); switch nothing. Turn it off once the log looks right.
      '';
    };

    people = mkOption {
      type = types.listOf types.str;
      default = lib.attrNames (deployment.people or { });
      defaultText = lib.literalExpression "builtins.attrNames config.lanbat.deployment.people";
      description = ''
        The keys (lanbat.deployment.people) of the people whose rooms follow-me
        reads. Someone not listed, a guest, neither moves music nor keeps a room
        occupied.
      '';
    };

    rooms = mkOption {
      default = { };
      description = ''
        Rooms follow-me acts in, by Home Assistant area name. A room must be one a
        presence sensor can report: a key of lanbat.deployment.voiceRooms or the
        room of a techo5Devices entry.
      '';
      type = types.attrsOf (
        types.submodule {
          options = {
            lights = mkOption {
              type = types.listOf (types.strMatching "light\\.[a-z0-9_]+");
              default = [ ];
              example = [ "light.living_room_shelf_spot" ];
              description = ''
                The room's lights, as entity ids. The only entities follow-me ever
                switches on or off. Leave out the screens and LEDs of a Stack-chan
                or an Echo Show.
              '';
            };
            illuminance = mkOption {
              type = types.nullOr (types.strMatching "sensor\\.[a-z0-9_]+");
              default = null;
              example = "sensor.kitchen_illuminance";
              description = ''
                The room's light-level sensor, in lux. Without one, the room is dark
                when the sun is below `sunBelow`.
              '';
            };
            music = mkOption {
              type = types.bool;
              default = true;
              description = "Let music follow people into and out of this room, and pause it when empty.";
            };
          };
        }
      );
    };

    arriveSeconds = mkOption {
      type = seconds;
      default = 10;
      description = "How long someone must stay in a room before its lights come on.";
    };
    lightsOffMinutes = mkOption {
      type = seconds;
      default = 5;
      description = "How long a room must be empty before the lights follow-me turned on go off.";
    };
    followSeconds = mkOption {
      type = seconds;
      default = 20;
      description = "How long someone must stay in a new room before the music follows.";
    };
    followCooldownSeconds = mkOption {
      type = seconds;
      default = 60;
      description = "After the music follows someone, the least time before it follows them again.";
    };
    musicPauseMinutes = mkOption {
      type = seconds;
      default = 15;
      description = "How long a room must be empty before its music is paused.";
    };
    unlocatedMinutes = mkOption {
      type = seconds;
      default = 20;
      description = ''
        A person the house still hears but cannot place ("home") keeps the last room
        they were in occupied for this long. Phones drop out of Bluetooth for minutes
        at a time; a silent room is not an empty one.
      '';
    };

    darkLux = mkOption {
      type = types.ints.positive;
      default = 30;
      description = "A room with a light-level sensor is dark below this many lux.";
    };
    sunBelow = mkOption {
      type = types.number;
      default = -1;
      description = "A room with no light-level sensor is dark when the sun's elevation is below this many degrees.";
    };
    eveningFrom = mkOption {
      type = time;
      default = "17:00";
      description = "The evening starts at this time (HH:MM).";
    };
    nightFrom = mkOption {
      type = time;
      default = "22:30";
      description = "The night starts at this time (HH:MM), and lasts until `morningTo`.";
    };
    morningTo = mkOption {
      type = time;
      default = "06:30";
      description = "The night ends at this time (HH:MM). Lights do not come on by day.";
    };
    eveningBrightness = mkOption {
      type = types.ints.between 1 100;
      default = 100;
      description = "Brightness, in percent, of the lights that come on in the evening.";
    };
    nightBrightness = mkOption {
      type = types.ints.between 1 100;
      default = 20;
      description = "Brightness, in percent, of the lights that come on at night (also warmer).";
    };
  };

  config = lib.mkIf cfg.enable {
    # Home Assistant's own configuration: the helpers the rules keep their state
    # in, the sensors they read, and the rules. `sun` is not set up elsewhere.
    services.home-assistant.config = {
      sun = { };
      inherit (generated)
        input_boolean
        input_text
        template
        automation
        ;
    };

    assertions = [
      {
        assertion = cfg.morningTo <= cfg.eveningFrom && cfg.eveningFrom <= cfg.nightFrom;
        message = "follow-me: morningTo (${cfg.morningTo}) must come before eveningFrom (${cfg.eveningFrom}), which must come before nightFrom (${cfg.nightFrom})";
      }
    ]
    ++ lib.mapAttrsToList (room: _: {
      assertion = lib.elem room presenceRooms;
      message = "follow-me: room \"${room}\" has no presence sensor; rooms are the keys of lanbat.deployment.voiceRooms and the rooms of techo5Devices (${lib.concatStringsSep ", " presenceRooms})";
    }) cfg.rooms
    ++ map (person: {
      assertion = deployment.people ? ${person};
      message = "follow-me: person \"${person}\" is not in lanbat.deployment.people";
    }) cfg.people;
  };
}
