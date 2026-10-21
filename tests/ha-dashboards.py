"""Checks for pkgs/home-assistant-dashboards on a small, made-up home.

Builds Home Assistant registries for one of each kind of device the
generator sorts (a dimmable bulb, a light switch, a plug with power and
energy, a thermometer, a soil sensor and a water valve in the garden, a
Frigate camera and zone, a music player, a voice satellite, a battery
sensor, an update, a device in no room), runs the generator twice and
checks what it wrote.
"""
import json
import os
import subprocess
import sys
import tempfile

GEN = sys.argv[1]


def store(key, data):
    return {"version": 1, "minor_version": 1, "key": key, "data": data}


AREAS = [{"id": "living", "name": "Living Room", "icon": None},
         {"id": "garden", "name": "Garden", "icon": None},
         {"id": "office", "name": "Office", "icon": None}]
DEVICES = [
    ("bulb", "lounge_spot", "living"), ("wallswitch", "light", "living"), ("plug", "lounge_heater", "living"),
    ("thermo", "Office thermometer", "office"), ("soil", "garden_sensor", "garden"),
    ("valve", "Garden Tap", "garden"), ("cam", "C1", "garden"), ("zone", "Driveway", None),
    ("player", "office speaker", "office"), ("sat", "Office Satellite", "office"),
    ("remote", "button remote", None), ("bridge", "Zigbee2MQTT Bridge", None),
]
ENTITIES = [
    # entity_id, platform, device, class, unit, category
    ("light.lounge_spot", "mqtt", "bulb", None, None, None),
    ("switch.lounge_light", "mqtt", "wallswitch", None, None, None),
    ("number.lounge_light_countdown", "mqtt", "wallswitch", None, "s", "config"),
    ("switch.lounge_heater", "mqtt", "plug", None, None, None),
    ("sensor.lounge_heater_power", "mqtt", "plug", "power", "W", None),
    ("sensor.lounge_heater_energy", "mqtt", "plug", "energy", "kWh", None),
    ("sensor.office_temperature", "mqtt", "thermo", "temperature", "°C", None),
    ("sensor.office_humidity", "mqtt", "thermo", "humidity", "%", None),
    ("sensor.garden_sensor_soil_moisture", "mqtt", "soil", "moisture", "%", None),
    ("sensor.garden_sensor_temperature", "mqtt", "soil", "temperature", "°C", None),
    ("switch.garden_valve", "mqtt", "valve", None, None, None),
    ("sensor.garden_valve_daily_irrigation_volume", "mqtt", "valve", None, "L", None),
    ("camera.c1", "frigate", "cam", None, None, None),
    ("binary_sensor.c1_person_occupancy", "frigate", "cam", "occupancy", None, None),
    ("binary_sensor.c1_all_occupancy", "frigate", "cam", "occupancy", None, None),
    ("image.c1_person", "frigate", "cam", None, None, None),
    ("switch.c1_detect", "frigate", "cam", None, None, "config"),
    ("binary_sensor.driveway_all_occupancy", "frigate", "zone", "occupancy", None, None),
    ("sensor.driveway_car_active_count", "frigate", "zone", None, "objects", None),
    ("media_player.office_speaker", "music_assistant", "player", "speaker", None, None),
    ("assist_satellite.office_satellite", "esphome", "sat", None, None, None),
    ("media_player.office_satellite", "esphome", "sat", None, None, None),
    ("sensor.button_remote_battery", "mqtt", "remote", "battery", "%", "diagnostic"),
    ("update.button_remote", "mqtt", "remote", None, None, "config"),
    ("switch.zigbee2mqtt_bridge_permit_join", "mqtt", "bridge", None, None, None),
    ("binary_sensor.zigbee2mqtt_bridge_connection_state", "mqtt", "bridge", "connectivity", None, "diagnostic"),
    ("weather.forecast_home", "met", None, None, None, None),
    ("person.someone", "person", None, None, None, None),
    ("sensor.disabled_thing", "mqtt", "plug", "power", "W", None),
    # Follow-me (lib/follow-me.nix): two switches, a room's occupied sensor, a rule.
    ("input_boolean.followme_paused", "input_boolean", None, None, None, None),
    ("input_boolean.followme_guests", "input_boolean", None, None, None, None),
    ("binary_sensor.followme_office_occupied", "template", None, None, None, None),
    ("automation.follow_me_lights_on_office", "automation", None, None, None, None),
]
DISABLED = {"sensor.disabled_thing"}


def write_fixture(d):
    os.makedirs(d, exist_ok=True)
    w = lambda n, data: json.dump(store(n, data), open(os.path.join(d, n), "w"))
    w("core.area_registry", {"areas": AREAS})
    w("core.device_registry", {"devices": [
        {"id": i, "name": n, "name_by_user": None, "area_id": a, "disabled_by": None,
         "manufacturer": "Zigbee2MQTT" if i == "bridge" else "Acme", "model": "x"} for i, n, a in DEVICES]})
    w("core.entity_registry", {"entities": [
        {"entity_id": e, "platform": p, "device_id": dev, "device_class": None, "original_device_class": c,
         "unit_of_measurement": u, "entity_category": cat, "area_id": None,
         "disabled_by": "user" if e in DISABLED else None, "hidden_by": None, "unique_id": e}
        for e, p, dev, c, u, cat in ENTITIES]})
    w("core.config_entries", {"entries": []})


