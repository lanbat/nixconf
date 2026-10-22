"""Bodies: a robot face on a satellite (pkgs/lva-stackchan), or a screen
(pkgs/techo5-bridge), by room.

A body connects over a WebSocket and tells the router what it senses; the
router tells it what to show. Both ways the messages are fixed fields with
values from fixed lists, so nothing a body sends ever becomes free text in a
prompt:

    body -> router  {"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}}
                    {"hello": {"room": "Kitchen", "kind": "screen", "proto": 1,
                               "can": ["mood", "caption", "greet", "capture", "phones", "here"]}}
                    {"state": {"present": true, "present_since_s": 130, "asleep": false, "battery_low": false,
                               "night": false, "nap": false, "battery": 87, "charging": false, "link": "usb",
                               "mood": "neutral", "brightness": 180, "fw": "1.5.0"}}
                    {"event": {"touch": "tap|stroke", "zone": "front|middle|back|screen|null"}}
                    {"event": {"wave": true}}      a hand waved in front of its screen
                    {"event": {"nfc": "<UID hex>"}}  a tag or card tapped on it
                    {"event": {"tapped": true}}   a phone read its NFC link (the page says who: tap.py)
                    {"face_image": "<base64>"}   a face it was asked to capture
                    {"face_image": null}          it couldn't get one
    router -> body  {"act": {"mood": "happy", "gesture": "nod", "look": "user", "sleep": false}}
                    {"tap": {"url": "https://who.<domain>/tap/<room>?n=..."}}
                                                  the link a phone held to it opens, every minute (tap.py)
                    {"act": {"caption": "...", "brightness": 0-255, "led": {"rgb": [r, g, b]} | null}}
                                                  (from Home Assistant, assistant_router/mqtt.py)
                    {"capture": true}             send me a face of whoever is in front
                    {"here": [{"person": "<key>", "name": "...", "confidence": 0.9}]}
                                                  who is in its room, on each change (bodies that can "here")

A hello without "can" is a robot, which can do everything but "here". A body
that names what it can do (CAPABILITIES) is sent only acts it can show, is
asked for a face only with "capture", is handed the phones' keys only with
"phones", and is told who is here only with "here".

A face comes over only when the room's body is asked for one (enrolment); the
bytes go straight to Frigate's face library and never into a prompt, so the
fixed-fields rule holds.

For a request from a room with a body, the cloud model gets the persona and
what the body senses after Home Assistant's context block, and starts its
reply with a tag, [mood] or [mood gesture], which is taken off before the
reply is spoken and sent to the body. A few body commands ("nod", "dance",
"go to sleep") are answered here, without a model.
"""
from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass
from typing import Any

MOODS = ("happy", "excited", "sad", "curious", "surprised", "thinking", "sleepy", "neutral")
PHASES = ("idle", "listening", "thinking", "speaking")
# Why a robot last started (its hello, through the bridge): the first four mean a crash or a power dip.
RESTARTS = ("panic", "brownout", "task-watchdog", "interrupt-watchdog", "watchdog", "power-on", "software",
            "external", "deep-sleep", "sdio", "other")
# Gestures the robot plays (the bridge's list), for Home Assistant's gesture event.
GESTURE_EVENTS = ("perk", "nod", "shake", "wiggle", "tilt", "dance", "look_around")
GESTURES = ("nod", "shake", "tilt", "wiggle", "dance", "look_around", "look_at_user")

# What a body may say it can do in its hello ("can"). A robot, which says
# nothing, can do all of these but "here".
CAPABILITIES = ("mood", "gesture", "look", "sleep", "caption", "greet", "capture", "phones", "here")
ROBOT_CAN = frozenset(CAPABILITIES) - {"here"}
# An act's fields and the capability each needs; the rest (brightness, led,
# yaw, nap, ...) are a robot's own.
ACT_NEEDS = {"mood": "mood", "gesture": "gesture", "look": "look", "sleep": "sleep", "caption": "caption",
             "greet": "greet"}

# How long to wait for a body's face before a capture is reported as failed.
CAPTURE_TIMEOUT_S = 10.0

_TAG = re.compile(r"^\s*\[([A-Za-z_]+)(?:\s+([A-Za-z_]+))?\]\s*")


def split_tag(text: str) -> tuple[str | None, str | None, str]:
    """A reply's leading [mood gesture] tag, checked against the lists, and
    the reply without it. Only a leading tag counts."""
    m = _TAG.match(text or "")
    if not m:
        return None, None, text
    mood = m.group(1).lower()
    gesture = (m.group(2) or "").lower()
    return (mood if mood in MOODS else None,
            gesture if mood in MOODS and gesture in GESTURES else None,
            text[m.end():])


