import asyncio
import json

from assistant_router.body import Bodies
from assistant_router.mqtt import HomeAssistant, command, robot_discovery, slug
from assistant_router.people import Assessment, People


class Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class Broker:
    def __init__(self):
        self.messages = []

    async def publish(self, topic, payload, retain):
        self.messages.append((topic, payload, retain))

    def last(self, topic):
        found = [p for t, p, _ in self.messages if t == topic]
        return json.loads(found[-1]) if found and found[-1][:1] in "{[" else (found[-1] if found else None)

    def topics(self):
        return [t for t, _, _ in self.messages]


def setup():
    bodies = Bodies("You are Nabu.")
    people = People({"kiril": "Kiril", "maria": "Maria"})
    clock = [100.0]
    ha = HomeAssistant(bodies, people, lambda: clock[0])
    socket = Socket()
    bodies.connect("Living room", socket)
    bodies.update("Living room", {"present": True, "battery": 87, "charging": False, "link": "wifi",
                                  "mood": "happy", "nap": False, "night": False, "brightness": 180,
                                  "fw": "1.5.0"}, 100.0)
    return bodies, people, ha, socket, clock


def test_slugs():
    assert slug("Living room") == "living_room"
    assert slug("Bedroom 1!") == "bedroom_1"
    assert slug("") == "room"


def test_discovery_is_one_device_with_its_entities():
    configs = dict(robot_discovery("Living room", "1.5.0"))
    battery = configs["homeassistant/sensor/lanbat_stackchan_living_room/battery/config"]
    assert battery["device"]["identifiers"] == ["lanbat_stackchan_living_room"]
    assert battery["device"]["sw_version"] == "1.5.0" and battery["device"]["suggested_area"] == "Living room"
    assert battery["state_topic"] == "lanbat/stackchan/living_room/state"
    assert battery["availability_mode"] == "all"
    assert {a["topic"] for a in battery["availability"]} == {
        "lanbat/router/availability", "lanbat/stackchan/living_room/availability"}
    light = configs["homeassistant/light/lanbat_stackchan_living_room/light/config"]
    assert light["schema"] == "json" and light["supported_color_modes"] == ["rgb"]
    nod = configs["homeassistant/button/lanbat_stackchan_living_room/nod/config"]
    assert nod["command_topic"] == "lanbat/stackchan/living_room/cmd/gesture" and nod["payload_press"] == "nod"
    speaker = configs["homeassistant/sensor/lanbat_stackchan_living_room/speaker/config"]
    assert speaker["name"] == "Probably speaking"  # a guess, and says so
    ids = [c["unique_id"] for c in configs.values() if "unique_id" in c]  # a tag scanner has none
    assert len(ids) == len(set(ids))


def test_commands_are_checked():
    assert command("gesture", "nod") == {"gesture": "nod"}
    assert command("gesture", "explode") is None
    assert command("sleep", "ON") == {"sleep": True} and command("sleep", "OFF") == {"sleep": False}
    assert command("mood", "happy") == {"mood": "happy"} and command("mood", "angry") is None
    assert command("brightness", "120.0") == {"brightness": 120}
    assert command("brightness", "999") is None and command("brightness", "dim") is None
    assert command("caption", "  Dinner!  ") == {"caption": "Dinner!"}
    assert command("caption", "x" * 500) == {"caption": "x" * 200}
    assert command("led", '{"state": "ON", "color": {"r": 0, "g": 0, "b": 255}}') == {"led": {"rgb": [0, 0, 255]}}
    assert command("led", '{"state": "ON"}') == {"led": {"rgb": [255, 255, 255]}}
    assert command("led", '{"state": "OFF"}') == {"led": None}
    assert command("led", '{"state": "ON", "color": {"r": 300, "g": 0, "b": 0}}') is None
    assert command("led", "not json") is None
    assert command("wifi", '{"ssid": "x"}') is None  # never a way to provision a robot


