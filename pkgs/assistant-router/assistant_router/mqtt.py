"""The robots, and who is around, in Home Assistant (MQTT discovery).

The router is connected to every robot's bridge already (body.py), and is
where who-is-speaking is worked out (people.py), so it is the one place that
publishes them, with one broker login on the server:

- a device per robot (per room with a body): its battery, link, mood, whether
  someone is in front of it, napping or night, its touches as events, and a
  tag scanner (Home Assistant's own Tags: an NFC tag tapped on it); and
  controls: gestures, sleep and wake, mood, the screen's brightness, a caption,
  and its LEDs as a notification light;
- in each robot's room, "probably" who spoke last and who is nearby, labelled
  as guesses (never for anything that needs to be sure);
- a "People" device: who is home (wifi-presence).

Topics, under PREFIX: stackchan/<room>/state (retained JSON), .../led,
.../event/touch, .../availability, .../cmd/<name>; people/state; and the
router's own availability, its MQTT will. Discovery configs go under
homeassistant/<component>/lanbat_stackchan_<room>/<object>/config, and again
whenever Home Assistant says it came online (homeassistant/status).

Commands are checked against fixed lists before they reach a body; MQTT is
never a way to hand a robot Wi-Fi details or phone keys.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

from .body import GESTURE_EVENTS, MOODS, PHASES, Bodies

LOOKS = ("track", "user", "up", "center")
from .people import People

LOG = logging.getLogger("assistant-router")

PREFIX = "lanbat"
DISCOVERY = "homeassistant"
ROUTER_AVAILABILITY = f"{PREFIX}/router/availability"
BUTTONS = ("nod", "shake", "tilt", "wiggle", "dance", "look_around")
TICK_SECONDS = 2.0

Publish = Callable[[str, str, bool], Awaitable[None]]


def slug(room: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (room or "").casefold()).strip("_") or "room"


def _base(room: str) -> str:
    return f"{PREFIX}/stackchan/{slug(room)}"


def robot_discovery(room: str, fw: str | None = None) -> list[tuple[str, dict]]:
    """(topic, config) for every entity of the robot in `room`."""
    s, base = slug(room), _base(room)
    uid = f"lanbat_stackchan_{s}"
    device = {"identifiers": [uid], "name": f"Stack-chan {room}", "manufacturer": "M5Stack",
              "model": "StackChan", "suggested_area": room}
    if fw:
        device["sw_version"] = fw
    common = {"device": device, "availability_mode": "all",
              "availability": [{"topic": ROUTER_AVAILABILITY}, {"topic": f"{base}/availability"}]}
    state = f"{base}/state"

    def entity(component: str, obj: str, name: str, **fields) -> tuple[str, dict]:
        return (f"{DISCOVERY}/{component}/{uid}/{obj}/config",
                {**common, "name": name, "unique_id": f"{uid}_{obj}", "object_id": f"{uid}_{obj}", **fields})

    out = [
        entity("sensor", "battery", "Battery", state_topic=state, device_class="battery",
               unit_of_measurement="%", state_class="measurement", value_template="{{ value_json.battery }}"),
        entity("sensor", "link", "Link", state_topic=state, icon="mdi:lan-connect",
               value_template="{{ value_json.link }}", entity_category="diagnostic"),
        entity("sensor", "mood", "Mood", state_topic=state, icon="mdi:emoticon-outline",
               value_template="{{ value_json.mood }}"),
        entity("binary_sensor", "someone", "Someone in front", state_topic=state, device_class="occupancy",
               value_template="{{ 'ON' if value_json.present else 'OFF' }}"),
        entity("binary_sensor", "charging", "Charging", state_topic=state, device_class="battery_charging",
               value_template="{{ 'ON' if value_json.charging else 'OFF' }}"),
        entity("binary_sensor", "battery_low", "Battery low", state_topic=state, device_class="battery",
               value_template="{{ 'ON' if value_json.battery_low else 'OFF' }}"),
        entity("binary_sensor", "napping", "Napping", state_topic=state, icon="mdi:sleep",
               value_template="{{ 'ON' if value_json.nap else 'OFF' }}"),
        entity("binary_sensor", "night", "Asleep for the night", state_topic=state, icon="mdi:weather-night",
               value_template="{{ 'ON' if value_json.night else 'OFF' }}"),
        entity("event", "touch", "Touch", state_topic=f"{base}/event/touch", event_types=["tap", "stroke"],
               icon="mdi:hand-back-right"),
        entity("event", "wave", "Wave", state_topic=f"{base}/event/wave", event_types=["wave"],
               icon="mdi:hand-wave"),
        # Behind the CoreS3's dark glass a lit room reads a few lux: relative.
        entity("sensor", "illuminance", "Light at the sensor", state_topic=state, device_class="illuminance",
               unit_of_measurement="lx", state_class="measurement", value_template="{{ value_json.lux }}",
               entity_category="diagnostic"),
        entity("binary_sensor", "hand_near", "Hand near", state_topic=state, icon="mdi:hand-back-left",
               value_template="{{ 'ON' if value_json.near else 'OFF' }}"),
        entity("select", "mood_select", "Set mood", command_topic=f"{base}/cmd/mood", options=list(MOODS),
               state_topic=state, value_template="{{ value_json.mood if value_json.mood in "
                                                   + json.dumps(list(MOODS)) + " else 'neutral' }}"),
        entity("number", "brightness", "Screen brightness", command_topic=f"{base}/cmd/brightness",
               state_topic=state, value_template="{{ value_json.brightness }}", min=0, max=255, step=5,
               icon="mdi:brightness-6", entity_category="config"),
        entity("text", "caption", "Caption", command_topic=f"{base}/cmd/caption", max=200,
               icon="mdi:message-text-outline"),
        entity("light", "light", "Light", schema="json", command_topic=f"{base}/cmd/led",
               state_topic=f"{base}/led", supported_color_modes=["rgb"]),
        entity("button", "sleep", "Go to sleep", command_topic=f"{base}/cmd/sleep", payload_press="ON",
               icon="mdi:sleep"),
        entity("button", "wake", "Wake up", command_topic=f"{base}/cmd/sleep", payload_press="OFF",
               icon="mdi:alarm"),
        # Who is talking to it: guesses, labelled as such (docs/people.md).
        entity("sensor", "speaker", "Probably speaking", state_topic=f"{base}/identity",
               value_template="{{ value_json.speaker }}", json_attributes_topic=f"{base}/identity",
               json_attributes_template="{{ {'posterior': value_json.posterior, 'how': value_json.how} | tojson }}",
               icon="mdi:account-voice"),
        entity("sensor", "nearby", "Probably nearby", state_topic=f"{base}/identity",
               value_template="{{ value_json.nearby }}", icon="mdi:account-group"),
        # What each recognition source says, behind the guesses above.
        *[entity("sensor", f"seen_{source}", name, state_topic=f"{base}/evidence",
                 value_template="{{ value_json." + source + ".names }}",
                 json_attributes_topic=f"{base}/evidence",
                 json_attributes_template="{{ value_json." + source + ".confidence | tojson }}",
                 icon=icon, entity_category="diagnostic")
          for source, name, icon in SOURCES],
        # What it is doing.
        entity("sensor", "phase", "Conversation", state_topic=state, device_class="enum",
               options=list(PHASES), value_template="{{ value_json.phase }}", icon="mdi:chat-processing"),
        entity("sensor", "voice_dir", "Voice direction", state_topic=state, unit_of_measurement="°",
               value_template="{{ value_json.voice_dir }}", icon="mdi:account-voice",
               entity_category="diagnostic"),
        entity("sensor", "head_yaw", "Head direction", state_topic=state, unit_of_measurement="°",
               value_template="{{ value_json.head_yaw }}", icon="mdi:head-sync-outline",
               entity_category="diagnostic"),
        # How it is.
        entity("sensor", "rssi", "Wi-Fi signal", state_topic=state, device_class="signal_strength",
               unit_of_measurement="dBm", state_class="measurement", value_template="{{ value_json.rssi }}",
               entity_category="diagnostic"),
        entity("sensor", "heap", "Free memory", state_topic=state, unit_of_measurement="kB",
               state_class="measurement", value_template="{{ value_json.heap_kb }}", icon="mdi:memory",
               entity_category="diagnostic"),
        entity("sensor", "psram", "Free PSRAM", state_topic=state, unit_of_measurement="kB",
               state_class="measurement", value_template="{{ value_json.psram_kb }}", icon="mdi:memory",
               entity_category="diagnostic"),
        entity("sensor", "nfc_mode", "NFC mode", state_topic=state, value_template="{{ value_json.nfc_mode }}",
               icon="mdi:nfc-variant", entity_category="diagnostic"),
        entity("sensor", "bridge_error", "Last problem", state_topic=state,
               value_template="{{ value_json.bridge_error }}", icon="mdi:alert-circle-outline",
               entity_category="diagnostic"),
        entity("binary_sensor", "pi_undervolt", "Pi under-voltage", state_topic=state, device_class="problem",
               value_template="{{ 'ON' if value_json.pi_undervolt else 'OFF' }}", entity_category="diagnostic"),
        # What it did.
        entity("event", "wake_word", "Wake word", state_topic=f"{base}/event/wake", event_types=["wake"],
               icon="mdi:account-voice"),
        entity("event", "gesture", "Gesture", state_topic=f"{base}/event/gesture", event_types=list(GESTURE_EVENTS),
               icon="mdi:robot-happy"),
        entity("event", "face", "Face", state_topic=f"{base}/event/face", event_types=["new", "lost"],
               icon="mdi:face-recognition"),
        entity("event", "phone_tap", "Phone tap", state_topic=f"{base}/event/phone_tap", event_types=["tap"],
               icon="mdi:cellphone-nfc"),
        entity("event", "card", "Card", state_topic=f"{base}/event/card",
               event_types=["waiting", "learned", "forgotten"], icon="mdi:card-account-details"),
        entity("event", "servo_fault", "Servo fault", state_topic=f"{base}/event/servo_fault",
               event_types=["fault"], icon="mdi:alert", entity_category="diagnostic"),
        # More to tell it.
        entity("select", "look", "Look", command_topic=f"{base}/cmd/look", options=list(LOOKS),
               icon="mdi:eye"),
        entity("number", "turn", "Head turn", command_topic=f"{base}/cmd/turn", min=-70, max=70, step=5,
               unit_of_measurement="°", icon="mdi:head-sync-outline", mode="slider"),
        entity("button", "nap", "Nap now", command_topic=f"{base}/cmd/nap", payload_press="ON", icon="mdi:sleep"),
        entity("button", "listen", "Start listening", command_topic=f"{base}/cmd/listen", payload_press="start",
               icon="mdi:microphone"),
        entity("button", "stop", "Stop reply", command_topic=f"{base}/cmd/listen", payload_press="stop",
               icon="mdi:stop"),
        entity("button", "read_card", "Read a card", command_topic=f"{base}/cmd/read_card", payload_press="ON",
               icon="mdi:nfc-search-variant"),
    ]
    # Home Assistant's tag scanner: a tapped tag's UID fires tag_scanned.
    out.append((f"{DISCOVERY}/tag/{uid}/nfc/config", {"topic": f"{base}/tag", "device": device}))
    for gesture in BUTTONS:
        out.append(entity("button", gesture, gesture.replace("_", " ").capitalize(),
                          command_topic=f"{base}/cmd/gesture", payload_press=gesture, icon="mdi:robot-happy"))
    return out


def people_discovery() -> list[tuple[str, dict]]:
    uid = "lanbat_people"
    return [(f"{DISCOVERY}/sensor/{uid}/home/config", {
        "name": "Probably home", "unique_id": f"{uid}_home", "object_id": f"{uid}_home",
        "state_topic": f"{PREFIX}/people/state", "value_template": "{{ value_json.home }}",
        "json_attributes_topic": f"{PREFIX}/people/state",
        "json_attributes_template": "{{ {'people': value_json.people} | tojson }}",
        "icon": "mdi:home-account",
        "availability": [{"topic": ROUTER_AVAILABILITY}],
        "device": {"identifiers": [uid], "name": "People", "manufacturer": "lanbat",
                   "model": "Who is around (a guess)"},
    })]


def robot_state(bodies: Bodies, room: str) -> dict | None:
    s = bodies.state(room)
    if s is None:
        return None
    return {"present": s.present, "battery": s.battery, "charging": s.charging, "battery_low": s.battery_low,
            "link": s.link, "mood": s.mood, "nap": s.nap, "night": s.night, "brightness": s.brightness,
            "fw": s.fw, "lux": s.lux, "near": s.near, "phase": s.phase or "idle",
            "voice_dir": s.voice_dir, "head_yaw": s.head_yaw, "rssi": s.rssi, "heap_kb": s.heap_kb,
            "psram_kb": s.psram_kb, "nfc_mode": s.nfc_mode, "pi_undervolt": s.pi_undervolt,
            "bridge_error": s.bridge_error or "none"}


def _names(people: People, keys) -> str:
    names = sorted(people.names[k] for k in keys if k in people.names)
    return ", ".join(names) if names else "nobody"


SOURCES = (
    ("camera", "Seen by the cameras", "mdi:cctv"),
    ("robot", "Seen by the robot", "mdi:robot"),
    ("voice", "Heard by voice", "mdi:account-voice"),
    ("bluetooth", "Phones here (Bluetooth)", "mdi:bluetooth"),
    ("wifi", "Phones here (Wi-Fi)", "mdi:wifi"),
    ("card", "Tapped (phone or card)", "mdi:cellphone-nfc"),
)


def evidence(people: People, room: str, now: float) -> dict:
    """Per recognition source: the names it gives for the room, and their
    confidences as attributes."""
    found = people.evidence(room, now)
    return {source: {"names": _names(people, found.get(source, {})),
                     "confidence": {people.names[k]: round(v, 2) for k, v in found.get(source, {}).items()
                                    if k in people.names}}
            for source, _, _ in SOURCES}


def identity(people: People, room: str, now: float) -> dict:
    speaker, posterior, how = people.last_speaker(room, now)
    nearby = people.assess(room, now).nearby
    return {"speaker": people.names[speaker] if speaker else "unknown",
            "posterior": round(posterior, 2) if speaker else None, "how": how or None,
            "nearby": _names(people, nearby)}


def _event_payload(event: dict) -> tuple[str, dict] | None:
    """(topic, Home Assistant event payload) for the robot's and router's own
    events; None for touches, waves and tags, which have their own."""
    if event.get("wake") is True:
        return "wake", {"event_type": "wake"}
    if event.get("tapped") is True:
        return "phone_tap", {"event_type": "tap"}
    if "gesture" in event:
        return "gesture", {"event_type": event["gesture"]}
    if "face" in event:
        return "face", {"event_type": event["face"]}
    if "servo_fault" in event:
        return "servo_fault", {"event_type": "fault", "servo": event["servo_fault"]}
    if "card" in event:
        return "card", {"event_type": event["card"], "person": event.get("person")}
    return None


def command(name: str, payload: str) -> dict | None:
    """A Home Assistant command as a body act; None when it isn't one."""
    text = (payload or "").strip()
    if name == "gesture" and text in BUTTONS:
        return {"gesture": text}
    if name == "sleep" and text in ("ON", "OFF"):
        return {"sleep": text == "ON"}
    if name == "mood" and text in MOODS:
        return {"mood": text}
    if name == "brightness":
        try:
            value = int(float(text))
        except ValueError:
            return None
        return {"brightness": value} if 0 <= value <= 255 else None
    if name == "caption" and text:
        return {"caption": text[:200]}
    if name == "look" and text in LOOKS:
        return {"look": text}
    if name == "turn":
        try:
            value = int(round(float(text)))
        except ValueError:
            return None
        return {"yaw": value} if -70 <= value <= 70 else None
    if name == "nap" and text == "ON":
        return {"nap": True}
    if name == "listen" and text in ("start", "stop"):
        return {"listen": text}
    if name == "led":
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("state") not in ("ON", "OFF"):
            return None
        if data["state"] == "OFF":
            return {"led": None}
        colour = data.get("color") if isinstance(data.get("color"), dict) else {"r": 255, "g": 255, "b": 255}
        rgb = [colour.get(c) for c in ("r", "g", "b")]
        if not all(type(c) is int and 0 <= c <= 255 for c in rgb):
            return None
        return {"led": {"rgb": rgb}}
    return None