_POLITE = r"^(?:(?:hey |okay |ok )?(?:nabu )?)?(?:can you |could you |would you |will you |please |now )*"

# What people add around a command ("nod for me please", "goodnight Nabu").
_TAIL = r"(?: (?:for me|for us|please|now|right now|again|nabu))*$"

# Commands to the body itself, matched on the whole request (with the polite
# words around it), and what is said back.
_COMMANDS: list[tuple[re.Pattern[str], dict[str, Any], str]] = [
    (re.compile(_POLITE + r"nod(?: your head)?" + _TAIL), {"gesture": "nod"}, "Yes!"),
    (re.compile(_POLITE + r"shake(?: your head)?" + _TAIL), {"gesture": "shake"}, "Nope!"),
    (re.compile(_POLITE + r"look at me" + _TAIL), {"look": "user"}, "I see you!"),
    (re.compile(_POLITE + r"look around" + _TAIL), {"gesture": "look_around"}, "Ooh, what's out there?"),
    (re.compile(_POLITE + r"(?:do (?:a |a little |your |some )?dance|dance)" + _TAIL),
     {"mood": "excited", "gesture": "dance"}, "Here I go!"),
    (re.compile(_POLITE + r"(?:go to sleep|go to bed|time to sleep|time for bed|sleep|take a nap|nap|"
                r"good ?night)" + _TAIL),
     {"sleep": True}, "Goodnight!"),
    (re.compile(_POLITE + r"wake up" + _TAIL), {"sleep": False}, "Good morning! I'm up!"),
]


def command(text: str) -> tuple[dict[str, Any], str] | None:
    """The act and the reply for a body command, or None for anything else."""
    said = re.sub(r"[^a-z' ]+", " ", (text or "").lower())
    said = re.sub(r"\s+", " ", said).strip()
    for pattern, act, reply in _COMMANDS:
        if pattern.match(said):
            return dict(act), reply
    return None


@dataclass
class BodyState:
    present: bool = False
    since: float | None = None  # when the person in front arrived (router clock)
    asleep: bool = False
    battery_low: bool = False
    ear: bool = False  # sends its own recording of each sentence (voice_sample)
    # For Home Assistant (assistant_router/mqtt.py); None until the body says.
    night: bool = False
    nap: bool = False
    battery: int | None = None
    charging: bool | None = None
    link: str | None = None
    mood: str | None = None
    brightness: int | None = None
    fw: str | None = None
    restart: str | None = None  # why it last started (its bridge says): "panic", "brownout", "power-on", ...
    lux: int | None = None  # light at its sensor: relative (behind dark glass)
    near: bool = False  # a hand close to its screen
    phase: str | None = None  # idle, listening, thinking, speaking
    voice_dir: int | None = None  # the last voice's direction, degrees, + to its left
    head_yaw: int | None = None
    rssi: int | None = None  # its Wi-Fi signal, dBm
    heap_kb: int | None = None
    psram_kb: int | None = None
    nfc_mode: str | None = None  # tag (for phones) or reader (for cards)
    pi_undervolt: bool | None = None  # its Pi's supply sagging
    bridge_error: str | None = None  # the bridge's last problem, short
    sound_mirror: bool = False  # the voice's direction mirrored (its bridge's setting)
    auto_turn: bool = True  # the turn to a voice learned from what the robot then sees
    turn_cal: str | None = None  # "learning (3/6)", "calibrated", "calibrated, mirrored"
    turn_gain: float | None = None
    turn_offset: float | None = None
    turn_rms: float | None = None
    turn_samples: int | None = None


def _key(room: str) -> str:
    return (room or "").strip().casefold()


def normalize_items_payload(raw: dict) -> dict[str, Any]:
    """Sanitize an items message from the techo5 bridge (shared with mqtt.py)."""
    person = raw.get("person")
    if not isinstance(person, str) or not person.strip():
        person = None
    else:
        person = person.strip()[:64]
    reminders = raw.get("reminders") if isinstance(raw.get("reminders"), list) else []
    todos = raw.get("todos") if isinstance(raw.get("todos"), list) else []
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    more = raw.get("more") if type(raw.get("more")) is int and raw["more"] >= 0 else 0
    clean_reminders = []
    for row in reminders[:5]:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str):
            continue
        rem = {"title": row["title"][:200]}
        for field in ("uid", "list", "due"):
            if isinstance(row.get(field), str):
                rem[field] = row[field][:200]
        clean_reminders.append(rem)
    clean_todos = [{"title": row["title"][:200]} for row in todos[:5]
                   if isinstance(row, dict) and isinstance(row.get("title"), str)]
    clean_events = []
    for row in events[:3]:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str):
            continue
        ev = {"title": row["title"][:200], "all_day": row.get("all_day") is True}
        if isinstance(row.get("start"), str):
            ev["start"] = row["start"][:64]
        clean_events.append(ev)
    return {"person": person, "reminders": clean_reminders, "todos": clean_todos,
            "more": more, "events": clean_events}