async def test_a_tick_publishes_the_robot_and_who_is_around():
    bodies, people, ha, _, clock = setup()
    people.observe("Living room", "phone", "maria", 1.0, now=90.0)
    people.remember("Living room", Assessment(speaker="kiril", posterior=0.93, how="voice"), 95.0)
    people.observe_home(["kiril", "maria"], now=99.0)
    broker = Broker()
    await ha.tick(broker.publish)
    assert "homeassistant/sensor/lanbat_stackchan_living_room/battery/config" in broker.topics()
    state = broker.last("lanbat/stackchan/living_room/state")
    assert state["battery"] == 87 and state["link"] == "wifi" and state["present"] is True
    assert broker.last("lanbat/stackchan/living_room/availability") == "online"
    ident = broker.last("lanbat/stackchan/living_room/identity")
    assert ident == {"speaker": "Kiril", "posterior": 0.93, "how": "voice", "nearby": "Maria"}
    assert broker.last("lanbat/people/state") == {"home": "Kiril, Maria", "people": ["kiril", "maria"]}
    # Nothing changed: nothing sent again.
    before = len(broker.messages)
    await ha.tick(broker.publish)
    assert len(broker.messages) == before
    # A while later the speaker is forgotten.
    clock[0] = 100.0 + 400
    await ha.tick(broker.publish)
    assert broker.last("lanbat/stackchan/living_room/identity")["speaker"] == "unknown"


async def test_a_robot_that_leaves_is_unavailable():
    bodies, _, ha, socket, _ = setup()
    broker = Broker()
    await ha.tick(broker.publish)
    bodies.disconnect("Living room", socket)
    await ha.tick(broker.publish)
    assert broker.last("lanbat/stackchan/living_room/availability") == "offline"


async def test_commands_reach_the_body_and_the_light_reports_back():
    bodies, _, ha, socket, _ = setup()
    broker = Broker()
    await ha.tick(broker.publish)
    await ha.handle("lanbat/stackchan/living_room/cmd/gesture", "dance", broker.publish)
    await ha.handle("lanbat/stackchan/living_room/cmd/led",
                    '{"state": "ON", "color": {"r": 0, "g": 0, "b": 255}}', broker.publish)
    await ha.handle("lanbat/stackchan/living_room/cmd/gesture", "explode", broker.publish)  # dropped
    await ha.handle("lanbat/stackchan/kitchen/cmd/gesture", "nod", broker.publish)  # no such robot
    assert socket.sent == [{"act": {"gesture": "dance"}}, {"act": {"led": {"rgb": [0, 0, 255]}}}]
    assert broker.last("lanbat/stackchan/living_room/led") == {
        "state": "ON", "color_mode": "rgb", "color": {"r": 0, "g": 0, "b": 255}}


async def test_home_assistant_coming_back_gets_everything_again():
    _, _, ha, _, _ = setup()
    broker = Broker()
    await ha.tick(broker.publish)
    first = len(broker.messages)
    await ha.handle("homeassistant/status", "online", broker.publish)
    assert len(broker.messages) == 2 * first


async def test_touches_become_events():
    bodies, _, ha, _, _ = setup()
    broker = Broker()
    bodies.event("Living room", {"touch": "stroke", "zone": "back"})
    bodies.event("Living room", {"touch": "punch"})  # not a touch it knows
    bodies.event("Kitchen", {"touch": "tap"})  # no body there
    task = asyncio.create_task(ha.events(broker.publish))
    await asyncio.sleep(0.01)
    task.cancel()
    touches = [(t, json.loads(p), r) for t, p, r in broker.messages]
    assert touches == [("lanbat/stackchan/living_room/event/touch", {"event_type": "stroke", "zone": "back"}, False)]


async def test_a_wave_is_its_own_event_and_light_is_published():
    bodies, _, ha, _, _ = setup()
    bodies.update("Living room", {"lux": 37, "near": True}, 100.0)
    broker = Broker()
    await ha.tick(broker.publish)
    state = broker.last("lanbat/stackchan/living_room/state")
    assert state["lux"] == 37 and state["near"] is True
    bodies.event("Living room", {"wave": True})
    task = asyncio.create_task(ha.events(broker.publish))
    await asyncio.sleep(0.01)
    task.cancel()
    assert broker.last("lanbat/stackchan/living_room/event/wave") == {"event_type": "wave"}


