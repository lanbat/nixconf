#!/usr/bin/env python3
"""A Stack-chan robot as the face of a Linux Voice Assistant satellite.

Follows LVA's peripheral WebSocket and talks to the robot over USB serial
(newline-delimited JSON, docs/stackchan.md). The robot's own firmware
(firmware/stackchan) does everything that has to be smooth or fast: the
face, blinking, looking at people it sees through its camera, the servos.
This decides what it should feel and say:

- A turn: listening when the wake word is heard, what you said as a caption,
  thinking while Home Assistant works, the reply as a caption with a mood read
  from it, the mouth following the reply's audio, then back to idle.
- Touch: a tap on the head starts a conversation (LVA's start_listening), or
  stops one; any touch silences a ringing timer; stroking it just makes it
  happy.
- Timers: shown as countdown rings, counted down here between LVA's events.
  LVA reports a cancelled timer only as an idle in the middle of a turn.
- Night: between NIGHT_START and NIGHT_END it sleeps, dimmed and still,
  waking for a conversation.
- The voice's direction: on the wake word it hears where the voice came from
  (the satellite's microphone array, not the mono LVA listens on) and turns
  to it, so the camera meets the speaker; with the router's face library a
  frame of the speaker is probed with it (docs/stackchan.md, docs/people.md).
- The assistant router (ROUTER_BODY_URL, pkgs/assistant-router body.py):
  the robot tells it whether someone is in front of it; it sends the mood
  and gesture the cloud model chose for a reply, and body commands ("nod",
  "dance", "go to sleep"). Its mood wins over the guess from the reply's
  words.

Brain holds the decisions and no I/O, so tests/stackchan-bridge.py drives it
with scripted events.
"""
from __future__ import annotations

import asyncio
import collections
import json
import math
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from operator import mul

PROTOCOL = 1

MOODS = ("neutral", "listening", "thinking", "happy", "excited", "sad", "surprised", "sleepy", "confused", "curious")
GESTURES = ("perk", "nod", "shake", "wiggle", "tilt", "dance", "look_around")
LOOKS = ("track", "user", "up", "center")
# How long the router's mood waits for the reply it was chosen for.
ROUTER_MOOD_SECONDS = 10

DEFAULT_SAD_WORDS = (
    "sorry",
    "can't",
    "cannot",
    "couldn't",
    "unable",
    "don't know",
    "not sure",
    "failed",
    "unfortunately",
    "error",
)

# The PlayStation Eye's four mics, metres from its centre (docs/stackchan.md).
DEFAULT_SOUND_GEOMETRY = "-0.01,-0.01;0.01,-0.01;0.01,0.01;-0.01,0.01"


def log(msg: str) -> None:
    print(f"lva-stackchan: {msg}", file=sys.stderr, flush=True)


def _minutes(hhmm: str) -> int | None:
    if not hhmm:
        return None
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _angle_moved(target: float, before: float) -> float:
    """Degrees between two directions, from 0 (same) to 180 (opposite)."""
    return abs((target - before + 180.0) % 360.0 - 180.0)