def parse_can(value: Any) -> frozenset[str] | None:
    """A hello's "can": the known capabilities it names, or None (a robot)
    when it names none."""
    if not isinstance(value, list):
        return None
    return frozenset(c for c in value if isinstance(c, str) and c in CAPABILITIES)


class Bodies:
    """The connected bodies, one per room."""

    def __init__(self, persona: str) -> None:
        self.persona = persona.strip()
        self._sockets: dict[str, Any] = {}
        self._kinds: dict[str, str] = {}  # room key -> the body's kind ("stackchan", "screen")
        self._can: dict[str, frozenset[str] | None] = {}  # room key -> what it can do; None: a robot
        self._states: dict[str, BodyState] = {}
        self._captures: dict[str, Any] = {}  # room key -> a face in flight (enrolment)
        self._names: dict[str, str] = {}  # room key -> the room as the body named it
        self._face_checks: dict[str, dict[str, Any]] = {}  # room key -> its last face check
        self._screen_items: dict[str, dict[str, Any]] = {}  # room key -> last items from a screen body
        self.events: asyncio.Queue | None = None  # (room, event) for Home Assistant, when it listens

    def connect(self, room: str, socket: Any, kind: str = "stackchan", can: Any = None) -> None:
        self._sockets[_key(room)] = socket
        self._states[_key(room)] = BodyState()
        self._names[_key(room)] = room.strip()
        self._kinds[_key(room)] = kind if isinstance(kind, str) and re.fullmatch(r"[a-z0-9-]{1,32}", kind) \
            else "stackchan"
        self._can[_key(room)] = parse_can(can)

    def kind(self, room: str) -> str | None:
        return self._kinds.get(_key(room)) if self.has(room) else None

    def is_robot(self, room: str) -> bool:
        """A body that named nothing it can do: a robot, with all its controls."""
        return self.has(room) and self._can.get(_key(room)) is None

    def can(self, room: str, what: str) -> bool:
        if not self.has(room):
            return False
        can = self._can.get(_key(room))
        return what in (ROBOT_CAN if can is None else can)

    def doable(self, room: str, act: dict[str, Any]) -> dict[str, Any]:
        """The part of `act` the room's body can show."""
        if self.is_robot(room):
            return dict(act)
        return {k: v for k, v in act.items() if k in ACT_NEEDS and self.can(room, ACT_NEEDS[k])}

    def rooms(self) -> list[str]:
        """The rooms with a body, as the bodies named them."""
        return [self._names.get(k, k) for k in self._sockets]

    def event(self, room: str, event: Any) -> None:
        """Something the body felt (a touch, a wave, a tag), for Home
        Assistant: fixed fields."""
        if self.events is None or not self.has(room) or not isinstance(event, dict):
            return
        touch, zone = event.get("touch"), event.get("zone")
        tag = event.get("nfc")
        if isinstance(tag, str) and re.fullmatch(r"[0-9A-F]{8,20}", tag):
            try:
                self.events.put_nowait((self._names.get(_key(room), room), {"nfc": tag}))
            except asyncio.QueueFull:
                pass
            return
        # What it did, for Home Assistant's event entities: fixed fields only.
        for kind, ok in (("wake", event.get("wake") is True),
                         ("tapped", event.get("tapped") is True),
                         ("gesture", event.get("gesture") in GESTURE_EVENTS),
                         ("face", event.get("face") in ("new", "lost")),
                         ("servo_fault", type(event.get("servo_fault")) is int and 0 <= event["servo_fault"] < 64)):
            if ok:
                try:
                    self.events.put_nowait((self._names.get(_key(room), room), {kind: event[kind]}))
                except asyncio.QueueFull:
                    pass
                return
        if event.get("wave") is True:
            touch, zone = "wave", None
        if touch in ("tap", "stroke", "wave"):
            try:
                self.events.put_nowait((self._names.get(_key(room), room), {
                    "touch": touch, "zone": zone if zone in ("front", "middle", "back", "screen") else None}))
            except asyncio.QueueFull:
                pass  # Home Assistant is not keeping up: drop the touch

    def announce(self, room: str, event: dict[str, Any]) -> None:
        """Something the router itself did in a body's room (a card learned),
        for Home Assistant."""
        if self.events is None or not self.has(room):
            return
        try:
            self.events.put_nowait((self._names.get(_key(room), room), event))
        except asyncio.QueueFull:
            pass

    def note_face_check(self, room: str, check: dict[str, Any]) -> None:
        """The answer to the body's last face probe (server.face_check), for
        Home Assistant's "Last face check"."""
        if self.has(room):
            self._face_checks[_key(room)] = dict(check)

    def face_check(self, room: str) -> dict[str, Any] | None:
        return self._face_checks.get(_key(room))

    def receive_items(self, room: str, items: Any, *, people_here: int,
                      sole_person: str | None = None) -> None:
        """The techo5 bridge's reminders, to-dos and events for the room's
        screen; kept only from a screen while exactly one person is here and
        the message names that person (or is a household/events-only clear)."""
        if not self.has(room) or self.kind(room) != "screen" or people_here != 1:
            return
        if not isinstance(items, dict):
            return
        parsed = normalize_items_payload(items)
        msg_person = parsed["person"]
        if msg_person is not None and msg_person != sole_person:
            return
        self._screen_items[_key(room)] = parsed

    def reconcile_screen_items(self, room: str, here: dict[str, tuple[float, list[str]]]) -> None:
        """Drop stored items when occupancy is not exactly the person they belong to."""
        key = _key(room)
        stored = self._screen_items.get(key)
        if stored is None:
            return
        if len(here) != 1:
            del self._screen_items[key]
            return
        sole = next(iter(here))
        person = stored.get("person")
        if person is not None and person != sole:
            del self._screen_items[key]

    def screen_items(self, room: str) -> dict[str, Any] | None:
        """The last items a screen in `room` sent; None when it sent none yet."""
        if not self.has(room) or self.kind(room) != "screen":
            return None
        return self._screen_items.get(_key(room))

    def disconnect(self, room: str, socket: Any) -> None:
        if self._sockets.get(_key(room)) is socket:
            del self._sockets[_key(room)]
            self._states.pop(_key(room), None)
            self._kinds.pop(_key(room), None)
            self._can.pop(_key(room), None)
            self._screen_items.pop(_key(room), None)
        fut = self._captures.pop(_key(room), None)
        if fut is not None and not fut.done():
            fut.set_result(None)  # the body went away: no face is coming

    def has(self, room: str) -> bool:
        return bool(room) and _key(room) in self._sockets

    def state(self, room: str) -> BodyState | None:
        return self._states.get(_key(room))

    def update(self, room: str, fields: dict[str, Any], now: float) -> None:
        state = self._states.get(_key(room))
        if state is None or not isinstance(fields, dict):
            return
        present = fields.get("present")
        if type(present) is bool:
            if present and not state.present:
                since = fields.get("present_since_s")
                ago = float(since) if type(since) in (int, float) and 0 <= since < 86400 else 0.0
                state.since = now - ago
            state.present = present
            if not present:
                state.since = None
        asleep = fields.get("asleep")
        if type(asleep) is bool:
            state.asleep = asleep
        battery_low = fields.get("battery_low")
        if type(battery_low) is bool:
            state.battery_low = battery_low
        ear = fields.get("ear")
        if type(ear) is bool:
            state.ear = ear
        for name in ("night", "nap", "charging"):
            if type(fields.get(name)) is bool:
                setattr(state, name, fields[name])
        if type(fields.get("battery")) is int and 0 <= fields["battery"] <= 100:
            state.battery = fields["battery"]
        if fields.get("link") in ("usb", "wifi"):
            state.link = fields["link"]
        if fields.get("mood") in MOODS + ("listening", "confused"):
            state.mood = fields["mood"]
        if type(fields.get("brightness")) is int and 0 <= fields["brightness"] <= 255:
            state.brightness = fields["brightness"]
        if type(fields.get("lux")) is int and 0 <= fields["lux"] <= 100000:
            state.lux = fields["lux"]
        if type(fields.get("near")) is bool:
            state.near = fields["near"]
        if isinstance(fields.get("fw"), str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,16}", fields["fw"]):
            state.fw = fields["fw"]
        if fields.get("restart") in RESTARTS:
            state.restart = fields["restart"]
        if fields.get("phase") in PHASES:
            state.phase = fields["phase"]
        for name, lo, hi in (("voice_dir", -180, 180), ("head_yaw", -90, 90), ("rssi", -127, 0),
                             ("heap_kb", 0, 1 << 20), ("psram_kb", 0, 1 << 20)):
            if name in fields:
                value = fields[name]
                setattr(state, name, value if type(value) is int and lo <= value <= hi else None)
        if "nfc_mode" in fields:
            state.nfc_mode = fields["nfc_mode"] if fields["nfc_mode"] in ("tag", "reader") else None
        if type(fields.get("sound_mirror")) is bool:
            state.sound_mirror = fields["sound_mirror"]
        if type(fields.get("auto_turn")) is bool:
            state.auto_turn = fields["auto_turn"]
        if "turn_cal" in fields:
            cal = fields["turn_cal"]
            state.turn_cal = cal if isinstance(cal, str) and re.fullmatch(
                r"learning \(\d/6\)|calibrated|calibrated, mirrored", cal) else None
        for name, lo, hi in (("turn_gain", -3.0, 3.0), ("turn_offset", -90.0, 90.0), ("turn_rms", 0.0, 90.0)):
            if name in fields:
                value = fields[name]
                setattr(state, name, float(value) if type(value) in (int, float) and lo <= value <= hi else None)
        if "turn_samples" in fields:
            n = fields["turn_samples"]
            state.turn_samples = n if type(n) is int and 0 <= n <= 1000 else None
        if "pi_undervolt" in fields:
            state.pi_undervolt = fields["pi_undervolt"] if type(fields["pi_undervolt"]) is bool else None
        if "bridge_error" in fields:
            # Diagnostics, never a prompt: printable, short.
            error = fields["bridge_error"]
            state.bridge_error = (re.sub(r"[^ -~]", "", error)[:80] or None) if isinstance(error, str) else None

    async def act(self, room: str, act: dict[str, Any]) -> bool:
        """Send what of `act` the body can show; False when that is nothing."""
        act = self.doable(room, act)
        if not act:
            return False
        return await self.send(room, {"act": act})

    async def send(self, room: str, msg: dict[str, Any]) -> bool:
        socket = self._sockets.get(_key(room))
        if socket is None:
            return False
        try:
            await socket.send_json(msg)
        except Exception:  # pylint: disable=broad-except
            return False
        return True

    async def capture(self, room: str, timeout: float = CAPTURE_TIMEOUT_S) -> bytes | None:
        """Ask the room's body for a face and wait for it. None when there is
        no body, a capture is already in flight, or no face arrives in time."""
        key = _key(room)
        socket = self._sockets.get(key)
        if socket is None or key in self._captures or not self.can(room, "capture"):
            return None
        fut = asyncio.get_running_loop().create_future()
        self._captures[key] = fut
        try:
            await socket.send_json({"capture": True})
        except Exception:  # pylint: disable=broad-except
            self._captures.pop(key, None)
            return None
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if self._captures.get(key) is fut:
                self._captures.pop(key, None)

    def complete_capture(self, room: str, image: str | None) -> None:
        """The body's answer to a capture: the base64 face, or None for a
        failure. Unknown or malformed input resolves as a failure, never raises."""
        fut = self._captures.get(_key(room))
        if fut is None or fut.done():
            return
        if image:
            try:
                fut.set_result(base64.b64decode(image, validate=True))
                return
            except (ValueError, TypeError):
                pass
        fut.set_result(None)

    def prompt_block(self, room: str, now: float) -> str | None:
        """The persona and what the body senses, for a cloud request from
        `room`; None when no body is there."""
        state = self.state(room)
        if state is None:
            return None
        if state.present:
            minutes = round((now - (state.since if state.since is not None else now)) / 60)
            seen = ("Someone is in front of you (just arrived)." if minutes < 1 else
                    f"Someone is in front of you (for about {minutes} minute{'s' if minutes != 1 else ''}).")
        else:
            seen = "Nobody is in front of you right now."
        if state.asleep:
            seen += " You were asleep until you were spoken to."
        if state.battery_low:
            seen += " Your battery is low: when it fits, ask to be charged."
        lines = [self.persona, seen]
        if self.can(room, "mood") and self.can(room, "gesture"):
            lines.append(
                "Your face shows a mood and your head moves. Begin every reply with a tag the listener "
                "won't hear: [mood] or [mood gesture], where mood is one of " + ", ".join(MOODS)
                + " and gesture one of " + ", ".join(GESTURES) + ". Example: [excited nod] Good morning!")
        elif self.can(room, "mood"):
            lines.append(
                "Your screen shows a mood. Begin every reply with a tag the listener won't hear: [mood], "
                "where mood is one of " + ", ".join(MOODS) + ". Example: [happy] Good morning!")
        return "\n".join(lines)
