# tests/follow-me.nix
#
# Evaluation checks for the follow-me module (modules/server/follow-me.nix):
# its settings and defaults, the mistakes it must reject, and what it generates
# for Home Assistant (lib/follow-me.nix). The generated templates are rendered by
# tests/follow-me-templates.py.
{ lib, pkgs }:

let
  fmLib = import ../lib/follow-me.nix { inherit lib; };

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
      "Office" = {
        lights = [ "light.office_lamp" ];
        illuminance = "sensor.office_lux";
      };
      "Kitchen" = {
        lights = [ "light.kitchen_strip" ];
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

  # Evenings, nights and the order they must come in.
  badClock = eval {
    enable = true;
    eveningFrom = "23:00";
    nightFrom = "22:30";
    rooms."Office".lights = [ "light.office_lamp" ];
  } { };

  # What the generator makes for two rooms (one with a light-level sensor, one
  # without) and two people.
  people = [
    {
      key = "alex";
      slug = "alex";
    }
    {
      key = "sam";
      slug = "sam";
    }
  ];
  genCfg = (fm on) // {
    people = [
      "alex"
      "sam"
    ];
    # A room can be watched without having lights to switch.
    rooms = (fm on).rooms // {
      "Hall" = {
        lights = [ ];
        illuminance = null;
        music = true;
      };
    };
  };
  gen = fmLib.mkFollowMe {
    cfg = genCfg;
    inherit people;
  };
  genLive = fmLib.mkFollowMe {
    cfg = genCfg // {
      dryRun = false;
    };
    inherit people;
  };
  templateEntities = lib.concatMap (
    b: (b.binary_sensor or [ ]) ++ (b.sensor or [ ])
  ) gen.template;
  entityNames = map (e: e.name) templateEntities;

  inLive = eval {
    enable = true;
    rooms."Office".lights = [ "light.office_lamp" ];
  } { };
  hacfg = inLive.services.home-assistant.config;

  actDry = fmLib.act {
    cfg = {
      dryRun = true;
    };
    message = "turn on the lights";
    steps = [ { action = "light.turn_on"; } ];
  };
  actLive = fmLib.act {
    cfg = {
      dryRun = false;
    };
    message = "turn on the lights";
    steps = [ { action = "light.turn_on"; } ];
  };

  automation = id: lib.findFirst (a: (a.id or "") == id) null gen.automation;
  automationLive = id: lib.findFirst (a: (a.id or "") == id) null genLive.automation;
  json = builtins.toJSON;
  lightsOn = automation "followme_lights_on_office";
  lightsOnLive = automationLive "followme_lights_on_office";
  litClear = automation "followme_lit_clear_office";

  generated = pkgs.writeText "follow-me-generated.json" (
    builtins.toJSON {
      inherit (gen) template;
      inherit (genCfg) unlocatedMinutes darkLux sunBelow;
    }
  );

  haPython = pkgs.home-assistant.python.withPackages (ps: [
    ps.homeassistant
    ps.colorlog # `hass --script check_config` imports it
  ]);

  # Home Assistant's own configuration check on what the generator makes, dry
  # and live. check_config exits 0 even for a broken automation, so its output
  # is what is read. (The NixOS module runs no such check at build time.)
  haConfig =
    g:
    (pkgs.formats.yaml { }).generate "followme.yaml" {
      sun = { };
      inherit (g) input_boolean input_text template automation;
    };
  configCheck = pkgs.runCommand "follow-me-config-check" { } ''
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
  templatesCheck = pkgs.runCommand "follow-me-templates-check" { } ''
    ${haPython}/bin/python ${./follow-me-templates.py} ${generated} | tee log
    ! grep -q '^FAIL' log
    touch $out
  '';

  expect = name: cond: if cond then "" else "FAIL: ${name}\n";