@dataclass
class Config:
    captions: bool = True
    caption_ms: int = 8000
    touch_to_talk: bool = True
    night_start: str = "23:00"
    night_end: str = "07:00"
    brightness: int = 180
    night_brightness: int = 10
    notice_guests: bool = True
    nap_after_s: int = 900  # nobody seen this long: the robot naps (0: never)
    sad_words: tuple[str, ...] = DEFAULT_SAD_WORDS
    sound_direction: bool = True  # the head turns to the voice (the satellite's mics)
    sound_offset_deg: float = 0.0  # the voice's angle as seen from the robot
    sound_turn_min_deg: float = 15.0  # the voice must move this far before a new turn
    sound_mic_geometry: str = DEFAULT_SOUND_GEOMETRY  # four "x,y" points in metres
    sound_source: str = "lanbat_ps_eye_capture"  # the microphone node to listen on

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        def flag(name: str, default: bool) -> bool:
            return env.get(name, "1" if default else "0") == "1"

        sad = env.get("SAD_WORDS")
        return cls(
            captions=flag("CAPTIONS", True),
            caption_ms=int(env.get("CAPTION_MS", "8000")),
            touch_to_talk=flag("TOUCH_TO_TALK", True),
            night_start=env.get("NIGHT_START", "23:00"),
            night_end=env.get("NIGHT_END", "07:00"),
            brightness=int(env.get("BRIGHTNESS", "180")),
            night_brightness=int(env.get("NIGHT_BRIGHTNESS", "10")),
            notice_guests=flag("NOTICE_GUESTS", True),
            nap_after_s=int(env.get("NAP_AFTER_S", "900")),
            sad_words=tuple(w.strip().lower() for w in sad.split(",") if w.strip()) if sad else DEFAULT_SAD_WORDS,
            sound_direction=flag("SOUND_DIRECTION", True),
            sound_offset_deg=float(env.get("SOUND_OFFSET_DEG", "0")),
            sound_turn_min_deg=float(env.get("SOUND_TURN_MIN_DEG", "15")),
            sound_mic_geometry=env.get("SOUND_MIC_GEOMETRY", DEFAULT_SOUND_GEOMETRY),
            sound_source=env.get("SOUND_SOURCE", "lanbat_ps_eye_capture"),
        )


@dataclass
class Timer:
    id: str
    name: str
    total: int
    ends_at: float
    ringing: bool = False