async def test_a_tapped_tag_goes_to_home_assistants_tag_scanner():
    bodies, _, ha, _, _ = setup()
    configs = dict(robot_discovery("Living room"))
    assert configs["homeassistant/tag/lanbat_stackchan_living_room/nfc/config"]["topic"] == \
        "lanbat/stackchan/living_room/tag"
    broker = Broker()
    bodies.event("Living room", {"nfc": "0435249AD11290"})
    bodies.event("Living room", {"nfc": "not hex!"})
    task = asyncio.create_task(ha.events(broker.publish))
    await asyncio.sleep(0.01)
    task.cancel()
    assert broker.messages == [("lanbat/stackchan/living_room/tag", "0435249AD11290", False)]


async def test_what_it_does_and_how_it_is_reach_home_assistant():
    bodies, _, ha, _, _ = setup()
    bodies.update("Living room", {"phase": "speaking", "voice_dir": 35, "head_yaw": -10, "rssi": -58,
                                  "heap_kb": 92, "psram_kb": 6100, "nfc_mode": "tag", "pi_undervolt": True,
                                  "bridge_error": "router write failed\x07: closed"}, 100.0)
    broker = Broker()
    await ha.tick(broker.publish)
    s = broker.last("lanbat/stackchan/living_room/state")
    assert (s["phase"], s["voice_dir"], s["head_yaw"], s["rssi"], s["heap_kb"], s["nfc_mode"]) == \
        ("speaking", 35, -10, -58, 92, "tag")
    assert s["pi_undervolt"] is True and s["bridge_error"] == "router write failed: closed"
    # Out of range or unknown: dropped to unknown, never passed on.
    bodies.update("Living room", {"phase": "dancing", "rssi": 40, "nfc_mode": "card", "voice_dir": "left"}, 101.0)
    await ha.tick(broker.publish)
    s = broker.last("lanbat/stackchan/living_room/state")
    assert (s["phase"], s["rssi"], s["nfc_mode"], s["voice_dir"]) == ("speaking", None, None, None)


async def test_its_own_events_become_event_entities():
    bodies, _, ha, _, _ = setup()
    broker = Broker()
    for event in ({"wake": True}, {"gesture": "perk"}, {"face": "lost"}, {"tapped": True},
                  {"servo_fault": 3}, {"gesture": "explode"}, {"face": "maybe"}):
        bodies.event("Living room", event)
    bodies.announce("Living room", {"card": "learned", "person": "Kiril"})
    task = asyncio.create_task(ha.events(broker.publish))
    await asyncio.sleep(0.01)
    task.cancel()
    got = {t.rsplit("/", 1)[1]: json.loads(p) for t, p, _ in broker.messages}
    assert got == {"wake": {"event_type": "wake"}, "gesture": {"event_type": "perk"},
                   "face": {"event_type": "lost"}, "phone_tap": {"event_type": "tap"},
                   "servo_fault": {"event_type": "fault", "servo": 3},
                   "card": {"event_type": "learned", "person": "Kiril"}}
    configs = dict(robot_discovery("Living room"))
    gesture = configs["homeassistant/event/lanbat_stackchan_living_room/gesture/config"]
    assert "perk" in gesture["event_types"]


async def test_more_controls():
    assert command("look", "up") == {"look": "up"}
    assert command("look", "sideways") is None
    assert command("turn", "-30") == {"yaw": -30}
    assert command("turn", "90") is None
    assert command("nap", "ON") == {"nap": True}
    assert command("listen", "start") == {"listen": "start"}
    assert command("listen", "stop") == {"listen": "stop"}
    assert command("listen", "now") is None
    assert command("debug", "camera") == {"debug": "camera"} and command("debug", "off") == {"debug": "off"}
    assert command("debug", "phones") is None  # the others stay on the robot's serial port
    assert dict(robot_discovery("Living room"))[
        "homeassistant/button/lanbat_stackchan_living_room/camera_view/config"]["payload_press"] == "camera"
    bodies, _, ha, socket, _ = setup()
    broker = Broker()
    await ha.handle("lanbat/stackchan/living_room/cmd/read_card", "ON", broker.publish)
    await ha.handle("lanbat/stackchan/living_room/cmd/turn", "25", broker.publish)
    assert socket.sent == [{"nfc_read": 30}, {"act": {"yaw": 25}}]