in
pkgs.runCommand "follow-me-check" { inherit templatesCheck configCheck; } ''
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
    + expect "a room's music setting defaults to on" (
      (fm on).rooms."Office".music == true && (fm on).rooms."Kitchen".music == false
    )
    + expect "a room's illuminance sensor is optional" ((fm on).rooms."Kitchen".illuminance == null)
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
    + expect "evening after night is rejected" (lib.length (failures badClock) == 1)
    + expect "the generator makes the global switches" (
      gen.input_boolean ? followme_paused && gen.input_boolean ? followme_guests
    )
    + expect "the generator makes a lit marker per room" (
      gen.input_boolean ? followme_lit_office && gen.input_boolean ? followme_lit_kitchen
    )
    + expect "a room name with a space and a digit makes a clean slug" (
      fmLib.slug "Bedroom 1" == "bedroom_1" && fmLib.slug "Living  Room!" == "living_room"
    )
    + expect "the generator makes a last-room helper per tracked person" (
      gen.input_text ? followme_lastroom_alex && gen.input_text ? followme_lastroom_sam
    )
    + expect "the generator makes occupied and dark sensors per room and one daypart" (
      lib.all (n: lib.elem n entityNames) [
        "followme Office occupied"
        "followme Kitchen occupied"
        "followme Office dark"
        "followme Kitchen dark"
        "followme daypart"
      ]
    )
    + expect "every generated sensor has a unique id" (
      lib.all (e: e ? unique_id) templateEntities
    )
    + expect "act in a dry run is one log step that says it would" (
      lib.length actDry == 1
      && (lib.head actDry).action == "logbook.log"
      && lib.hasPrefix "would " (lib.head actDry).data.message
    )
    + expect "act live is the log step, then the steps" (
      lib.length actLive == 2
      && (lib.head actLive).action == "logbook.log"
      && !(lib.hasPrefix "would " (lib.head actLive).data.message)
      && (lib.elemAt actLive 1).action == "light.turn_on"
    )
    + expect "the module hands the helpers, sensors and the sun to Home Assistant" (
      hacfg ? template && hacfg ? input_boolean && hacfg ? input_text && hacfg ? sun
    )
    + expect "a disabled module adds nothing to Home Assistant" (off.services.home-assistant.config == { })
    + expect "the template check ran" (templatesCheck != null)
    + expect "lights on: a rule for a room with lights" (lightsOn != null)
    + expect "lights on: none for a room with no lights" (automation "followme_lights_on_hall" == null)
    + expect "lights on: triggered by the room becoming occupied, off to on, after arriveSeconds" (
      let
        t = lib.head lightsOn.triggers;
      in
      t.trigger == "state"
      && t.entity_id == "binary_sensor.followme_office_occupied"
      && t.from == "off"
      && t.to == "on"
      && t.for.seconds == 10
    )
    + expect "lights on: not while paused, only when dark, in the evening or at night, and when every light is off" (
      let
        c = json lightsOn.conditions;
      in
      lib.hasInfix "input_boolean.followme_paused" c
      && lib.hasInfix "binary_sensor.followme_office_dark" c
      && lib.hasInfix "sensor.followme_daypart" c
      && lib.hasInfix "evening" c
      && lib.hasInfix "night" c
      && lib.hasInfix "light.office_lamp" c
    )
    + expect "lights on: never by day" (
      let
        daypartCondition = lib.findFirst (c: lib.hasInfix "sensor.followme_daypart" (json c)) null lightsOn.conditions;
      in
      daypartCondition != null && !(lib.hasInfix "day" (lib.replaceStrings [ "daypart" ] [ "" ] (json daypartCondition)))
    )
    + expect "lights on: night is dim and warm, evening is full" (
      let
        a = json lightsOnLive.actions;
      in
      lib.hasInfix "\"brightness_pct\":20" a
      && lib.hasInfix "\"brightness_pct\":100" a
      && lib.hasInfix "color_temp_kelvin" a
    )
    + expect "lights on: only the room's own lights are switched" (
      let
        switched = lib.filter (x: x ? action && x.action == "light.turn_on") (
          lib.concatMap (x: x.choose or [ x ]) lightsOnLive.actions
        );
        inChoose = lib.concatMap (
          x: lib.concatMap (c: c.sequence or [ ]) (x.choose or [ ]) ++ (x.default or [ ])
        ) lightsOnLive.actions;
        targets = map (x: x.target.entity_id) (
          lib.filter (x: x.action or "" == "light.turn_on") inChoose
        );
      in
      targets != [ ] && lib.all (t: t == [ "light.office_lamp" ]) targets
    )
    + expect "lights on: dry run logs and switches nothing" (
      let
        dry = automation "followme_lights_on_office";
      in
      lib.length dry.actions == 1 && (lib.head dry.actions).action == "logbook.log"
      && lib.hasPrefix "would " (lib.head dry.actions).data.message
    )
    + expect "lights on: live run marks the room before it switches" (
      (lib.head lightsOnLive.actions).action == "logbook.log"
      && lib.hasInfix "input_boolean.followme_lit_office" (json (lib.elemAt lightsOnLive.actions 1))
    )
    + expect "manual off: the marker clears when the room's lights all go off" (
      litClear != null
      && (lib.head litClear.triggers).entity_id == [ "light.office_lamp" ]
      && (lib.head litClear.triggers).to == "off"
      && lib.hasInfix "input_boolean.turn_off" (json litClear.actions)
      && lib.hasInfix "followme_lit_office" (json litClear.actions)
    )
  }"
  if [ -n "$errors" ]; then
    printf '%s' "$errors" >&2
    exit 1
  fi
  touch $out
''