@dataclass
class Brain:
    """Decisions only. Every method returns a list of actions:

    ("device", {...})  a line for the robot
    ("lva", {...})     a command for LVA's peripheral API
    ("mouth", bool)    start or stop following the reply's audio
    ("log", str)       something worth a line in the journal
    """

    config: Config
    now: float
    in_turn: bool = False
    heard: bool = False  # the transcript of this turn has arrived
    replied: bool = False  # the reply of this turn has arrived
    mood: str = "neutral"
    muted: bool = False
    online: bool = True
    asleep: bool = False
    timers: dict[str, Timer] = field(default_factory=dict)
    night_seen: bool = False  # night or day at the last look: asleep follows its changes
    router_mood: str | None = None
    router_mood_at: float = 0.0
    present: bool = False
    present_at: float = 0.0
    napping: bool = False  # the robot's own nap (nobody around), not the night
    battery_low: bool = False
    sound_angle: float = 0.0  # the direction the head last turned to (degrees)

    def __post_init__(self) -> None:
        self.asleep = self.night_seen = self._is_night(self.now)

    # ── helpers ────────────────────────────────────────────────────────────
    def _is_night(self, now: float) -> bool:
        start, end = _minutes(self.config.night_start), _minutes(self.config.night_end)
        if start is None or end is None or start == end:
            return False
        t = time.localtime(now)
        minute = t.tm_hour * 60 + t.tm_min
        return start <= minute < end if start < end else minute >= start or minute < end

    def _mood(self, mood: str) -> tuple[str, dict]:
        self.mood = mood
        return ("device", {"mood": mood})

    def _caption(self, text: str, who: str) -> list:
        if not self.config.captions or not text:
            return []
        return [("device", {"caption": {"text": text, "who": who, "ms": self.config.caption_ms}})]

    def _status(self) -> tuple[str, dict]:
        return ("device", {"status": {"muted": self.muted, "online": self.online}})

    def router_state(self, now: float) -> dict:
        return {"present": self.present,
                "present_since_s": int(now - self.present_at) if self.present else 0,
                "asleep": self.asleep or self.napping,
                "battery_low": self.battery_low}

    def _sleep(self, asleep: bool) -> list:
        return [
            ("router", {"state": self.router_state(self.now)}),
            ("device", {"sleep": asleep}),
            (
                "device",
                {"config": {"brightness": self.config.night_brightness if asleep else self.config.brightness}},
            ),
        ]

    def timers_payload(self, now: float) -> list[dict]:
        return [
            {
                "id": t.id,
                "name": t.name,
                "remaining_s": max(0, round(t.ends_at - now)),
                "total_s": t.total,
                "ringing": t.ringing,
            }
            for t in sorted(self.timers.values(), key=lambda t: t.ends_at)
        ]

    def _timers(self, now: float) -> tuple[str, dict]:
        return ("device", {"timers": self.timers_payload(now)})

    def reply_mood(self, text: str) -> str:
        lowered = text.lower().replace("’", "'")
        if any(word in lowered for word in self.config.sad_words):
            return "sad"
        if lowered.rstrip().endswith("?"):
            return "curious"
        return "happy"

    def full_state(self, now: float) -> list:
        return [
            ("device", {"config": {
                "brightness": self.config.night_brightness if self.asleep else self.config.brightness,
                "notice": self.config.notice_guests,
                "nap_after_s": self.config.nap_after_s,
            }}),
            self._status(),
            ("device", {"sleep": self.asleep and not self.in_turn}),
            ("device", {"mood": self.mood}),
            ("device", {"look": "user" if self.in_turn else "track"}),
            self._timers(now),
        ]

    def _end_turn(self, now: float) -> list:
        self.in_turn = self.heard = self.replied = False
        out: list = [("mouth", False), ("device", {"mouth": 0}), self._mood("neutral"), ("device", {"look": "track"})]
        if self.asleep:
            out += self._sleep(True)
        return out

    # ── inputs ─────────────────────────────────────────────────────────────
    def on_lva(self, event: str, data: dict, now: float) -> list:
        self.now = now
        out: list = []
        if event in ("wake_word_detected", "listening"):
            if not self.in_turn:
                self.in_turn, self.heard, self.replied = True, False, False
                if self.asleep:
                    out += self._sleep(False)
                out += [("device", {"gesture": "perk"})] + self._caption("Listening…", "status")
            else:
                # Continued conversation: the microphone reopened after a question.
                self.heard = self.replied = False
            out += [("mouth", False), ("device", {"mouth": 0}), self._mood("listening"), ("device", {"look": "user"})]
        elif event == "stt_text":
            self.heard = True
            out += self._caption(data.get("text", ""), "user")
        elif event == "thinking":
            out += [self._mood("thinking"), ("device", {"look": "up"})]
        elif event == "tts_text":
            self.replied = True
            text = data.get("text", "")
            mood = self.reply_mood(text)
            if self.router_mood and now - self.router_mood_at <= ROUTER_MOOD_SECONDS:
                mood = self.router_mood
            self.router_mood = None
            out += self._caption(text, "assistant") + [self._mood(mood), ("device", {"look": "user"})]
            if mood == "curious":
                out.append(("device", {"gesture": "tilt"}))
        elif event == "tts_speaking":
            out.append(("mouth", True))
        elif event == "tts_finished":
            out += [("mouth", False), ("device", {"mouth": 0})]
        elif event == "idle":
            ringing = [t for t in self.timers.values() if t.ringing]
            if ringing:
                # The ringing was stopped (stop word, a touch, or HA).
                for t in ringing:
                    del self.timers[t.id]
                out.append(self._timers(now))
            if self.in_turn and self.heard and not self.replied:
                # A timer cancelled by voice: LVA says only "idle", between
                # the transcript and the reply. The turn goes on.
                if self.timers:
                    self.timers.clear()
                    out.append(self._timers(now))
                return out
            out += self._end_turn(now)
        elif event == "pipeline_error":
            out += [self._mood("confused"), ("device", {"gesture": "shake"})]
            out += self._caption("Sorry, I didn't catch that.", "status")
        elif event == "disconnected":
            self.online = False
            out += [self._status(), self._mood("confused")]
            out += self._caption("Offline", "status")
        elif event == "zeroconf" and data.get("status") == "connected":
            self.online = True
            out += [self._status(), self._mood("neutral")]
        elif event == "snapshot":
            self.muted = bool(data.get("muted", self.muted))
            self.online = bool(data.get("ha_connected", self.online))
            out.append(self._status())
        elif event == "muted":
            self.muted = bool(data.get("muted"))
            out.append(self._status())
        elif event in ("timer_ticking", "timer_updated", "timer_ringing"):
            tid = str(data.get("id", ""))
            self.timers[tid] = Timer(
                id=tid,
                name=str(data.get("name", "")),
                total=int(data.get("total_seconds", 0)),
                ends_at=now + int(data.get("seconds_left", 0)),
                ringing=event == "timer_ringing",
            )
            out.append(self._timers(now))
            if event == "timer_ringing":
                if self.asleep:
                    out += self._sleep(False)
                out += [self._mood("surprised"), ("device", {"gesture": "wiggle"})]
        return out

    def on_router(self, msg: dict, now: float) -> list:
        """A message from the assistant router: an act, or a face capture
        request; fields from fixed lists, else ignored."""
        if not isinstance(msg, dict):
            return []
        self.now = now
        if msg.get("capture"):
            return [("device", {"capture": True})]
        act = msg.get("act")
        if not isinstance(act, dict):
            return []
        out: list = []
        mood = act.get("mood")
        if mood in MOODS:
            self.router_mood, self.router_mood_at = mood, now
            out.append(self._mood(mood))
        gesture = act.get("gesture")
        if gesture == "look_at_user":
            out.append(("device", {"look": "user"}))
        elif gesture in GESTURES:
            out.append(("device", {"gesture": gesture}))
        if act.get("look") in LOOKS:
            out.append(("device", {"look": act["look"]}))
        sleep = act.get("sleep")
        if type(sleep) is bool:
            # Until night or day next changes (tick); in a turn, at its end.
            self.asleep = sleep
            if not self.in_turn:
                out += self._sleep(sleep) + [self._mood("sleepy" if sleep else "neutral")]
        return out

    def on_device(self, msg: dict, now: float) -> list:
        self.now = now
        out: list = []
        if "face_image" in msg:
            # A face the robot was asked to capture: to the router, verbatim
            # (base64, or null when it got none). It goes to Frigate's library.
            out.append(("router", {"face_image": msg.get("face_image")}))
        face = msg.get("face")
        if face in ("new", "lost") and (face == "new") != self.present:
            self.present = face == "new"
            self.present_at = now
            out.append(("router", {"state": self.router_state(now)}))
        battery = msg.get("battery")
        if isinstance(battery, dict) and type(battery.get("low")) is bool and battery["low"] != self.battery_low:
            self.battery_low = battery["low"]
            level = battery.get("level")
            out.append(("log", f"robot battery {'low' if self.battery_low else 'fine again'}"
                               + (f" ({level} %)" if type(level) is int else "")))
            out.append(("router", {"state": self.router_state(now)}))
        rest = msg.get("rest")
        if rest in ("nap", "awake") and (rest == "nap") != self.napping:
            self.napping = rest == "nap"
            out.append(("router", {"state": self.router_state(now)}))
        if "hello" in msg:
            hello = msg["hello"] or {}
            out.append(("log", f"robot firmware {hello.get('fw', '?')}, protocol {hello.get('proto', '?')}"))
            if hello.get("proto") != PROTOCOL:
                out.append(("log", f"the robot speaks protocol {hello.get('proto')}, this bridge {PROTOCOL}: reflash it (docs/stackchan.md)"))
            out += self.full_state(now)
        touch = msg.get("touch")
        if touch:
            if any(t.ringing for t in self.timers.values()):
                out.append(("lva", {"command": "stop_timer_ringing"}))
            elif touch == "tap" and self.config.touch_to_talk:
                out.append(("lva", {"command": "stop_pipeline" if self.in_turn else "start_listening"}))
            elif not self.in_turn:
                out += [self._mood("happy"), ("device", {"gesture": "nod"})]
        if msg.get("servo_fault") is not None:
            out.append(("log", f"servo {msg['servo_fault']} reported a fault"))
        if msg.get("log"):
            out.append(("log", f"robot: {msg['log']}"))
        return out

    def on_sound(self, angle: float, confidence: float, now: float) -> list:
        """The voice's direction (estimate_direction, from the satellite's
        microphones): the head turns to it so the camera meets the speaker.
        It moves only when the voice has moved on by sound_turn_min_deg: the
        estimate wobbles a few degrees between windows, and a turn is not a
        free gesture."""
        self.now = now
        target = angle + self.config.sound_offset_deg
        if _angle_moved(target, self.sound_angle) < self.config.sound_turn_min_deg:
            return []
        self.sound_angle = target
        yaw = int(round(max(-70.0, min(70.0, target)) * 10))
        return [
            ("device", {"yaw": yaw}),
            ("log", f"voice at {angle:.0f} degrees (confidence {confidence:.2f}): turning"),
        ]

    def tick(self, now: float) -> list:
        out: list = []
        self.now = now
        night = self._is_night(now)
        if night != self.night_seen:
            # Night falls or ends: that decides, whatever "go to sleep" said.
            self.night_seen = self.asleep = night
            if not self.in_turn:
                out += self._sleep(night)
                out.append(self._mood("sleepy" if night else "neutral"))
        # A timer that ended without ringing here (rang on another satellite,
        # or its events were missed) goes after a minute.
        stale = [t.id for t in self.timers.values() if not t.ringing and now - t.ends_at > 60]
        for tid in stale:
            del self.timers[tid]
        if self.timers or stale:
            out.append(self._timers(now))
        return out