async def test_each_recognition_source_is_a_sensor():
    bodies, people, ha, _, clock = setup()
    people.observe_phone("Living room", "maria", -55, clock[0], via="ble")
    people.note_voice("Living room", {"kiril": 0.75}, clock[0])
    broker = Broker()
    await ha.tick(broker.publish)
    e = broker.last("lanbat/stackchan/living_room/evidence")
    assert e["bluetooth"] == {"names": "Maria", "confidence": {"Maria": 1.0}}
    assert e["voice"] == {"names": "Kiril", "confidence": {"Kiril": 0.75}}
    assert e["camera"]["names"] == "nobody"
    configs = dict(robot_discovery("Living room"))
    voice = configs["homeassistant/sensor/lanbat_stackchan_living_room/seen_voice/config"]
    assert voice["name"] == "Heard by voice" and voice["state_topic"].endswith("/evidence")


async def test_who_is_here_and_where_everyone_is():
    _, people, ha, _, clock = setup()
    people.observe("Living room", "tap", "kiril", 1.0, clock[0])
    people.note_voice("Living room", {"kiril": 0.8, "maria": 0.1}, clock[0])
    people.observe_home(["maria"], clock[0])
    broker = Broker()
    await ha.tick(broker.publish)
    assert broker.last("lanbat/stackchan/living_room/here") == {
        "names": "Kiril", "people": {"Kiril": {"confidence": 1.0, "sources": ["card", "voice"]}}}
    assert broker.last("lanbat/people/kiril/where") == {
        "where": "Living room", "confidence": 1.0, "sources": ["card", "voice"]}
    assert broker.last("lanbat/people/maria/where") == {"where": "home", "confidence": 0.2, "sources": ["home"]}
    config = broker.last("homeassistant/sensor/lanbat_people/where_kiril/config")
    assert config["name"] == "Where is Kiril" and config["state_topic"] == "lanbat/people/kiril/where"
    assert config["device"]["name"] == "People"
    here = dict(robot_discovery("Living room"))["homeassistant/sensor/lanbat_stackchan_living_room/here/config"]
    assert here["name"] == "Probably here" and here["state_topic"] == "lanbat/stackchan/living_room/here"
    # Renamed (in Authentik), and someone forgotten: the entities follow.
    people.add("kiril", "Kiki")
    people.remove("maria")
    await ha.tick(broker.publish)
    assert broker.last("homeassistant/sensor/lanbat_people/where_kiril/config")["name"] == "Where is Kiki"

    def raw(topic):
        return [p for t, p, _ in broker.messages if t == topic][-1]
    assert raw("homeassistant/sensor/lanbat_people/where_maria/config") == ""  # removed from HA
    assert raw("lanbat/people/maria/where") == ""


async def test_why_a_robot_last_restarted_is_a_diagnostic():
    bodies, _, ha, _, _ = setup()
    bodies.update("Living room", {"restart": "brownout"}, 100.0)
    bodies.update("Living room", {"restart": "melted"}, 100.0)  # not a reason it knows
    broker = Broker()
    await ha.tick(broker.publish)
    assert broker.last("lanbat/stackchan/living_room/state")["restart"] == "brownout"
    config = dict(robot_discovery("Living room"))["homeassistant/sensor/lanbat_stackchan_living_room/restart/config"]
    assert config["name"] == "Last restart" and config["entity_category"] == "diagnostic"