class HomeAssistant:
    """Keeps Home Assistant's view of the robots and people current; the MQTT
    connection itself is the caller's (run_mqtt), behind `publish`."""

    def __init__(self, bodies: Bodies, people: People, clock: Callable[[], float]) -> None:
        self.bodies = bodies
        self.people = people
        self.clock = clock
        self._published: dict[str, str] = {}  # topic -> payload, to send only changes
        self._discovered: dict[str, str | None] = {}  # room -> the firmware it was announced with
        self._leds: dict[str, dict] = {}  # room -> the light's state, as last commanded
        self._people_announced = False
        bodies.events = asyncio.Queue(maxsize=64)

    async def _send(self, publish: Publish, topic: str, payload: Any, retain: bool = True,
                    force: bool = False) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
        if not force and self._published.get(topic) == text:
            return
        self._published[topic] = text
        await publish(topic, text, retain)

    def forget(self) -> None:
        """Home Assistant restarted, or the broker connection is new: send it all again."""
        self._published.clear()
        self._discovered.clear()
        self._people_announced = False

    async def tick(self, publish: Publish) -> None:
        now = self.clock()
        rooms = self.bodies.rooms()
        if not self._people_announced:
            for topic, config in people_discovery():
                await self._send(publish, topic, config)
            self._people_announced = True
        home = sorted(self.people.home(now))
        await self._send(publish, f"{PREFIX}/people/state",
                         {"home": _names(self.people, home), "people": home})
        for room in rooms:
            state = robot_state(self.bodies, room)
            if state is None:
                continue
            if room not in self._discovered or self._discovered[room] != state["fw"]:
                for topic, config in robot_discovery(room, state["fw"]):
                    await self._send(publish, topic, config)
                self._discovered[room] = state["fw"]
            await self._send(publish, f"{_base(room)}/availability", "online")
            await self._send(publish, f"{_base(room)}/state", state)
            await self._send(publish, f"{_base(room)}/identity", identity(self.people, room, now))
            await self._send(publish, f"{_base(room)}/evidence", evidence(self.people, room, now))
            if room not in self._leds:
                self._leds[room] = {"state": "OFF", "color_mode": "rgb"}
            await self._send(publish, f"{_base(room)}/led", self._leds[room])
        for room in [r for r in self._discovered if r not in rooms]:
            await self._send(publish, f"{_base(room)}/availability", "offline")
            del self._discovered[room]

    async def events(self, publish: Publish) -> None:
        while True:
            room, event = await self.bodies.events.get()
            if "nfc" in event:
                await self._send(publish, f"{_base(room)}/tag", event["nfc"], retain=False, force=True)
                continue
            named = _event_payload(event)
            if named is not None:
                topic, payload = named
                await self._send(publish, f"{_base(room)}/event/{topic}", payload, retain=False, force=True)
                continue
            if event["touch"] == "wave":
                await self._send(publish, f"{_base(room)}/event/wave", {"event_type": "wave"},
                                 retain=False, force=True)
                continue
            await self._send(publish, f"{_base(room)}/event/touch",
                             {"event_type": event["touch"], "zone": event["zone"]}, retain=False, force=True)

    async def handle(self, topic: str, payload: str, publish: Publish) -> None:
        if topic == f"{DISCOVERY}/status":
            if payload == "online":
                self.forget()
                await self.tick(publish)
            return
        parts = topic.split("/")
        if len(parts) != 5 or parts[0] != PREFIX or parts[1] != "stackchan" or parts[3] != "cmd":
            return
        room = next((r for r in self.bodies.rooms() if slug(r) == parts[2]), None)
        if room is not None and parts[4] == "read_card" and payload.strip() == "ON":
            # Cards for Home Assistant's tag scanner, while the robot is a tag for phones.
            await self.bodies.send(room, {"nfc_read": 30})
            return
        act = command(parts[4], payload)
        if room is None or act is None:
            return
        if await self.bodies.act(room, act) and "led" in act:
            led = act["led"]
            self._leds[room] = ({"state": "ON", "color_mode": "rgb",
                                 "color": dict(zip("rgb", led["rgb"]))} if led else
                                {"state": "OFF", "color_mode": "rgb"})
            await self._send(publish, f"{_base(room)}/led", self._leds[room])


