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
    builtins.deepSeq
      (fm (
        eval {
          enable = true;
          rooms."Office".lights = [ "lock.front_door" ];
        } { }
      )).rooms
      true
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
      "Bedroom 1" = {
        lights = [ "light.bedroom_1_left" ];
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
  genAnyCfg = genCfg // {
    lightsOffAny = true;
    lightsOffAnyMinutes = 10;
  };
  genAny = fmLib.mkFollowMe {
    cfg = genAnyCfg;
    inherit people;
  };
  genAnyLive = fmLib.mkFollowMe {
    cfg = genAnyCfg // {
      dryRun = false;
    };
    inherit people;
  };
  templateEntities = lib.concatMap (b: (b.binary_sensor or [ ]) ++ (b.sensor or [ ])) gen.template;
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
  musicPause = automation "followme_music_pause_office";
  musicPauseLive = automationLive "followme_music_pause_office";
  lastRoom = automation "followme_lastroom_alex";
  musicFollow = automation "followme_music_follow_alex";
  musicFollowLive = automationLive "followme_music_follow_alex";
  lightsOff = automation "followme_lights_off_office";
  lightsOffLive = automationLive "followme_lights_off_office";
  automationAny = id: lib.findFirst (a: (a.id or "") == id) null genAny.automation;
  automationAnyLive = id: lib.findFirst (a: (a.id or "") == id) null genAnyLive.automation;
  lightsOffAny = automationAny "followme_lights_off_any_office";
  lightsOffAnyLive = automationAnyLive "followme_lights_off_any_office";
  lightsOffAnyKitchen = automationAny "followme_lights_off_any_kitchen";

  generated = pkgs.writeText "follow-me-generated.json" (
    builtins.toJSON {
      inherit (gen) template automation;
      automationLightsOffAny = genAny.automation;
      automationLightsOffAnyLive = genAnyLive.automation;
      inherit (genCfg)
        unlocatedMinutes
        darkLux
        sunBelow
        followCooldownSeconds
        ;
      lightsOffAnyMinutes = genAnyCfg.lightsOffAnyMinutes;
      kitchenLights = genAnyCfg.rooms.Kitchen.lights;
      echoShowKitchenLight = "light.kitchen_echo_screen";
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
      inherit (g)
        input_boolean
        input_text
        template
        automation
        ;
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
      && c.lightsOffAny == false
      && c.lightsOffAnyMinutes == 10
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
    + expect "every generated sensor has a unique id" (lib.all (e: e ? unique_id) templateEntities)
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
    + expect "a disabled module adds nothing to Home Assistant" (
      off.services.home-assistant.config == { }
    )
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
    +
      expect
        "lights on: not while paused, only when dark, in the evening or at night, and when every light is off"
        (
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
        daypartCondition = lib.findFirst (
          c: lib.hasInfix "sensor.followme_daypart" (json c)
        ) null lightsOn.conditions;
      in
      daypartCondition != null
      && !(lib.hasInfix "day" (lib.replaceStrings [ "daypart" ] [ "" ] (json daypartCondition)))
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
        targets = map (x: x.target.entity_id) (lib.filter (x: x.action or "" == "light.turn_on") inChoose);
      in
      targets != [ ] && lib.all (t: t == [ "light.office_lamp" ]) targets
    )
    + expect "lights on: dry run logs and switches nothing" (
      let
        dry = automation "followme_lights_on_office";
      in
      lib.length dry.actions == 1
      && (lib.head dry.actions).action == "logbook.log"
      && lib.hasPrefix "would " (lib.head dry.actions).data.message
    )
    + expect "lights on: live run marks the room before it switches" (
      (lib.head lightsOnLive.actions).action == "logbook.log"
      && lib.hasInfix "input_boolean.followme_lit_office" (json (lib.elemAt lightsOnLive.actions 1))
    )
    + expect "lights off: a rule for a room with lights, none for one without" (
      lightsOff != null && automation "followme_lights_off_hall" == null
    )
    + expect "lights off: triggered by the room going from occupied to empty for lightsOffMinutes" (
      let
        t = lib.head lightsOff.triggers;
      in
      t.entity_id == "binary_sensor.followme_office_occupied"
      && t.from == "on"
      && t.to == "off"
      && t.for.minutes == 5
    )
    + expect "lights off: never from unavailable (a router restart is not an empty room)" (
      (lib.head lightsOff.triggers).from == "on"
    )
    + expect "lights off: only lights follow-me turned on, not while paused, not with guests" (
      let
        c = json lightsOff.conditions;
      in
      lib.hasInfix "input_boolean.followme_lit_office" c
      && lib.hasInfix "input_boolean.followme_paused" c
      && lib.hasInfix "input_boolean.followme_guests" c
    )
    + expect "lights off: live run turns off the room's lights, then clears the marker" (
      let
        a = json lightsOffLive.actions;
      in
      lib.hasInfix "light.turn_off" a
      && lib.hasInfix "light.office_lamp" a
      && lib.hasInfix "input_boolean.turn_off" a
      && lib.hasInfix "followme_lit_office" a
      && !(lib.hasInfix "light.turn_on" a)
    )
    + expect "lights off: dry run only logs" (
      lib.length lightsOff.actions == 1
      && lib.hasPrefix "would " (lib.head lightsOff.actions).data.message
    )
    + expect "lights off any: absent by default" (automation "followme_lights_off_any_office" == null)
    + expect "lights off any: a rule per room with lights when enabled, none without" (
      lightsOffAny != null
      && automationAny "followme_lights_off_any_hall" == null
      && lightsOffAnyKitchen != null
    )
    + expect "lights off any: triggered by occupied on to off for lightsOffAnyMinutes" (
      let
        t = lib.head lightsOffAny.triggers;
      in
      t.entity_id == "binary_sensor.followme_office_occupied"
      && t.from == "on"
      && t.to == "off"
      && t.for.minutes == 10
    )
    + expect "lights off any: never from unavailable (on to off only)" (
      (lib.head lightsOffAny.triggers).from == "on"
    )
    + expect "lights off any: not while paused or with guests, only when a room light is on" (
      let
        c = json lightsOffAny.conditions;
      in
      lib.hasInfix "input_boolean.followme_paused" c
      && lib.hasInfix "input_boolean.followme_guests" c
      && !(lib.hasInfix "followme_lit_office" c)
      && lib.hasInfix "light.office_lamp" c
    )
    + expect "lights off any: live run turns off only the room's configured lights" (
      let
        a = json lightsOffAnyLive.actions;
      in
      lib.hasInfix "light.turn_off" a
      && lib.hasInfix "light.office_lamp" a
      && !(lib.hasInfix "followme_lit" a)
      && !(lib.hasInfix "light.turn_on" a)
    )
    + expect "lights off any: dry run logs the hand-switched message" (
      lib.length lightsOffAny.actions == 1
      && lib.hasPrefix "would " (lib.head lightsOffAny.actions).data.message
      && lib.hasInfix "switched by hand" (lib.head lightsOffAny.actions).data.message
    )
    + expect "lights off any: kitchen targets only its listed lights, not the Echo Show" (
      let
        live = automationAnyLive "followme_lights_off_any_kitchen";
        steps = lib.elemAt live.actions 1;
      in
      steps.action == "light.turn_off" && steps.target.entity_id == [ "light.kitchen_strip" ]
    )
    +
      expect "last room: written whenever a tracked person's sensor names a covered room, dry run or not"
        (
          lastRoom != null
          && (lib.head lastRoom.triggers).entity_id == "sensor.lanbat_people_where_alex"
          && lib.hasInfix "input_text.set_value" (json lastRoom.actions)
          && lib.hasInfix "input_text.followme_lastroom_alex" (json lastRoom.actions)
          && !(lib.hasInfix "logbook" (json lastRoom.actions))
        )
    + expect "last room: only rooms, never home, away, unknown or unavailable (a phone gap keeps it)" (
      let
        to = (lib.head lastRoom.triggers).to;
      in
      lib.sort (a: b: a < b) to == [
        "Bedroom 1"
        "Hall"
        "Kitchen"
        "Office"
      ]
    )
    + expect "music: one rule per tracked person" (
      musicFollow != null && automation "followme_music_follow_sam" != null
    )
    + expect "music: triggered by the person's last room changing, held for followSeconds" (
      let
        t = lib.head musicFollow.triggers;
      in
      t.entity_id == "input_text.followme_lastroom_alex" && t.for.seconds == 20
    )
    + expect "music: only between rooms that have music on" (
      let
        c = json musicFollow.conditions;
      in
      lib.hasInfix "Office" c
      && lib.hasInfix "Bedroom 1" c
      && lib.hasInfix "Hall" c
      && !(lib.hasInfix "Kitchen" c)
    )
    +
      expect "music: not while paused, source playing, source room left empty, destination free, cooldown"
        (
          let
            c = json musicFollow.conditions;
            v = json musicFollow.variables;
          in
          lib.hasInfix "input_boolean.followme_paused" c
          && lib.hasInfix "playing" c
          && lib.hasInfix "_occupied" c
          && lib.hasInfix "last_triggered" c
          && lib.hasInfix "60" c
          && lib.hasInfix "dst_free" v
        )
    + expect "music: only Music Assistant players (no TV, no Kodi)" (
      let
        v = json musicFollow.variables;
      in
      lib.hasInfix "integration_entities('music_assistant')" v
      && !(lib.hasInfix "kodi" v)
      && !(lib.hasInfix "androidtv" v)
    )
    + expect "music: live run moves the queue to the free player, and does not stop anything itself" (
      let
        a = json musicFollowLive.actions;
      in
      lib.hasInfix "music_assistant.transfer_queue" a
      && lib.hasInfix "source_player" a
      && lib.hasInfix "auto_play" a
      && !(lib.hasInfix "media_stop" a)
      && !(lib.hasInfix "media_pause" a)
    )
    + expect "music: dry run only logs" (
      lib.length musicFollow.actions == 1
      && lib.hasPrefix "would " (lib.head musicFollow.actions).data.message
    )
    + expect "music: no rule when fewer than two rooms have music" (
      let
        one = fmLib.mkFollowMe {
          cfg = genCfg // {
            rooms = {
              "Office" = {
                lights = [ ];
                illuminance = null;
                music = true;
              };
            };
          };
          inherit people;
        };
      in
      !(lib.any (a: lib.hasPrefix "followme_music_follow" (a.id or "")) one.automation)
    )
    + expect "pause: a rule for each room with music on, none for a room with music off" (
      musicPause != null && automation "followme_music_pause_kitchen" == null
    )
    + expect "pause: triggered by the room going from occupied to empty for musicPauseMinutes" (
      let
        t = lib.head musicPause.triggers;
      in
      t.entity_id == "binary_sensor.followme_office_occupied"
      && t.from == "on"
      && t.to == "off"
      && t.for.minutes == 15
    )
    + expect "pause: not while paused or with guests, and only if something is playing" (
      let
        c = json musicPause.conditions;
      in
      lib.hasInfix "input_boolean.followme_paused" c
      && lib.hasInfix "input_boolean.followme_guests" c
      && lib.hasInfix "players" c
    )
    + expect "pause: pauses the room's Music Assistant players, never stops, never a TV" (
      let
        a = json musicPauseLive.actions;
        v = json musicPauseLive.variables;
      in
      lib.hasInfix "media_player.media_pause" a
      && !(lib.hasInfix "media_stop" a)
      && lib.hasInfix "integration_entities('music_assistant')" v
      && !(lib.hasInfix "kodi" v)
      && !(lib.hasInfix "androidtv" v)
    )
    + expect "pause: dry run only logs" (
      lib.length musicPause.actions == 1
      && lib.hasPrefix "would " (lib.head musicPause.actions).data.message
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