def run(storage, out):
    subprocess.run([GEN, "--storage", storage, "--out", out, "--links",
                    json.dumps({"Frigate": "https://frigate.example.com", "Grafana": "https://grafana.example.com"})],
                   check=True)
    return {n: json.load(open(os.path.join(out, n))) for n in sorted(os.listdir(out))}


def walk(node, path="config"):
    """Every card: (path, card)."""
    if isinstance(node, dict):
        if "type" in node and path.split(".")[-1] not in ("features", "badges", "strategy"):
            yield path, node
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path)


def entities_in(node):
    s = json.dumps(node)
    return {e for e, *_ in ENTITIES if f'"{e}"' in s}


failures = []


def expect(name, ok):
    if not ok:
        failures.append(name)
    print(("ok   " if ok else "FAIL ") + name)


with tempfile.TemporaryDirectory() as tmp:
    storage = os.path.join(tmp, "storage")
    write_fixture(storage)
    a = run(storage, os.path.join(tmp, "a"))
    b = run(storage, os.path.join(tmp, "b"))
    expect("the output is the same on every run", a == b)
    expect("it writes the Overview, Cameras, System and the merge fragments",
           set(a) == {"lovelace.lovelace", "lovelace.lanbat-cameras", "lovelace.lanbat-system", "dashboards.json",
                      "energy-devices.json", "all-lights.json"})
    for name in ("lovelace.lovelace", "lovelace.lanbat-cameras", "lovelace.lanbat-system"):
        expect(f"{name} is a lovelace store with its key", a[name]["key"] == name and "views" in a[name]["data"]["config"])
    views = {v["path"]: v for v in a["lovelace.lovelace"]["data"]["config"]["views"]}
    expect("the Overview has Home, Media, Climate, Garden and Energy",
           list(views) == ["home", "media", "climate", "garden", "energy"])
    home = views["home"]
    headings = [s["cards"][0]["heading"] for s in home["sections"]]
    expect("Home has a section per room with devices, in name order, then the rest",
           headings == ["Home", "Garden", "Living Room", "Office", "Not in a room yet"])
    living = home["sections"][headings.index("Living Room")]
    expect("a room's bulb dims, its light switch and plug toggle",
           any(c.get("entity") == "light.lounge_spot" and c.get("features") == [{"type": "light-brightness"}]
               for c in living["cards"])
           and any(c.get("entity") == "switch.lounge_light" and c.get("icon") == "mdi:lightbulb" for c in living["cards"])
           and any(c.get("entity") == "switch.lounge_heater" and c.get("icon") == "mdi:power-socket-uk"
                   for c in living["cards"]))
    expect("the room's lights-off badge turns off just its lights",
           json.dumps(living["cards"][0]).count("homeassistant.turn_off") == 1
           and "switch.lounge_heater" not in json.dumps(living["cards"][0]["badges"]))
    every = set()
    for store_ in (a["lovelace.lovelace"], a["lovelace.lanbat-cameras"], a["lovelace.lanbat-system"]):
        every |= entities_in(store_)
    expect("no disabled entity and no config number appears",
           "sensor.disabled_thing" not in every and "number.lounge_light_countdown" not in every)
    # A camera's "anything" occupancy is left out: its badges name each object.
    expect("every primary entity is somewhere",
           {e for e, *_ in ENTITIES if _[4] is None and e not in DISABLED} - every
           == {"binary_sensor.c1_all_occupancy"})
    expect("the satellite's own reply player is not a music player",
           "media_player.office_satellite" not in json.dumps(home)
           and "media_player.office_speaker" in json.dumps(views["media"]))
    expect("the plug's energy feeds the Energy dashboard with its power as the rate",
           a["energy-devices.json"] == [{"name": "lounge_heater", "stat_consumption": "sensor.lounge_heater_energy",
                                         "stat_rate": "sensor.lounge_heater_power"}])
    cams = a["lovelace.lanbat-cameras"]["data"]["config"]["views"][0]
    expect("the camera is live, with its switches and last snapshot",
           '"camera_view": "live"' in json.dumps(cams) and "switch.c1_detect" in json.dumps(cams)
           and "image.c1_person" in json.dumps(cams))
    garden = json.dumps(home["sections"][headings.index("Garden")])
    expect("a camera in a room shows there live, without the rest of Frigate",
           '"camera.c1"' in garden and "image.c1_person" not in garden)
    expect("a Frigate zone gets its counts", "sensor.driveway_car_active_count" in json.dumps(cams))
    system = json.dumps(a["lovelace.lanbat-system"])
    expect("System shows the Zigbee bridge, updates, batteries and the links",
           "switch.zigbee2mqtt_bridge_permit_join" in system and "update.button_remote" in system
           and "sensor.button_remote_battery" in system and "https://grafana.example.com" in system)
    expect("System has a Follow-me section: pause and guests switches, each room's occupied sensor, its rules' log",
           '"Follow-me"' in system and "input_boolean.followme_paused" in system
           and "input_boolean.followme_guests" in system and "binary_sensor.followme_office_occupied" in system
           and "automation.follow_me_lights_on_office" in system)
    plain = os.path.join(tmp, "plain")
    write_fixture(plain)
    reg = json.load(open(os.path.join(plain, "core.entity_registry")))
    reg["data"]["entities"] = [x for x in reg["data"]["entities"]
                               if "followme" not in x["entity_id"] and "follow_me" not in x["entity_id"]]
    json.dump(reg, open(os.path.join(plain, "core.entity_registry"), "w"))
    p_ = run(plain, os.path.join(tmp, "p"))
    expect("a home without follow-me has no Follow-me section",
           "Follow-me" not in json.dumps(p_["lovelace.lanbat-system"]))
    expect("the System dashboard alone is for admins; the Overview is listed as lovelace",
           [(d["id"], d["require_admin"]) for d in a["dashboards.json"]]
           == [("lanbat-cameras", False), ("lanbat-system", True), ("lovelace", False)])
    expect("every card has a type",
           all(isinstance(c.get("type"), str) for _, c in walk(a["lovelace.lovelace"]["data"]["config"])))
    # A room with a screen (an Echo Show on TECHO5): its own dashboard, a view
    # per person, with only cards the device draws.
    screens = os.path.join(tmp, "screens")
    write_fixture(screens)
    reg = json.load(open(os.path.join(screens, "core.entity_registry")))
    # The room's lamp, and two device lights that are not the room's: the
    # Echo Show's own screen and a robot's LED.
    for eid, platform in (("light.office_lamp", "mqtt"), ("light.office_satellite_screen", "esphome"),
                          ("light.lanbat_stackchan_office_light", "mqtt")):
        reg["data"]["entities"].append({
            "entity_id": eid, "platform": platform, "device_id": "sat", "device_class": None,
            "original_device_class": None, "unit_of_measurement": None, "entity_category": None,
            "area_id": None, "disabled_by": None, "hidden_by": None, "unique_id": eid})
    for key in ("alex", "sam"):
        reg["data"]["entities"].append({
            "entity_id": f"sensor.lanbat_people_where_{key}", "platform": "mqtt", "device_id": None,
            "device_class": None, "original_device_class": None, "unit_of_measurement": None,
            "entity_category": None, "area_id": None, "disabled_by": None, "hidden_by": None,
            "unique_id": f"lanbat_people_where_{key}"})
    json.dump(reg, open(os.path.join(screens, "core.entity_registry"), "w"))
    out = os.path.join(tmp, "s")
    subprocess.run([GEN, "--storage", screens, "--out", out,
                    "--screens", json.dumps([{"room": "Office", "path": "lanbat-screen-office"}]),
                    "--people", json.dumps({"alex": "Alex Example", "sam": "Sam"})], check=True)
    s_ = {n: json.load(open(os.path.join(out, n))) for n in sorted(os.listdir(out))}
    screen = s_.get("lovelace.lanbat-screen-office", {}).get("data", {}).get("config", {})
    paths = [v["path"] for v in screen.get("views", [])]
    expect("a screen's dashboard has a home view and one per person", paths == ["home", "alex", "sam"])
    alex = json.dumps(screen["views"][1]) if len(paths) == 3 else ""
    expect("a person's view greets them and shows where the others are, not themselves",
           '"Hi Alex"' in alex and "sensor.lanbat_people_where_sam" in alex
           and "sensor.lanbat_people_where_alex" not in alex)
    expect("the screen shows its room's devices", "sensor.office_temperature" in json.dumps(screen))
    offs = [c for _, c in walk(screen) if c.get("name") == "Lights off"]
    expect("the screen's lights-off button turns off the room's lamp, not the screen or the robot",
           offs and all(c["tap_action"]["target"]["entity_id"] == ["light.office_lamp"] for c in offs))
    expect("the screen's lights-off button asks nothing (TECHO5 can't, and then does nothing)",
           offs and all("confirmation" not in c["tap_action"] for c in offs))
    drawn = {"heading", "tile", "button", "entities", "glance", "markdown", "picture-entity", "grid",
             "vertical-stack", "horizontal-stack", "conditional", "gauge", "history-graph", "sensor"}
    kinds = {c["type"] for _, c in walk(screen)} - {"sections", "grid"}
    expect("the screen uses only cards the device draws", kinds <= drawn)
    expect("the screen's dashboard is listed, out of the sidebar",
           {"id": "lanbat-screen-office", "url_path": "lanbat-screen-office", "title": "Office screen",
            "icon": "mdi:tablet-dashboard", "mode": "storage", "require_admin": False, "show_in_sidebar": False}
           in s_["dashboards.json"])
    # No room at all: still a valid, small dashboard.
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty)
    e = run(empty, os.path.join(tmp, "e"))
    expect("an empty Home Assistant gets a Home view and nothing breaks",
           [v["path"] for v in e["lovelace.lovelace"]["data"]["config"]["views"]] == ["home"])

sys.exit(1 if failures else 0)