async def test_a_greeting_is_a_recognized_event():
    bodies, _, ha, _, _ = setup()
    broker = Broker()
    bodies.announce("Living room", {"recognized": "Kiril", "how": ["card", "voice"]})
    task = asyncio.create_task(ha.events(broker.publish))
    for _ in range(50):
        if "lanbat/stackchan/living_room/event/recognized" in broker.topics():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert broker.last("lanbat/stackchan/living_room/event/recognized") == {
        "event_type": "recognized", "person": "Kiril", "how": ["card", "voice"]}
    config = dict(robot_discovery("Living room"))["homeassistant/event/lanbat_stackchan_living_room/recognized/config"]
    assert config["name"] == "Recognized" and config["event_types"] == ["recognized"]


async def test_the_mirror_switch():
    assert command("mirror", "ON") == {"mirror": True}
    assert command("mirror", "OFF") == {"mirror": False}
    assert command("mirror", "maybe") is None
    bodies, _, ha, socket, _ = setup()
    bodies.update("Living room", {"sound_mirror": True}, 100.0)
    broker = Broker()
    await ha.tick(broker.publish)
    assert broker.last("lanbat/stackchan/living_room/state")["sound_mirror"] is True
    switch = dict(robot_discovery("Living room"))["homeassistant/switch/lanbat_stackchan_living_room/mirror/config"]
    assert switch["command_topic"] == "lanbat/stackchan/living_room/cmd/mirror"


async def test_the_turn_calibration_in_home_assistant():
    assert command("auto_turn", "OFF") == {"auto_turn": False}
    assert command("reset_turn", "ON") == {"reset_turn": True}
    assert command("reset_turn", "maybe") is None
    bodies, _, ha, _, _ = setup()
    bodies.update("Living room", {"turn_cal": "calibrated, mirrored", "turn_gain": -2.31, "turn_offset": 7.9,
                                  "turn_rms": 4.2, "turn_samples": 9, "auto_turn": True}, 100.0)
    broker = Broker()
    await ha.tick(broker.publish)
    s = broker.last("lanbat/stackchan/living_room/state")
    assert (s["turn_cal"], s["turn_gain"], s["turn_samples"], s["auto_turn"]) == ("calibrated, mirrored", -2.31, 9, True)
    bodies.update("Living room", {"turn_cal": "anything; rm -rf", "turn_gain": 99}, 101.0)
    await ha.tick(broker.publish)
    s = broker.last("lanbat/stackchan/living_room/state")
    assert (s["turn_cal"], s["turn_gain"]) == ("unknown", None)
    configs = dict(robot_discovery("Living room"))
    assert "homeassistant/sensor/lanbat_stackchan_living_room/turn_cal/config" in configs
    assert "homeassistant/switch/lanbat_stackchan_living_room/auto_turn/config" in configs


def test_a_screen_gets_the_rooms_guesses_and_no_robot_controls():
    configs = dict(robot_discovery("Kitchen", None, robot=False))
    objects = {topic.split("/")[3] for topic in configs}
    assert {"someone", "speaker", "nearby", "here", "recognized", "seen_camera", "seen_bluetooth"} <= objects
    assert not objects & {"battery", "light", "nod", "turn", "nfc", "caption", "mood_select"}
    assert all("/lanbat_room_kitchen/" in topic for topic in configs)
    device = configs["homeassistant/sensor/lanbat_room_kitchen/here/config"]["device"]
    assert device["name"] == "Kitchen presence" and device["suggested_area"] == "Kitchen"


async def test_a_screen_is_published_without_its_light_and_ignores_robot_commands():
    bodies = Bodies("You are Nabu.")
    people = People({"kiril": "Kiril"})
    ha = HomeAssistant(bodies, people, lambda: 100.0)
    socket = Socket()
    bodies.connect("Kitchen", socket, "screen", ["mood", "greet", "here"])
    broker = Broker()
    await ha.tick(broker.publish)
    topics = broker.topics()
    assert "homeassistant/sensor/lanbat_room_kitchen/here/config" in topics
    assert not any("lanbat_stackchan_kitchen" in t for t in topics)
    assert "lanbat/stackchan/kitchen/here" in topics and "lanbat/stackchan/kitchen/led" not in topics
    await ha.handle("lanbat/stackchan/kitchen/cmd/mood", "happy", broker.publish)
    assert socket.sent == []
