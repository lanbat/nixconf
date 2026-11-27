# tests/lighting.nix
#
# Evaluation checks for the lighting module (modules/server/lighting.nix): its
# settings and defaults, what it must reject, and what it generates for Home
# Assistant (lib/lighting.nix). The generated YAML is also checked with Home
# Assistant's own configuration check.
{ lib, pkgs }:

let
  ltLib = import ../lib/lighting.nix { inherit lib; };

  eval =
    lighting: withLogbook:
    (lib.evalModules {
      modules = [
        {
          options.assertions = lib.mkOption { default = [ ]; };
          options.services.home-assistant.config = lib.mkOption {
            type = lib.types.attrsOf lib.types.anything;
            default = { };
          };
          config.services.home-assistant.config = lib.mkIf withLogbook { logbook = { }; };
        }
        ../modules/server/lighting.nix
        { lanbat.homeAssistant.lighting = lighting; }
      ];
    }).config;

  failures = cfg: lib.filter (a: !a.assertion) cfg.assertions;
  messages = cfg: map (a: a.message) (failures cfg);
  lt = cfg: cfg.lanbat.homeAssistant.lighting;

  off = eval { } false;

  on = eval {
    enable = true;
    duskRoutines.fairy = {
      lights = [ "light.livingroom_fairylights" ];
      offAt = "04:00";
    };
    rooms.kitchen = {
      sensors = [ "binary_sensor.kitchen_presence_occupancy" ];
      lights = [ "switch.kitchen_light" ];
    };
    approach.hall = {
      approach = [ "binary_sensor.c1_person_occupancy" ];
      presence = [ "binary_sensor.hall_occupancy" ];
      right = [ "light.hall_right" ];
      left = [ "light.hall_left" ];
      trackers = [ "device_tracker.alex_phone" ];
      inside = [ "sensor.lanbat_people_where_alex" ];
      offAfterSeconds = 180;
    };
  } true;

  noLogbook = eval {
    enable = true;
    duskRoutines.fairy = {
      lights = [ "light.fairy" ];
      offAt = "04:00";
    };
  } false;

  # A fan is not a light; forcing the value throws.
  notALight = builtins.tryEval (
    builtins.deepSeq
      (lt (
        eval {
          enable = true;
          duskRoutines.fairy = {
            lights = [ "fan.plug" ];
            offAt = "04:00";
          };
        } true
      )).duskRoutines
      true
  );

  # A light is not an approach or presence sensor; forcing the value throws.
  notASensor = builtins.tryEval (
    builtins.deepSeq
      (lt (
        eval {
          enable = true;
          approach.hall.approach = [ "light.hall" ];
        } true
      )).approach
      true
  );

  genCfg = lt on;
  gen = ltLib.mkLighting { cfg = genCfg; };
  genLive = ltLib.mkLighting {
    cfg = genCfg // {
      dryRun = false;
    };
  };

  json = builtins.toJSON;
  automation = id: lib.findFirst (a: (a.id or "") == id) null gen.automation;
  automationLive = id: lib.findFirst (a: (a.id or "") == id) null genLive.automation;

  roomOn = automation "lighting_room_kitchen_on";
  roomOnLive = automationLive "lighting_room_kitchen_on";
  roomOff = automation "lighting_room_kitchen_off";
  roomOffLive = automationLive "lighting_room_kitchen_off";
  duskOn = automation "lighting_dusk_fairy_on";
  duskOnLive = automationLive "lighting_dusk_fairy_on";
  duskOff = automation "lighting_dusk_fairy_off";
  duskOffLive = automationLive "lighting_dusk_fairy_off";
  nearOn = automation "lighting_approach_hall_near_on";
  nearOnLive = automationLive "lighting_approach_hall_near_on";
  rightOn = automation "lighting_approach_hall_right_on";
  rightOnLive = automationLive "lighting_approach_hall_right_on";
  leftOn = automation "lighting_approach_hall_left_on";
  leftOnLive = automationLive "lighting_approach_hall_left_on";
  offRule = automation "lighting_approach_hall_off";
  offRuleLive = automationLive "lighting_approach_hall_off";

  templateSensors = lib.concatMap (b: b.binary_sensor or [ ]) gen.template;
  templateNames = map (e: e.name) templateSensors;

  actDry = ltLib.act {
    cfg = {
      dryRun = true;
    };
    message = "turn on the lights";
    steps = [ { action = "homeassistant.turn_on"; } ];
  };
  actLive = ltLib.act {
    cfg = {
      dryRun = false;
    };
    message = "turn on the lights";
    steps = [ { action = "homeassistant.turn_on"; } ];
  };

  haPython = pkgs.home-assistant.python3Packages.python.withPackages (ps: [
    ps.homeassistant
    ps.colorlog # `hass --script check_config` imports it
  ]);

  # Home Assistant's own configuration check on what the generator makes, dry
  # and live. check_config exits 0 even for a broken automation, so its output
  # is what is read.
  haConfig =
    g:
    (pkgs.formats.yaml { }).generate "lighting.yaml" {
      sun = { };
      inherit (g)
        input_boolean
        template
        automation
        ;
    };
  configCheck = pkgs.runCommand "lighting-config-check" { } ''
    export HOME=$TMPDIR
    for yaml in ${haConfig gen} ${haConfig genLive}; do
      d=$TMPDIR/$(basename $yaml)
      mkdir -p $d
      cp --no-preserve=mode $yaml $d/configuration.yaml
      touch $d/secrets.yaml
      ${haPython}/bin/python -m homeassistant --script check_config -c $d > $d.out 2>&1 || true
      cat $d.out
      if grep -E 'ERROR:homeassistant.components|Incorrect config|Invalid' $d.out; then
        echo "check_config rejected $yaml" >&2
        exit 1
      fi
    done
    touch $out
  '';

  expect = name: cond: if cond then "" else "FAIL: ${name}\n";