async def run_mqtt(ha: HomeAssistant, host: str, port: int, username: str | None, password: str | None) -> None:
    """Connected to the broker for as long as the router runs, reconnecting."""
    import aiomqtt

    delay = 1
    while True:
        try:
            will = aiomqtt.Will(ROUTER_AVAILABILITY, "offline", retain=True)
            async with aiomqtt.Client(host, port, username=username, password=password, will=will,
                                      identifier="lanbat-assistant-router") as client:
                delay = 1

                async def publish(topic: str, payload: str, retain: bool) -> None:
                    await client.publish(topic, payload, retain=retain)

                ha.forget()
                await publish(ROUTER_AVAILABILITY, "online", True)
                await client.subscribe(f"{DISCOVERY}/status")
                await client.subscribe(f"{PREFIX}/stackchan/+/cmd/+")
                LOG.info(json.dumps({"mqtt": "connected", "host": host}))

                async def ticks() -> None:
                    while True:
                        await ha.tick(publish)
                        await asyncio.sleep(TICK_SECONDS)

                tasks = [asyncio.create_task(ticks()), asyncio.create_task(ha.events(publish))]
                try:
                    async for message in client.messages:
                        payload = message.payload.decode("utf-8", "replace") \
                            if isinstance(message.payload, (bytes, bytearray)) else str(message.payload)
                        await ha.handle(str(message.topic), payload, publish)
                finally:
                    for t in tasks:
                        t.cancel()
        except aiomqtt.MqttError as err:
            LOG.warning(json.dumps({"mqtt": "unreachable", "error": str(err)}))
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)
