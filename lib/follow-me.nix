# lib/follow-me.nix
#
# What follow-me (modules/server/follow-me.nix) gives Home Assistant: helpers,
# template sensors and automations, generated from its settings. Pure: nothing
# here knows a room, a light or a person; they all arrive in `cfg` and `people`.
#
# Entities (room_slug and person_slug come from `slug`):
#   input_boolean.followme_paused            pauses every rule (off: running)
#   input_boolean.followme_guests            suspends the "empty room" rules
#   input_boolean.followme_lit_<room_slug>   follow-me turned this room's lights on
#   input_text.followme_lastroom_<person>    the last covered room the person was in
#   binary_sensor.followme_<room>_occupied   see occupied below
#   binary_sensor.followme_<room>_dark       dark enough to want lights
#   sensor.followme_daypart                  day, evening or night
{ lib }:

let
  inherit (builtins) toJSON;

  # As the assistant router names a person's entities (mqtt.slug) and the
  # dashboard generator does (person_slug): runs of anything but a-z0-9 become
  # one "_", none at the ends.
  slug =
    s:
    lib.concatStringsSep "_" (
      lib.filter (p: builtins.isString p && p != "") (builtins.split "[^a-z0-9]+" (lib.toLower s))
    );

  whereSensor = person: "sensor.lanbat_people_where_${person.slug}";
  lastRoomHelper = person: "input_text.followme_lastroom_${person.slug}";

  # A decision, as the steps that make it happen: always a logbook line, and the
  # steps themselves only when it is not a dry run. A dry run's line says what
  # follow-me would do.
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
          name = "Follow-me";
          message = if cfg.dryRun then "would ${message}" else message;
        };
      }
    ]
    ++ lib.optionals (!cfg.dryRun) steps;

  # Whether the room has a tracked person in it. A person the house hears but
  # cannot place ("home") still holds the last room they were in for
  # unlocatedMinutes: phones drop out of Bluetooth for minutes at a time.
  # Unknown and unavailable sensors are not "empty": with none of them
  # reporting, the sensor is unavailable and nothing acts on it.
  trackers =
    people:
    toJSON (
      map (p: {
        w = whereSensor p;
        l = lastRoomHelper p;
      }) people
    );

  occupied =
    cfg: people: room: {
      name = "followme ${room} occupied";
      unique_id = "followme_${slug room}_occupied";
      state = ''
        {% set room = ${toJSON room} %}
        {% set ns = namespace(on=false) %}
        {% for p in ${trackers people} %}
          {% set w = states(p.w) %}
          {% if w == room %}
            {% set ns.on = true %}
          {% elif w == 'home' and states(p.l) == room
                  and (now() - states[p.w].last_changed).total_seconds() < ${toString (cfg.unlocatedMinutes * 60)} %}
            {% set ns.on = true %}
          {% endif %}
        {% endfor %}
        {{ ns.on }}
      '';
      availability = ''
        {% set ns = namespace(known=false) %}
        {% for p in ${trackers people} %}
          {% if states(p.w) not in ['unknown', 'unavailable'] %}{% set ns.known = true %}{% endif %}
        {% endfor %}
        {{ ns.known }}
      '';
    };

  # Dark by the room's light-level sensor, or by the sun when it has none or the
  # sensor is not answering.
  dark =
    cfg: room: roomCfg:
    let
      byLux = roomCfg.illuminance;
      byTheSun = "(state_attr('sun.sun', 'elevation') | float(90)) < ${toString cfg.sunBelow}";
    in
    {
      name = "followme ${room} dark";
      unique_id = "followme_${slug room}_dark";
      state =
        if byLux == null then
          "{{ ${byTheSun} }}"
        else
          "{% if is_number(states('${byLux}')) %}{{ states('${byLux}') | float < ${toString cfg.darkLux} }}{% else %}{{ ${byTheSun} }}{% endif %}";
      availability =
        "{{ is_number(state_attr('sun.sun', 'elevation'))"
        + lib.optionalString (byLux != null) " or is_number(states('${byLux}'))"
        + " }}";
    };

  daypart = cfg: {
    name = "followme daypart";
    unique_id = "followme_daypart";
    state = ''
      {% set t = now().strftime('%H:%M') %}
      {% if t < '${cfg.morningTo}' or t >= '${cfg.nightFrom}' %}night{% elif t < '${cfg.eveningFrom}' %}day{% else %}evening{% endif %}
    '';
  };

  mkFollowMe =
    { cfg, people }:
    let
      rooms = cfg.rooms;
      roomSlugs = map slug (lib.attrNames rooms);
      lit = r: "followme_lit_${slug r}";
    in
    {
      input_boolean = {
        followme_paused = {
          name = "Pause follow-me";
          icon = "mdi:pause-circle-outline";
        };
        followme_guests = {
          name = "Guests here";
          icon = "mdi:account-multiple";
        };
      }
      // lib.mapAttrs' (r: _: {
        name = lit r;
        value = {
          name = "Follow-me lit ${r}";
          icon = "mdi:lightbulb-on-outline";
        };
      }) rooms;

      input_text = lib.listToAttrs (
        map (p: {
          name = "followme_lastroom_${p.slug}";
          value = {
            name = "Follow-me last room of ${p.key}";
            max = 100;
          };
        }) people
      );

      template = [
        {
          binary_sensor = lib.concatLists (
            lib.mapAttrsToList (r: rc: [
              (occupied cfg people r)
              (dark cfg r rc)
            ]) rooms
          );
          sensor = [ (daypart cfg) ];
        }
      ];

      automation = [ ];
    };
in
{
  inherit slug act mkFollowMe;
}