in
pkgs.runCommand "lighting-check" { inherit configCheck; } ''
  errors="${
    expect "off by default" ((lt off).enable == false)
    + expect "off adds no failing assertion" (failures off == [ ])
    + expect "dry run is on by default" ((lt off).dryRun == true)
    + expect "sun below defaults to -1" ((lt off).sunBelow == -1)
    + expect "a valid configuration raises no assertion" (failures on == [ ])
    + expect "offAt and offAfterSeconds defaults" (
      (lt on).duskRoutines.fairy.offAt == "04:00" && (lt on).approach.hall.offAfterSeconds == 180
    )
    + expect "lighting without the logbook integration is rejected" (
      lib.length (failures noLogbook) == 1 && lib.any (m: lib.hasInfix "logbook" m) (messages noLogbook)
    )
    + expect "a lights entry that is not a light entity is rejected" (!notALight.success)
    + expect "an approach entry that is not a sensor entity is rejected" (!notASensor.success)
    + expect "the generator makes the pause switch" (gen.input_boolean ? lighting_paused)
    + expect "the generator makes the dark sensor, and the active and near helpers" (
      lib.length templateSensors == 3
      && lib.elem "lighting dark" templateNames
      && lib.elem "lighting hall active" templateNames
      && lib.elem "lighting hall near" templateNames
    )
    + expect "every generated sensor has a unique id" (lib.all (e: e ? unique_id) templateSensors)
    + expect "act in a dry run is one log step that says it would" (
      lib.length actDry == 1
      && (lib.head actDry).action == "logbook.log"
      && lib.hasPrefix "would " (lib.head actDry).data.message
    )
    + expect "act live is the log step, then the steps" (
      lib.length actLive == 2
      && (lib.head actLive).action == "logbook.log"
      && !(lib.hasPrefix "would " (lib.head actLive).data.message)
      && (lib.elemAt actLive 1).action == "homeassistant.turn_on"
    )
    + expect "a disabled module adds nothing to Home Assistant" (
      off.services.home-assistant.config == { }
    )
    + expect "the module hands the helpers, sensors and the sun to Home Assistant" (
      let
        c = on.services.home-assistant.config;
      in
      c ? template && c ? input_boolean && c ? sun && c ? automation
    )
    + expect "dusk on: triggered by dark and on restart" (
      let
        t = duskOn.triggers;
      in
      lib.any (x: x.trigger == "homeassistant" && x.event == "start") t
      && lib.any (
        x:
        x.trigger == "state"
        && x.entity_id == "binary_sensor.lighting_dark"
        && x.from == "off"
        && x.to == "on"
      ) t
    )
    + expect "dusk on: not while paused, only when dark and every light off" (
      let
        c = json duskOn.conditions;
      in
      lib.hasInfix "input_boolean.lighting_paused" c
      && lib.hasInfix "binary_sensor.lighting_dark" c
      && lib.hasInfix "light.livingroom_fairylights" c
    )
    + expect "dusk on: live run turns on only the listed lights" (
      lib.length duskOnLive.actions == 2
      && (lib.elemAt duskOnLive.actions 1).action == "homeassistant.turn_on"
      && (lib.elemAt duskOnLive.actions 1).target.entity_id == [ "light.livingroom_fairylights" ]
    )
    + expect "dusk on: dry run only logs" (
      lib.length duskOn.actions == 1 && lib.hasPrefix "would " (lib.head duskOn.actions).data.message
    )
    + expect "dusk off: time trigger at offAt" (
      (lib.head duskOff.triggers).trigger == "time" && (lib.head duskOff.triggers).at == "04:00:00"
    )
    + expect "dusk off: turns off the listed lights" (
      lib.hasInfix "homeassistant.turn_off" (json duskOffLive.actions)
      && lib.hasInfix "light.livingroom_fairylights" (json duskOffLive.actions)
    )
    + expect "approach right on: triggered by approach, gated on dark and off lights" (
      let
        t = lib.head rightOn.triggers;
        c = json rightOn.conditions;
      in
      t.trigger == "state"
      && t.entity_id == [ "binary_sensor.c1_person_occupancy" ]
      && t.to == "on"
      && lib.hasInfix "binary_sensor.lighting_dark" c
      && lib.hasInfix "light.hall_right" c
    )
    + expect "approach right on: turns on the right light" (
      (lib.elemAt rightOnLive.actions 1).target.entity_id == [ "light.hall_right" ]
    )
    + expect "approach left on: triggered by presence, turns on the left light" (
      let
        t = lib.head leftOn.triggers;
      in
      t.entity_id == [ "binary_sensor.hall_occupancy" ]
      && (lib.elemAt leftOnLive.actions 1).target.entity_id == [ "light.hall_left" ]
    )
    + expect "approach off: triggered by active going off for offAfterSeconds" (
      let
        t = lib.head offRule.triggers;
      in
      t.entity_id == "binary_sensor.lighting_approach_hall_active"
      && t.from == "on"
      && t.to == "off"
      && t.for.seconds == 180
    )
    + expect "approach off: turns off both lights" (
      lib.hasInfix "light.hall_right" (json offRuleLive.actions)
      && lib.hasInfix "light.hall_left" (json offRuleLive.actions)
    )
    + expect "approach off: dry run only logs" (
      lib.length offRule.actions == 1 && lib.hasPrefix "would " (lib.head offRule.actions).data.message
    )
    + expect "a key with a space and a digit makes a clean slug" (
      ltLib.slug "Front Door" == "front_door" && ltLib.slug "Hall 2!" == "hall_2"
    )
    + expect "a routine with no approach sensor makes no right-on or off rule" (
      let
        g = ltLib.mkLighting {
          cfg = genCfg // {
            approach.hall = (genCfg.approach.hall) // {
              approach = [ ];
              right = [ ];
              presence = [ ];
              left = [ ];
            };
          };
        };
        ids = map (a: a.id or "") g.automation;
      in
      !(lib.elem "lighting_approach_hall_right_on" ids) && !(lib.elem "lighting_approach_hall_off" ids)
    )
    + expect "near on: triggered by the near sensor, gated on dark and all lights off" (
      let
        t = lib.head nearOn.triggers;
        c = json nearOn.conditions;
      in
      t.entity_id == "binary_sensor.lighting_approach_hall_near"
      && t.to == "on"
      && lib.hasInfix "binary_sensor.lighting_dark" c
      && lib.hasInfix "light.hall_left" c
    )
    + expect "near on: turns on the right and the left light" (
      (lib.elemAt nearOnLive.actions 1).target.entity_id == [
        "light.hall_right"
        "light.hall_left"
      ]
    )
    + expect "the near sensor reads the distance, 10 m, and is off when inside" (
      let
        n = lib.findFirst (e: e.name == "lighting hall near") null templateSensors;
      in
      n != null
      && lib.hasInfix "device_tracker.alex_phone" n.state
      && lib.hasInfix "zone.home" n.state
      && lib.hasInfix "< 10" n.state
      && lib.hasInfix "sensor.lanbat_people_where_alex" n.state
      && lib.hasInfix "not ns.inside" n.state
    )
    + expect "the active sensor includes the near sensor" (
      let
        a = lib.findFirst (e: e.name == "lighting hall active") null templateSensors;
      in
      lib.hasInfix "binary_sensor.lighting_approach_hall_near" a.state
    )
    + expect "room lights: offAfterSeconds defaults to 300" (
      (lt on).rooms.kitchen.offAfterSeconds == 300
    )
    + expect "room on: sensor, darkness and restart trigger it" (
      let
        t = roomOn.triggers;
      in
      lib.length t == 3
      && (lib.head t).entity_id == [ "binary_sensor.kitchen_presence_occupancy" ]
      && (lib.head t).to == "on"
      && (lib.elemAt t 1).entity_id == "binary_sensor.lighting_dark"
    )
    + expect "room on: not while paused, only when dark, occupied and every light off" (
      let
        c = json roomOn.conditions;
      in
      lib.hasInfix "lighting_paused" c
      && lib.hasInfix "binary_sensor.lighting_dark" c
      && lib.hasInfix "binary_sensor.kitchen_presence_occupancy" c
      && lib.hasInfix "switch.kitchen_light" c
    )
    + expect "room on: live run turns on only the room's lights, dry run only logs" (
      (lib.elemAt roomOnLive.actions 1).action == "homeassistant.turn_on"
      && (lib.elemAt roomOnLive.actions 1).target.entity_id == [ "switch.kitchen_light" ]
      && lib.length roomOn.actions == 1
    )
    + expect "room off: waits offAfterSeconds of the sensor off, and only if none is on" (
      let
        t = lib.head roomOff.triggers;
      in
      t.to == "off"
      && t.for.seconds == 300
      && lib.hasInfix "binary_sensor.kitchen_presence_occupancy" (json roomOff.conditions)
      && lib.hasInfix "homeassistant.turn_off" (json roomOffLive.actions)
      && lib.hasInfix "switch.kitchen_light" (json roomOffLive.actions)
    )
    + expect "the configuration check ran" (configCheck != null)
  }"
  if [ -n "$errors" ]; then
    printf '%s' "$errors" >&2
    exit 1
  fi
  touch $out
''
