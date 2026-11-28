# lib/lighting.nix
#
# What lighting (modules/server/lighting.nix) gives Home Assistant: a "dark
# outside" sensor, an "activity" helper per approach routine, and the
# automations, generated from its settings. Pure: nothing here knows a light or
# a sensor; they all arrive in `cfg`.
#
# Entities (key_slug comes from `slug`):
#   binary_sensor.lighting_dark                   dark outside (sun elevation)
#   binary_sensor.lighting_<key>_active  someone at the entrance or in the hall
#   input_boolean.lighting_paused                 pauses every rule (off: running)
{ lib }:

let
  inherit (builtins) toJSON;

  # As follow-me and the dashboard generator name entities: runs of anything but
  # a-z0-9 become one "_", none at the ends.
  slug =
    s:
    lib.concatStringsSep "_" (
      lib.filter (p: builtins.isString p && p != "") (builtins.split "[^a-z0-9]+" (lib.toLower s))
    );

  # A decision, as the steps that make it happen: always a logbook line, and the
  # steps themselves only when it is not a dry run. A dry run's line says what
  # lighting would do.
  act =
    {
      cfg,
      message,
      steps,
    }:
    [
      {
        action = "logbook.log";
        data = {
          name = "Lighting";
          message = if cfg.dryRun then "would ${message}" else message;
        };
      }
    ]
    ++ lib.optionals (!cfg.dryRun) steps;

  # Dark outside: the sun is below `sunBelow`. One sensor for every rule.
  dark = cfg: {
    name = "lighting dark";
    unique_id = "lighting_dark";
    state = "{{ (state_attr('sun.sun', 'elevation') | float(90)) < ${toString cfg.sunBelow} }}";
    availability = "{{ is_number(state_attr('sun.sun', 'elevation')) }}";
  };

  # Someone is at the entrance or in the hall: any approach or presence sensor
  # is on. Unknown and unavailable sensors are not "absent": with none of them
  # reporting, the sensor is unavailable and the off rule does not fire.
  active =
    key: rc:
    let
      sensors =
        rc.approach ++ rc.presence ++ lib.optional (hasNear rc) "binary_sensor.lighting_${slug key}_near";
    in
    {
      name = "lighting ${key} active";
      unique_id = "lighting_approach_${slug key}_active";
      state = ''
        {% set ns = namespace(on=false) %}
        {% for s in ${toJSON sensors} %}
          {% if states(s) == 'on' %}{% set ns.on = true %}{% endif %}
        {% endfor %}
        {{ ns.on }}
      '';
      availability = ''
        {% set ns = namespace(known=false) %}
        {% for s in ${toJSON sensors} %}
          {% if states(s) not in ['unknown', 'unavailable'] %}{% set ns.known = true %}{% endif %}
        {% endfor %}
        {{ ns.known }}
      '';
    };

  # Whether the routine has any source of "someone is near the house".
  hasNear = rc: rc.distance != [ ] || rc.trackers != [ ];

  # Someone outside is within nearMeters of the house: a distance sensor or a phone's GPS
  # distance to zone.home reads below it and nobody is inside, so being indoors (inside the radius) never
  # counts. Unavailable when no distance sensor reports.
  near = key: rc: {
    name = "lighting ${key} near";
    unique_id = "lighting_approach_${slug key}_near";
    state = ''
      {% set ns = namespace(near=false, inside=false) %}
      {% for s in ${toJSON rc.distance} %}
        {% if is_number(states(s)) and states(s) | float < ${toString rc.nearMeters} %}{% set ns.near = true %}{% endif %}
      {% endfor %}
      {% for t in ${toJSON rc.trackers} %}
        {% set d = distance(t, 'zone.home') %}
        {% if d is number and d * 1000 < ${toString rc.nearMeters} %}{% set ns.near = true %}{% endif %}
      {% endfor %}
      {% for s in ${toJSON rc.inside} %}
        {% if states(s) not in ['away', 'unknown', 'unavailable', '''] %}{% set ns.inside = true %}{% endif %}
      {% endfor %}
      {{ ns.near and not ns.inside }}
    '';
    availability = ''
      {% set ns = namespace(known=false) %}
      {% for s in ${toJSON rc.distance} %}
        {% if is_number(states(s)) %}{% set ns.known = true %}{% endif %}
      {% endfor %}
      {% for t in ${toJSON rc.trackers} %}
        {% if distance(t, 'zone.home') is number %}{% set ns.known = true %}{% endif %}
      {% endfor %}
      {{ ns.known }}
    '';
  };

  # No listed light is on.
  allOff =
    lights: "{{ expand(${toJSON lights}) | selectattr('state', 'eq', 'on') | list | count == 0 }}";

  # At least one listed light is on.
  anyOn =
    lights: "{{ expand(${toJSON lights}) | selectattr('state', 'eq', 'on') | list | count > 0 }}";

  notPaused = {
    condition = "state";
    entity_id = "input_boolean.lighting_paused";
    state = "off";
  };

  darkOn = {
    condition = "state";
    entity_id = "binary_sensor.lighting_dark";
    state = "on";
  };

  # The fairy lights (or any "on at dusk" routine) come on when it gets dark,
  # and again on a restart that happens after dark, so they never sit unlit all
  # night.
  duskOn =
    cfg: key: rc:
    let
      r = slug key;
    in
    {
      id = "lighting_dusk_${r}_on";
      alias = "Lighting: ${key} on at dusk";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = "binary_sensor.lighting_dark";
          from = "off";
          to = "on";
        }
        {
          trigger = "homeassistant";
          event = "start";
        }
      ];
      conditions = [
        notPaused
        darkOn
        {
          condition = "template";
          value_template = allOff rc.lights;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn on the ${key} lights (dark)";
        steps = [
          {
            action = "homeassistant.turn_on";
            target.entity_id = rc.lights;
          }
        ];
      };
    };

  duskOff =
    cfg: key: rc:
    let
      r = slug key;
    in
    {
      id = "lighting_dusk_${r}_off";
      alias = "Lighting: ${key} off in the early morning";
      mode = "single";
      triggers = [
        {
          trigger = "time";
          at = "${rc.offAt}:00";
        }
      ];
      conditions = [
        notPaused
        {
          condition = "template";
          value_template = anyOn rc.lights;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn off the ${key} lights (early morning)";
        steps = [
          {
            action = "homeassistant.turn_off";
            target.entity_id = rc.lights;
          }
        ];
      };
    };

  # A room's lights come on when one of its sensors reports someone in the dark:
  # the sensor turning on, it getting dark with someone already there, or a
  # restart. Lights that are already on are left alone.
  roomOn =
    cfg: key: rc:
    let
      r = slug key;
    in
    {
      id = "lighting_room_${r}_on";
      alias = "Lighting: ${key} lights on";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = rc.sensors;
          to = "on";
        }
        {
          trigger = "state";
          entity_id = "binary_sensor.lighting_dark";
          from = "off";
          to = "on";
        }
        {
          trigger = "homeassistant";
          event = "start";
        }
      ];
      conditions = [
        notPaused
        darkOn
        {
          condition = "template";
          value_template = anyOn rc.sensors;
        }
        {
          condition = "template";
          value_template = allOff rc.lights;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn on the ${key} lights (someone is there and it is dark)";
        steps = [
          {
            action = "homeassistant.turn_on";
            target.entity_id = rc.lights;
          }
        ];
      };
    };

  # They go off once every sensor has reported nobody for offAfterSeconds. A
  # sensor that is unknown or unavailable is not "nobody": only a real change to
  # off starts the wait.
  roomOff =
    cfg: key: rc:
    let
      r = slug key;
    in
    {
      id = "lighting_room_${r}_off";
      alias = "Lighting: ${key} lights off";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = rc.sensors;
          to = "off";
          for.seconds = rc.offAfterSeconds;
        }
      ];
      conditions = [
        notPaused
        {
          condition = "template";
          value_template = allOff rc.sensors;
        }
        {
          condition = "template";
          value_template = anyOn rc.lights;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn off the ${key} lights (nobody there)";
        steps = [
          {
            action = "homeassistant.turn_off";
            target.entity_id = rc.lights;
          }
        ];
      };
    };

  # The right hallway light comes on when someone approaches the entrance at
  # night.
  approachRightOn =
    cfg: key: rc:
    let
      r = slug key;
    in
    {
      id = "lighting_approach_${r}_right_on";
      alias = "Lighting: ${key} right light on";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = rc.approach;
          to = "on";
        }
      ];
      conditions = [
        notPaused
        darkOn
        {
          condition = "template";
          value_template = allOff rc.right;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn on the ${key} right light (someone is approaching)";
        steps = [
          {
            action = "homeassistant.turn_on";
            target.entity_id = rc.right;
          }
        ];
      };
    };

  # Someone outside comes within range at night: the porch and the hallway light
  # come on together. Someone already inside is excluded by the sensor, so only
  # the hall's presence rule below lights the hallway for them.
  approachNearOn =
    cfg: key: rc:
    let
      r = slug key;
      lights = rc.right ++ rc.left;
    in
    {
      id = "lighting_approach_${r}_near_on";
      alias = "Lighting: ${key} lights on (someone within ${toString rc.nearMeters} m)";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = "binary_sensor.lighting_${r}_near";
          to = "on";
        }
      ];
      conditions = [
        notPaused
        darkOn
        {
          condition = "template";
          value_template = allOff lights;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn on the ${key} lights (someone is within ${toString rc.nearMeters} m)";
        steps = [
          {
            action = "homeassistant.turn_on";
            target.entity_id = lights;
          }
        ];
      };
    };

  # The left hallway light comes on once presence is in the hall.
  approachLeftOn =
    cfg: key: rc:
    let
      r = slug key;
    in
    {
      id = "lighting_approach_${r}_left_on";
      alias = "Lighting: ${key} left light on";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = rc.presence;
          to = "on";
        }
      ];
      conditions = [
        notPaused
        darkOn
        {
          condition = "template";
          value_template = allOff rc.left;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn on the ${key} left light (someone is in the hall)";
        steps = [
          {
            action = "homeassistant.turn_on";
            target.entity_id = rc.left;
          }
        ];
      };
    };

  # Both lights off once nobody has been at the entrance or in the hall for a
  # while.
  approachOff =
    cfg: key: rc:
    let
      r = slug key;
      lights = rc.right ++ rc.left;
    in
    {
      id = "lighting_approach_${r}_off";
      alias = "Lighting: ${key} lights off";
      mode = "single";
      triggers = [
        {
          trigger = "state";
          entity_id = "binary_sensor.lighting_${r}_active";
          from = "on";
          to = "off";
          for.seconds = rc.offAfterSeconds;
        }
      ];
      conditions = [
        notPaused
        {
          condition = "template";
          value_template = anyOn lights;
        }
      ];
      actions = act {
        inherit cfg;
        message = "turn off the ${key} lights (nobody there)";
        steps = [
          {
            action = "homeassistant.turn_off";
            target.entity_id = lights;
          }
        ];
      };
    };

  mkLighting =
    { cfg }:
    let
      approachKeys = lib.filterAttrs (
        _: rc: rc.approach != [ ] || rc.presence != [ ] || hasNear rc
      ) cfg.approach;
    in
    {
      input_boolean = {
        lighting_paused = {
          name = "Pause lighting";
          icon = "mdi:pause-circle-outline";
        };
      };

      template = [
        {
          binary_sensor = [
            (dark cfg)
          ]
          ++ lib.mapAttrsToList (k: rc: active k rc) approachKeys
          ++ lib.mapAttrsToList (k: rc: near k rc) (lib.filterAttrs (_: rc: hasNear rc) approachKeys);
        }
      ];

      automation =
        lib.concatLists (
          lib.mapAttrsToList (k: rc: [
            (duskOn cfg k rc)
            (duskOff cfg k rc)
          ]) cfg.duskRoutines
        )
        ++ lib.concatLists (
          lib.mapAttrsToList (k: rc: [
            (roomOn cfg k rc)
            (roomOff cfg k rc)
          ]) cfg.rooms
        )
        ++ lib.concatLists (
          lib.mapAttrsToList (
            k: rc:
            lib.optionals (rc.approach != [ ] && rc.right != [ ]) [
              (approachRightOn cfg k rc)
            ]
            ++ lib.optionals (rc.presence != [ ] && rc.left != [ ]) [
              (approachLeftOn cfg k rc)
            ]
            ++ lib.optionals (hasNear rc && (rc.right != [ ] || rc.left != [ ])) [
              (approachNearOn cfg k rc)
            ]
            ++
              lib.optionals
                ((rc.approach != [ ] || rc.presence != [ ] || hasNear rc) && (rc.right != [ ] || rc.left != [ ]))
                [
                  (approachOff cfg k rc)
                ]
          ) cfg.approach
        );
    };
in
{
  inherit slug act mkLighting;
}
