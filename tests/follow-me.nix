# tests/follow-me.nix
#
# Evaluation checks for the follow-me module (modules/server/follow-me.nix):
# its settings and defaults, the mistakes it must reject, and what it generates
# for Home Assistant (lib/follow-me.nix). The generated templates are rendered by
# tests/follow-me-templates.py.
{ lib, pkgs }:

let
  eval =
    followMe: deployment:
    (lib.evalModules {
      modules = [
        {
          options.assertions = lib.mkOption { default = [ ]; };
          options.lanbat.deployment = lib.mkOption {
            type = lib.types.attrsOf lib.types.anything;
            default = { };
          };
          options.services.home-assistant.config = lib.mkOption {
            type = lib.types.attrsOf lib.types.anything;
            default = { };
          };
          config.lanbat.deployment = {
            people = {
              alex.name = "Alex";
              sam.name = "Sam";
            };
            voiceRooms = {
              "Office" = [ "server" ];
              "Bedroom 1" = "pi5";
            };
            techo5Devices.kitchen.room = "Kitchen";
          }
          // deployment;
        }
        ../modules/server/follow-me.nix
        { lanbat.homeAssistant.followMe = followMe; }
      ];
    }).config;

  failures = cfg: lib.filter (a: !a.assertion) cfg.assertions;
  messages = cfg: map (a: a.message) (failures cfg);
  fm = cfg: cfg.lanbat.homeAssistant.followMe;

  off = eval { } { };

  on = eval {
    enable = true;
    rooms = {
      "Office".lights = [ "light.office_lamp" ];
      "Kitchen" = {
        lights = [ "light.kitchen_strip" ];
        illuminance = "sensor.kitchen_lux";
        music = false;
      };
    };
  } { };

  noSensor = eval {
    enable = true;
    rooms."Garage".lights = [ "light.garage" ];
  } { };

  unknownPerson = eval {
    enable = true;
    people = [ "nobody" ];
    rooms."Office".lights = [ "light.office_lamp" ];
  } { };

  # A lock is not a light: the type refuses it, so forcing the value throws.
  notALight = builtins.tryEval (
    builtins.deepSeq (fm (eval {
      enable = true;
      rooms."Office".lights = [ "lock.front_door" ];
    } { })).rooms true
  );

  expect = name: cond: if cond then "" else "FAIL: ${name}\n";
in
pkgs.runCommand "follow-me-check" { } ''
  errors="${
    expect "off by default" ((fm off).enable == false)
    + expect "off adds no failing assertion" (failures off == [ ])
    + expect "dry run is on by default" ((fm off).dryRun == true)
    + expect "people default to the keys of lanbat.deployment.people" (
      (fm off).people == [
        "alex"
        "sam"
      ]
    )
    + expect "defaults match the spec" (
      let
        c = fm off;
      in
      c.arriveSeconds == 10
      && c.lightsOffMinutes == 5
      && c.followSeconds == 20
      && c.followCooldownSeconds == 60
      && c.musicPauseMinutes == 15
      && c.unlocatedMinutes == 20
      && c.darkLux == 30
      && c.sunBelow == -1
      && c.eveningFrom == "17:00"
      && c.nightFrom == "22:30"
      && c.morningTo == "06:30"
      && c.eveningBrightness == 100
      && c.nightBrightness == 20
    )
    + expect "a valid configuration raises no assertion" (failures on == [ ])
    + expect "a room's music setting defaults to on" ((fm on).rooms."Office".music == true)
    + expect "a room's illuminance sensor is optional" ((fm on).rooms."Office".illuminance == null)
    + expect "a Voice room and a TECHO5 room are both rooms a sensor can report" (failures on == [ ])
    + expect "a room with no presence sensor is rejected" (lib.length (failures noSensor) == 1)
    + expect "the message names follow-me and the room" (
      lib.any (m: lib.hasInfix "follow-me" m && lib.hasInfix "Garage" m) (messages noSensor)
    )
    + expect "a person not in lanbat.deployment.people is rejected" (
      lib.length (failures unknownPerson) == 1
    )
    + expect "the message names the person" (
      lib.any (m: lib.hasInfix "nobody" m) (messages unknownPerson)
    )
    + expect "a lights entry that is not a light entity is rejected" (!notALight.success)
  }"
  if [ -n "$errors" ]; then
    printf '%s' "$errors" >&2
    exit 1
  fi
  touch $out
''
