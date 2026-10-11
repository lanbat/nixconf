"""Bodies: a robot face on a satellite (pkgs/lva-stackchan), by room.

A body connects over a WebSocket and tells the router what it senses; the
router tells it what to show. Both ways the messages are fixed fields with
values from fixed lists, so nothing a body sends ever becomes free text in a
prompt:

    body -> router  {"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}}
                    {"state": {"present": true, "present_since_s": 130, "asleep": false, "battery_low": false}}
                    {"face_image": "<base64>"}   a face it was asked to capture
                    {"face_image": null}          it couldn't get one
    router -> body  {"act": {"mood": "happy", "gesture": "nod", "look": "user", "sleep": false}}
                    {"capture": true}             send me a face of whoever is in front

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
GESTURES = ("nod", "shake", "tilt", "wiggle", "dance", "look_around", "look_at_user")

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

# Commands to the body itself, matched on the whole request, and what is said back.
_COMMANDS: list[tuple[re.Pattern[str], dict[str, Any], str]] = [
    (re.compile(_POLITE + r"nod(?: your head)?(?: please)?$"), {"gesture": "nod"}, "Yes!"),
    (re.compile(_POLITE + r"shake your head(?: please)?$"), {"gesture": "shake"}, "Nope!"),
    (re.compile(_POLITE + r"look at me(?: please)?$"), {"look": "user"}, "I see you!"),
    (re.compile(_POLITE + r"look around(?: please)?$"), {"gesture": "look_around"}, "Ooh, what's out there?"),
    (re.compile(_POLITE + r"(?:do (?:a |a little |your |some )?dance|dance)(?: for me| for us)?(?: please)?$"),
     {"mood": "excited", "gesture": "dance"}, "Here I go!"),
    (re.compile(_POLITE + r"(?:go to sleep|go to bed|time to sleep|sleep now)(?: please)?$"),
     {"sleep": True}, "Goodnight!"),
    (re.compile(_POLITE + r"wake up(?: please)?$"), {"sleep": False}, "Good morning! I'm up!"),
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


def _key(room: str) -> str:
    return (room or "").strip().casefold()


class Bodies:
    """The connected bodies, one per room."""

    def __init__(self, persona: str) -> None:
        self.persona = persona.strip()
        self._sockets: dict[str, Any] = {}
        self._states: dict[str, BodyState] = {}
        self._captures: dict[str, Any] = {}  # room key -> a face in flight (enrolment)

    def connect(self, room: str, socket: Any) -> None:
        self._sockets[_key(room)] = socket
        self._states[_key(room)] = BodyState()

    def disconnect(self, room: str, socket: Any) -> None:
        if self._sockets.get(_key(room)) is socket:
            del self._sockets[_key(room)]
            self._states.pop(_key(room), None)
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

    async def act(self, room: str, act: dict[str, Any]) -> bool:
        socket = self._sockets.get(_key(room))
        if socket is None or not act:
            return False
        try:
            await socket.send_json({"act": act})
        except Exception:  # pylint: disable=broad-except
            return False
        return True

    async def capture(self, room: str, timeout: float = CAPTURE_TIMEOUT_S) -> bytes | None:
        """Ask the room's body for a face and wait for it. None when there is
        no body, a capture is already in flight, or no face arrives in time."""
        key = _key(room)
        socket = self._sockets.get(key)
        if socket is None or key in self._captures:
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
        return "\n".join([
            self.persona,
            seen,
            "Your face shows a mood and your head moves. Begin every reply with a tag the listener "
            "won't hear: [mood] or [mood gesture], where mood is one of " + ", ".join(MOODS)
            + " and gesture one of " + ", ".join(GESTURES) + ". Example: [excited nod] Good morning!",
        ])