# ── the reply's audio ──────────────────────────────────────────────────────
SAMPLE_RATE = 8000
CHUNK_BYTES = SAMPLE_RATE // 20 * 2  # 50 ms of 16-bit mono


def envelope_level(chunk: bytes, gain: float = 3.0, floor: float = 0.01) -> float:
    """How open the mouth is for 50 ms of signed 16-bit little-endian audio."""
    count = len(chunk) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", chunk[: count * 2])
    rms = math.sqrt(sum(s * s for s in samples) / count) / 32768
    if rms < floor:
        return 0.0
    return min(1.0, rms * gain)


async def follow_mouth(send) -> None:
    """Stream the default sink's monitor while a reply plays; cancelled when it ends."""
    proc = await asyncio.create_subprocess_exec(
        os.environ.get("PW_RECORD", "pw-record"),
        "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16",
        "-P", "{ stream.capture.sink = true node.name = lva-stackchan-mouth }",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    last = -1.0
    try:
        while True:
            chunk = await proc.stdout.readexactly(CHUNK_BYTES)
            level = round(envelope_level(chunk), 2)
            if abs(level - last) >= 0.08 or (level == 0 and last != 0):
                await send({"mouth": level})
                last = level
    except asyncio.IncompleteReadError:
        pass
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()


# ── the voice's direction ──────────────────────────────────────────────────
# The satellite's microphone array (the PlayStation Eye has four mics) hears a
# word a few microseconds apart. Where the mics sit is sound_mic_geometry:
# four "x,y" points in metres, y forward, x to the robot's right. The lead of
# a front mic over its rear partner scales with cos θ and the lead of the right
# mic over its left one with −sin θ (θ from straight ahead, positive to the
# robot's left, its yaw sign), so two short cross-correlations per axis and an
# atan2 give the angle. Pure Python on a 0.3 s window: a few milliseconds, run
# in a worker thread. The mics' channel order is a calibration step (docs/
# stackchan.md).
DOA_RATE = 48000  # the card's own rate: no resampling
DOA_CHANNELS = 4
DOA_WINDOW_S = 0.3
DOA_CHUNK = 256  # frames per read, one pass through the window's deques

CAPTURE_SETTLE_S = 1.2  # the head just turned: let it arrive before the frame
CAPTURE_TIMEOUT_S = 5.0  # the robot answers a capture with a face_image

_DOA_MIN_SAMPLES = 512
_DOA_CONFIDENCE_MIN = 0.25  # below this the mics do not agree on a direction
_DOA_MIN_BASELINE = 0.005  # 5 mm: an axis that short cannot be read


def parse_geometry(geometry: str) -> list[tuple[float, float]] | None:
    """Four "x,y" points in metres (y forward, x to the right), or None."""
    points = []
    for part in str(geometry).split(";"):
        xy = part.split(",")
        if len(xy) != 2:
            return None
        try:
            points.append((float(xy[0]), float(xy[1])))
        except ValueError:
            return None
    return points if len(points) == 4 else None


def _pair_lead(a: list[float], b: list[float], max_lag: int,
               energy_a: float, energy_b: float) -> tuple[float, float] | None:
    """(the samples by which mic b leads mic a, how sure 0..1), or None when
    there is no peak to read; the peak is rounded with its two neighbours."""
    n = min(len(a), len(b))
    base = math.sqrt(energy_a * energy_b)
    if base <= 0:
        return None
    scores = [0.0] * (2 * max_lag + 1)
    for k in range(-max_lag, max_lag + 1):
        lo, hi = max(0, k), n - max(0, -k)
        scores[k + max_lag] = sum(map(mul, a[lo:hi], b[lo - k:hi - k]))
    i = max(range(len(scores)), key=scores.__getitem__)
    if scores[i] <= 0:
        return None
    lead = float(i - max_lag)
    if 0 < i < len(scores) - 1:
        s0, s1, s2 = scores[i - 1], scores[i], scores[i + 1]
        curve = s0 - 2 * s1 + s2
        if curve != 0:
            lead += max(-0.5, min(0.5, 0.5 * (s0 - s2) / curve))
    return lead, min(1.0, scores[i] / base)


def estimate_direction(channels: list[list[float]], fs: int, geometry: str,
                       speed_of_sound: float = 343.0) -> tuple[float, float] | None:
    """(the voice's angle in the window, how sure 0..1), or None when the mics
    do not agree. channels: one list of samples per mic, four, equal length.
    The angle is degrees from straight ahead, positive to the robot's left,
    its yaw sign."""
    points = parse_geometry(geometry)
    if points is None or len(channels) != DOA_CHANNELS:
        return None
    if min(len(c) for c in channels) < _DOA_MIN_SAMPLES:
        return None
    by_x = sorted(range(4), key=lambda i: (points[i][0], points[i][1]))
    by_y = sorted(range(4), key=lambda i: (points[i][1], points[i][0]))
    y_pairs = [sorted(pair, key=lambda i: points[i][1]) for pair in (by_x[:2], by_x[2:])]  # (rear, front)
    x_pairs = [sorted(pair, key=lambda i: points[i][0]) for pair in (by_y[:2], by_y[2:])]  # (left, right)
    baselines = [abs(points[f][1] - points[r][1]) for r, f in y_pairs] + \
        [abs(points[r][0] - points[l][0]) for l, r in x_pairs]
    if min(baselines) < _DOA_MIN_BASELINE:
        return None
    max_lag = int(math.ceil(max(baselines) * fs / speed_of_sound)) + 2
    energy = [sum(map(mul, c, c)) for c in channels]
    leads = []
    for (i, j), base in zip(y_pairs + x_pairs, baselines):
        out = _pair_lead(channels[i], channels[j], max_lag, energy[i], energy[j])
        if out is None:
            return None
        leads.append(out)
    cos_part = sum(lead / base for lead, base in zip((l[0] for l in leads[:2]), baselines[:2])) / 2
    sin_part = sum(-lead / base for lead, base in zip((l[0] for l in leads[2:]), baselines[2:])) / 2
    confidence = sum(l[1] for l in leads) / len(leads)
    if confidence < _DOA_CONFIDENCE_MIN:
        return None
    return math.degrees(math.atan2(sin_part, cos_part)), confidence


class MicWindow:
    """The last DOA_WINDOW_S of each mic, from interleaved s16 frames."""

    def __init__(self, channels: int, frames: int) -> None:
        self.channels = [collections.deque(maxlen=frames) for _ in range(channels)]

    def push(self, raw: bytes) -> None:
        count, n = len(self.channels), len(raw) // (2 * len(self.channels))
        if n == 0:
            return
        values = struct.unpack(f"<{n * count}h", raw[: n * 2 * count])
        for frame in range(n):
            for ch in range(count):
                self.channels[ch].append(values[frame * count + ch] / 32768.0)

    def snapshot(self) -> list[list[float]] | None:
        if any(len(c) < c.maxlen for c in self.channels):
            return None
        return [list(c) for c in self.channels]


# ── I/O ────────────────────────────────────────────────────────────────────
class Bridge:
    def __init__(self, config: Config, device: str, lva_url: str, router_url: str = "", room: str = "") -> None:
        self.brain = Brain(config, now=time.time())
        self.device = device
        self.lva_url = lva_url
        self.router_url = router_url
        self.room = room
        self.writer: asyncio.StreamWriter | None = None
        self.ws = None
        self.router_ws = None
        self.mouth_task: asyncio.Task | None = None
        self.config = config
        self.mic_window: MicWindow | None = None
        self.router_faces = False  # the router has a face library to probe
        self.face_waiter: asyncio.Future | None = None  # a capture in flight
        self.capture_task: asyncio.Task | None = None

    async def send_device(self, line: dict) -> None:
        if self.writer is None:
            return
        try:
            self.writer.write(json.dumps(line, separators=(",", ":")).encode() + b"\n")
            await self.writer.drain()
        except (OSError, ConnectionError) as err:
            log(f"robot write failed: {err}")
            self.writer = None

    async def act(self, actions: list) -> None:
        for kind, value in actions:
            if kind == "device":
                await self.send_device(value)
            elif kind == "lva":
                if self.ws is not None:
                    await self.ws.send(json.dumps(value))
            elif kind == "mouth":
                if value and self.mouth_task is None:
                    self.mouth_task = asyncio.create_task(follow_mouth(self.send_device))
                elif not value and self.mouth_task is not None:
                    self.mouth_task.cancel()
                    self.mouth_task = None
            elif kind == "router":
                if self.router_ws is not None:
                    try:
                        await self.router_ws.send(json.dumps(value))
                    except Exception as err:  # pylint: disable=broad-except
                        log(f"router write failed: {err}")
            elif kind == "log":
                log(value)

    async def serial_loop(self) -> None:
        import serial_asyncio_fast

        while True:
            try:
                reader, self.writer = await serial_asyncio_fast.open_serial_connection(url=self.device, baudrate=115200)
                log(f"robot connected on {self.device}")
                await self.act(self.brain.full_state(time.time()))
                while True:
                    raw = await reader.readline()
                    if not raw:
                        break
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue  # boot noise from the ROM before the firmware speaks
                    if isinstance(msg, dict):
                        img = msg.get("face_image")
                        if isinstance(img, str) and self.face_waiter is not None:
                            waiter, self.face_waiter = self.face_waiter, None
                            if not waiter.done():
                                waiter.set_result(img)
                            continue  # the capture's answer, not a sensor event
                        await self.act(self.brain.on_device(msg, time.time()))
            except (OSError, ConnectionError) as err:
                if self.writer is not None:
                    log(f"robot gone: {err}")
            self.writer = None
            await asyncio.sleep(3)

    async def lva_loop(self) -> None:
        import websockets

        delay = 1
        while True:
            try:
                async with websockets.connect(self.lva_url) as ws:
                    self.ws, delay = ws, 1
                    log(f"following {self.lva_url}")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        event = msg.get("event")
                        if event:
                            await self.act(self.brain.on_lva(event, msg.get("data") or {}, time.time()))
                            # The voice was just heard: turn to it before the
                            # reply's audio drowns the microphone out.
                            if self.config.sound_direction and event in ("wake_word_detected", "listening"):
                                asyncio.create_task(self.hear_direction())
            except (OSError, websockets.exceptions.WebSocketException) as err:
                log(f"LVA unreachable ({err}); retrying")
            self.ws = None
            await self.act(self.brain.on_lva("disconnected", {}, time.time()))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def router_loop(self) -> None:
        """The assistant router (pkgs/assistant-router body.py): what the
        robot senses goes there, moods, gestures and body commands come back."""
        import websockets

        delay = 1
        while True:
            try:
                async with websockets.connect(self.router_url) as ws:
                    self.router_ws, delay = ws, 1
                    await ws.send(json.dumps({"hello": {"room": self.room, "kind": "stackchan", "proto": PROTOCOL}}))
                    await ws.send(json.dumps({"state": self.brain.router_state(time.time())}))
                    log(f"body of {self.room} at {self.router_url}")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(msg, dict):
                            if "body_hello" in msg:
                                self.router_faces = bool((msg["body_hello"] or {}).get("faces"))
                            elif "face_result" in msg:
                                result = msg["face_result"]
                                if isinstance(result, dict) and result.get("person"):
                                    conf = result.get("confidence")
                                    log(f"face probe: {result['person']}"
                                        + (f" ({conf:.2f})" if isinstance(conf, (int, float)) else ""))
                            else:
                                await self.act(self.brain.on_router(msg, time.time()))
            except (OSError, websockets.exceptions.WebSocketException) as err:
                log(f"router unreachable ({err}); retrying")
            self.router_ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def clock_loop(self) -> None:
        while True:
            await asyncio.sleep(3)
            await self.act(self.brain.tick(time.time()))
            # Heartbeat: the robot shows it has lost its Pi after 10 s without a line.
            await self.send_device({"ping": 1})

    async def doa_loop(self) -> None:
        """The rolling window behind hear_direction: the microphone node at
        its full channel count, not the mono LVA listens on."""
        self.mic_window = MicWindow(DOA_CHANNELS, int(DOA_WINDOW_S * DOA_RATE))
        delay = 3
        while True:
            proc: asyncio.subprocess.Process | None = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    os.environ.get("PW_RECORD", "pw-record"),
                    "--rate", str(DOA_RATE), "--channels", str(DOA_CHANNELS), "--format", "s16",
                    "-P", f"{{ node.name = lva-stackchan-doa target.object = {self.config.sound_source} }}",
                    "-",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                delay = 3
                log(f"hearing direction on {self.config.sound_source}")
                while True:
                    self.mic_window.push(await proc.stdout.readexactly(DOA_CHUNK * DOA_CHANNELS * 2))
            except (OSError, asyncio.IncompleteReadError) as err:
                log(f"direction listener stopped ({err}); retrying")
            finally:
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                    await proc.wait()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def hear_direction(self) -> None:
        """The voice was just heard: turn to it, and with the head aimed take
        a frame of the speaker for the router's face library."""
        window = self.mic_window.snapshot() if self.mic_window is not None else None
        if window is None:
            return
        est = await asyncio.to_thread(
            estimate_direction, window, DOA_RATE, self.config.sound_mic_geometry)
        if est is None:
            return
        angle, confidence = est
        await self.act(self.brain.on_sound(angle, confidence, time.time()))
        if self.router_faces:
            self._start_capture()

    def _start_capture(self) -> None:
        if self.capture_task is None or self.capture_task.done():
            self.capture_task = asyncio.create_task(self.face_for_router(settle_s=CAPTURE_SETTLE_S))

    async def face_capture(self, settle_s: float = 0.0) -> str | None:
        """A frame of whoever is in front ({"capture": true}), as base64; one at
        a time. The head settles first when it has just turned."""
        if self.face_waiter is not None:
            return None
        waiter = asyncio.get_running_loop().create_future()
        self.face_waiter = waiter
        try:
            if settle_s:
                await asyncio.sleep(settle_s)
            await self.send_device({"capture": True})
            return await asyncio.wait_for(waiter, CAPTURE_TIMEOUT_S)
        except (asyncio.TimeoutError, OSError, ConnectionError) as err:
            log(f"face capture: {err}")
            return None
        finally:
            if self.face_waiter is waiter:
                self.face_waiter = None

    async def face_for_router(self, settle_s: float = 0.0) -> None:
        """A face of whoever is talking after a turn, sent to the router as a
        probe so it can be recognised (the DOA flow; enrolment is the router's
        own capture request)."""
        b64 = await self.face_capture(settle_s)
        if b64 and self.router_ws is not None:
            try:
                await self.router_ws.send(json.dumps({"face_probe": b64}))
            except Exception as err:  # pylint: disable=broad-except
                log(f"router write failed: {err}")

    async def run(self) -> None:
        loops = [self.serial_loop(), self.lva_loop(), self.clock_loop()]
        if self.router_url and self.room:
            loops.append(self.router_loop())
        if self.config.sound_direction:
            loops.append(self.doa_loop())
        await asyncio.gather(*loops)


def main() -> None:
    bridge = Bridge(
        Config.from_env(),
        device=os.environ.get("STACKCHAN_DEVICE", "/dev/stackchan"),
        lva_url=os.environ.get("LVA_PERIPHERAL_URL", "ws://127.0.0.1:6055"),
        router_url=os.environ.get("ROUTER_BODY_URL", ""),
        room=os.environ.get("ROOM", ""),
    )
    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
