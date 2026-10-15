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
  happy. A hand waved in front of its screen silences a ringing timer, or
  stops the reply.
- Timers: shown as countdown rings, counted down here between LVA's events.
  LVA reports a cancelled timer only as an idle in the middle of a turn.
- Night: between NIGHT_START and NIGHT_END it sleeps, dimmed and still,
  waking for a conversation.
- The voice's direction: on the wake word it hears where the voice came from
  (the satellite's microphone array, not the mono LVA listens on) and turns
  to it, so the camera meets the speaker; with the router's face library a
  frame of the speaker is probed with it (docs/stackchan.md, docs/people.md).
- Who is here: with the router's face library it also shows the router a
  face as it comes into view, and again every minute while someone stays, so
  the router knows who is in the room without anyone speaking.
- Greeting: when the router recognises someone arriving (its "greet" act),
  the robot wakes, looks happy, wags and nods with three green breaths, and
  shows "Hi <name>!"; not while asleep, nor in the middle of a turn.
- The link: USB serial when the robot is plugged into this Pi, otherwise
  Wi-Fi (WIFI_HOST): the robot dials a TLS port here, pinned to this
  bridge's own certificate, and gets in with a token. The Wi-Fi details
  travel only over USB; USB wins when both are up.
- The robot's ear: with the router, the robot records each sentence from the
  wake word to the transcript and the bridge hands it to the router, for
  voice-id to score as a second opinion on who spoke; then it is dropped.
- Phones: the robot is a Bluetooth room sensor too. The bridge hands it the
  people's phone beacons (BEACONS) and iPhone keys (the room-presence-irks
  credential) over USB only, and passes the phones it hears to the router.
- The assistant router (ROUTER_BODY_URL, pkgs/assistant-router body.py):
  the robot tells it whether someone is in front of it, its battery, link
  and mood, and its touches (the router shows them in Home Assistant); it
  sends the mood and gesture the cloud model chose for a reply, and body
  commands ("nod", "dance", "go to sleep", and from Home Assistant a caption,
  the screen's brightness, a notification light). Its mood wins over the
  guess from the reply's words.

Brain holds the decisions and no I/O, so tests/stackchan-bridge.py drives it
with scripted events.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import hashlib
import hmac
import json
import math
import os
import secrets
import ssl
import struct
import subprocess
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
# Why the robot last started, as its hello says (firmware 1.9.6): the first
# four mean something went wrong.
RESET_REASONS = ("panic", "brownout", "task-watchdog", "interrupt-watchdog", "watchdog", "power-on",
                 "software", "external", "deep-sleep", "sdio", "other")
# A line from the robot that isn't JSON is boot noise from the ROM, unless it
# is a crash's report: that goes to the journal (a few lines a minute at most).
CRASH_MARKERS = ("Guru Meditation", "Backtrace", "abort()", "assert failed", "Brownout", "rst:", "panic",
                 "Rebooting")
CRASH_LINES_PER_MIN = 30

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

# The PlayStation Eye's four mics: a line across its front, 2 cm apart, in
# metres from its centre (docs/stackchan.md). Which end is channel 0 depends on
# how the camera faces the robot: reverse the order if the head turns away.
DEFAULT_SOUND_GEOMETRY = "-0.03,0;-0.01,0;0.01,0;0.03,0"


# The conversation's phase for Home Assistant, from LVA's events.
PHASES = {
    "wake_word_detected": "listening",
    "listening": "listening",
    "thinking": "thinking",
    "tts_speaking": "speaking",
    "idle": "idle",
    "pipeline_error": "idle",
    "disconnected": "idle",
}

_ERROR_WORDS = ("failed", "unreachable", "error", "fault", "gone")
_last_error = {"text": None}


def log(msg: str) -> None:
    print(f"lva-stackchan: {msg}", file=sys.stderr, flush=True)
    if any(w in msg.lower() for w in _ERROR_WORDS):
        _last_error["text"] = msg[:80]


def _settings_path() -> str | None:
    state = os.environ.get("STATE_DIRECTORY")
    return os.path.join(state, "settings.json") if state else None


def load_settings() -> dict:
    """Settings Home Assistant changed, kept across restarts (the state dir)."""
    path = _settings_path()
    try:
        with open(path or "", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(changes: dict) -> None:
    path = _settings_path()
    if not path:
        return
    data = {**load_settings(), **changes}
    tmp = path + ".new"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError as err:
        log(f"settings not saved: {err}")


def last_error() -> str | None:
    """The last line that reported a problem (80 characters at most)."""
    return _last_error["text"]


def pi_undervolt(root: str = "/sys/class/hwmon") -> bool | None:
    """Whether this Pi's supply is sagging now (the firmware's rpi_volt
    alarm), or None on a host without one."""
    try:
        for name in os.listdir(root):
            base = os.path.join(root, name)
            try:
                with open(os.path.join(base, "name"), encoding="ascii") as f:
                    if f.read().strip() != "rpi_volt":
                        continue
                with open(os.path.join(base, "in0_lcrit_alarm"), encoding="ascii") as f:
                    return f.read().strip() == "1"
            except OSError:
                continue
    except OSError:
        pass
    return None


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
    auto_brightness: bool = True  # the screen follows the room's light
    sad_words: tuple[str, ...] = DEFAULT_SAD_WORDS
    sound_direction: bool = True  # the head turns to the voice (the satellite's mics)
    sound_offset_deg: float = 0.0  # the voice's angle as seen from the robot
    sound_turn_min_deg: float = 15.0  # the voice must move this far before a new turn
    sound_mic_geometry: str = DEFAULT_SOUND_GEOMETRY  # four "x,y" points in metres
    sound_source: str = "lanbat_ps_eye_capture"  # the microphone node to listen on
    sound_over_wifi: bool = False  # on Wi-Fi too the robot is beside these mics (wifi.besideMicrophones)

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
            auto_brightness=flag("AUTO_BRIGHTNESS", True),
            sad_words=tuple(w.strip().lower() for w in sad.split(",") if w.strip()) if sad else DEFAULT_SAD_WORDS,
            sound_direction=flag("SOUND_DIRECTION", True),
            sound_offset_deg=float(env.get("SOUND_OFFSET_DEG", "0")),
            sound_turn_min_deg=float(env.get("SOUND_TURN_MIN_DEG", "15")),
            sound_mic_geometry=env.get("SOUND_MIC_GEOMETRY", DEFAULT_SOUND_GEOMETRY),
            sound_source=env.get("SOUND_SOURCE", "lanbat_ps_eye_capture"),
            sound_over_wifi=flag("SOUND_OVER_WIFI", False),
        )


@dataclass
class Timer:
    id: str
    name: str
    total: int
    ends_at: float
    ringing: bool = False


# ── learning the turn to a voice ───────────────────────────────────────────
# After each voice estimate the robot reports where it then sees the person
# ({"face_at": …}, firmware): pairs of (the raw estimate, the face's direction).
# A straight line through them is the turn: its slope fixes short turns and a
# mirrored array (negative), its offset where the mics sit. Used only once it
# is clearly right; until then the manual mirror switch and offset apply.
TURN_MIN_SAMPLES = 6
TURN_MIN_SPREAD = 30.0  # degrees of raw estimate the samples must cover
TURN_KEEP = 40
TURN_MAX_RMS = 12.0
FACE_AT_MS = 2500  # how long the robot looks for the face after a turn
FACE_LIMIT = 68  # a face at the head's limit (±70°) may be further out: not a sample

# Faces shown to the router (face_probe) so it can say who is in the room: one
# as it comes into view (at most every PROBE_GAP_S), and again every
# PROBE_AGAIN_S while someone stays; over Wi-Fi a minute apart at most. The
# camera's frames are 320×240 already: small enough for the Wi-Fi link.
PROBE_GAP_S = 10.0
PROBE_AGAIN_S = 60.0
PROBE_WIFI_S = 60.0
PROBE_SETTLE_S = 0.6  # a face just came into view: a moment to settle in the frame
GREET_MS = 8000  # "Hi <name>!" stays this long, and the happy face with it


def _line(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    n = len(points)
    if n < 2:
        return None
    mx = sum(v for v, _ in points) / n
    my = sum(t for _, t in points) / n
    sxx = sum((v - mx) ** 2 for v, _ in points)
    if sxx <= 0:
        return None
    a = sum((v - mx) * (t - my) for v, t in points) / sxx
    return a, my - a * mx


def fit_turn(samples: list[tuple[float, float]]) -> dict | None:
    """{"gain", "offset", "rms", "n"} for `turn = gain·raw + offset`, or None
    while the samples don't make it clear."""
    def spread(pts):
        return max(v for v, _ in pts) - min(v for v, _ in pts) if pts else 0.0
    if len(samples) < TURN_MIN_SAMPLES or spread(samples) < TURN_MIN_SPREAD:
        return None
    line = _line(samples)
    if line is None:
        return None
    res = sorted(abs(t - (line[0] * v + line[1])) for v, t in samples)
    mad = res[len(res) // 2]
    limit = max(15.0, 2.5 * mad)
    keep = [(v, t) for v, t in samples if abs(t - (line[0] * v + line[1])) <= limit]
    if len(keep) < TURN_MIN_SAMPLES or spread(keep) < TURN_MIN_SPREAD:
        return None
    line = _line(keep)
    if line is None:
        return None
    a, b = line
    rms = math.sqrt(sum((t - (a * v + b)) ** 2 for v, t in keep) / len(keep))
    if not 0.3 <= abs(a) <= 3.0 or rms > TURN_MAX_RMS:
        return None
    return {"gain": round(a, 3), "offset": round(b, 1), "rms": round(rms, 1), "n": len(keep)}


class TurnCalibration:
    def __init__(self, samples=None) -> None:
        self.samples: list[tuple[float, float]] = []
        for s in samples or []:
            if isinstance(s, (list, tuple)) and len(s) == 2 and all(type(x) in (int, float) for x in s):
                self.samples.append((float(s[0]), float(s[1])))
        self.samples = self.samples[-TURN_KEEP:]
        self.fit = fit_turn(self.samples)

    def add(self, raw: float, face: float) -> bool:
        """A sample; True when it made the calibration (newly) usable."""
        before = self.fit is not None
        self.samples = (self.samples + [(float(raw), float(face))])[-TURN_KEEP:]
        self.fit = fit_turn(self.samples)
        return self.fit is not None and not before

    def turn(self, raw: float) -> float | None:
        return None if self.fit is None else self.fit["gain"] * raw + self.fit["offset"]

    def reset(self) -> None:
        self.samples, self.fit = [], None

    def status(self) -> str:
        if self.fit is None:
            return f"learning ({min(len(self.samples), TURN_MIN_SAMPLES)}/{TURN_MIN_SAMPLES})"
        return "calibrated, mirrored" if self.fit["gain"] < 0 else "calibrated"


@dataclass
class Brain:
    """Decisions only. Every method returns a list of actions:

    ("device", {...})  a line for the robot
    ("lva", {...})     a command for LVA's peripheral API
    ("mouth", bool)    start or stop following the reply's audio
    ("log", str)       something worth a line in the journal
    ("ear", "start"|"drop"|text)  the robot's recording: begin, discard, or
                       send as the sentence `text` to the router
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
    wifi: dict | None = None  # the Wi-Fi details for the robot (wifi_payload), None: none
    phones: dict | None = None  # whose phones the robot looks for (phones_payload), None: nobody
    battery_level: int | None = None
    charging: bool | None = None
    link: str | None = None  # "usb" or "wifi", from the robot's hello
    firmware: str | None = None
    restart: str | None = None  # why the robot last started (its hello): "panic", "brownout", ...
    lux: int | None = None  # light at the sensor (behind the glass: relative)
    near: bool = False  # a hand close to its screen
    ear: bool = False  # the robot has a microphone (its hello), and a router listens
    tap_url: str = ""  # the link the robot is a tag with, from the router; it keeps nothing
    # For Home Assistant (through the router): what it is doing and how it is.
    phase: str = "idle"  # idle, listening, thinking, speaking: the conversation
    voice_dir: int | None = None  # the last voice's direction, degrees, + to its left
    head_yaw: int | None = None  # where the head points, degrees (its health line)
    rssi: int | None = None  # its Wi-Fi signal, dBm, while on Wi-Fi
    heap_kb: int | None = None
    psram_kb: int | None = None
    nfc_mode: str | None = None  # "tag" (for phones) or "reader" (for cards)
    pi_undervolt: bool | None = None  # this Pi's supply sagging (None: can't tell)
    # The voice's direction mirrored (left for right): the satellite's mics the
    # other way round from the robot. Home Assistant's switch; kept on the Pi.
    sound_mirror: bool = False
    # The turn learned from where the robot then sees the person (TurnCalibration).
    calibration: "TurnCalibration" = field(default_factory=lambda: TurnCalibration())
    auto_turn: bool = True
    last_voice: dict | None = None  # the last estimate, waiting for the robot's face_at
    # Nobody where it turned: look once the other way (a mirrored array). Off
    # after nobody either way (in the dark it sees nobody anywhere, and would
    # turn away from every voice), until the camera sees a face again.
    mirror_probe: bool = True
    # Faces shown to the router (face_probe), so it can say who is here.
    probes: bool = False  # the router has a face library (its body_hello)
    probe_at: float | None = None  # when one went last
    greeted_until: float | None = None  # a greeting's happy face lasts until then
    router: bool = False  # a router is connected: the ear's recordings have somewhere to go

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
                "battery_low": self.battery_low,
                "ear": self.ear,
                # For Home Assistant (the router publishes the robot there).
                "night": self.asleep,
                "nap": self.napping,
                "battery": self.battery_level,
                "charging": self.charging,
                "link": self.link,
                "mood": self.mood,
                "brightness": self.config.brightness,
                "fw": self.firmware,
                "restart": self.restart,
                "lux": self.lux,
                "near": self.near,
                "phase": self.phase,
                "voice_dir": self.voice_dir,
                "head_yaw": self.head_yaw,
                "rssi": self.rssi,
                "heap_kb": self.heap_kb,
                "psram_kb": self.psram_kb,
                "nfc_mode": self.nfc_mode,
                "pi_undervolt": self.pi_undervolt,
                "sound_mirror": self.sound_mirror,
                "auto_turn": self.auto_turn,
                "turn_cal": self.calibration.status(),
                "turn_gain": (self.calibration.fit or {}).get("gain"),
                "turn_offset": (self.calibration.fit or {}).get("offset"),
                "turn_rms": (self.calibration.fit or {}).get("rms"),
                "turn_samples": len(self.calibration.samples),
                "bridge_error": last_error()}

    def _listening(self) -> bool:
        # Over USB only: a sentence's audio, base64 at about 43 KB/s, is more
        # than the robot's TLS link keeps up with, and broke its Wi-Fi link at
        # every wake word. On Wi-Fi the satellite's own microphone is the voice.
        return self.ear and self.router and self.link == "usb"

    def _record(self) -> list:
        return [("device", {"record": True}), ("ear", "start")] if self._listening() else []

    def _stop_recording(self, text: str | None = None) -> list:
        if not self._listening():
            return []
        return [("device", {"record": False}), ("ear", text if text else "drop")]

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
                "auto_brightness": self.config.auto_brightness,
            }}),
            self._status(),
            ("device", {"sleep": self.asleep and not self.in_turn}),
            ("device", {"mood": self.mood}),
            ("device", {"look": "user" if self.in_turn else "track"}),
            self._timers(now),
        ]

    def _end_turn(self, now: float) -> list:
        listening = self.in_turn and not self.heard
        self.in_turn = self.heard = self.replied = False
        out: list = [("mouth", False), ("device", {"mouth": 0}), self._mood("neutral"), ("device", {"look": "track"})]
        if listening:
            out += self._stop_recording()  # nothing was said: discard it
        if self.asleep:
            out += self._sleep(True)
        return out

    # ── inputs ─────────────────────────────────────────────────────────────
    def on_lva(self, event: str, data: dict, now: float) -> list:
        self.now = now
        out: list = []
        self.phase = PHASES.get(event, self.phase)
        if event == "wake_word_detected":
            out.append(("router", {"event": {"wake": True}}))
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
            out += self._record()
        elif event == "stt_text":
            self.heard = True
            out += self._caption(data.get("text", ""), "user")
            out += self._stop_recording(data.get("text", ""))
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
            if self.in_turn and not self.heard:
                out += self._stop_recording()
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
        if "tap" in msg:
            # The link a phone held to the robot opens (the router's "who
            # tapped" page); "" or null: none, the robot then reads cards.
            tap = msg["tap"]
            url = tap.get("url", "") if isinstance(tap, dict) else "" if tap is None else None
            if isinstance(url, str) and (url == "" or (url.startswith("https://") and len(url) <= 120)):
                self.tap_url = url
                return [("device", {"tap": {"url": url}})]
            return []
        if type(msg.get("nfc_read")) is int and 0 <= msg["nfc_read"] <= 120:
            # Read cards for a while instead ("remember this card").
            return [("device", {"nfc_read": msg["nfc_read"]})]
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
        yaw = act.get("yaw")
        if type(yaw) is int and -70 <= yaw <= 70:
            # The head to an angle, degrees (+ to its left); the robot takes tenths.
            out.append(("device", {"yaw": yaw * 10}))
        if type(act.get("nap")) is bool:
            out.append(("device", {"nap": act["nap"]}))
        if type(act.get("auto_turn")) is bool and act["auto_turn"] != self.auto_turn:
            self.auto_turn = act["auto_turn"]
            out.append(("save", {"auto_turn": self.auto_turn}))
        if act.get("reset_turn") is True:
            self.calibration.reset()
            out += [("save", {"turn_samples": []}), ("log", "turn calibration reset: learning again")]
        if type(act.get("mirror")) is bool and act["mirror"] != self.sound_mirror:
            self.sound_mirror = act["mirror"]
            out.append(("save", {"sound_mirror": self.sound_mirror}))
            out.append(("log", f"voice direction {'mirrored' if self.sound_mirror else 'as measured'}"))
        if act.get("listen") == "start":
            out.append(("lva", {"command": "start_listening"}))
        elif act.get("listen") == "stop":
            out.append(("lva", {"command": "stop_pipeline"}))
        sleep = act.get("sleep")
        if type(sleep) is bool:
            # Until night or day next changes (tick); in a turn, at its end.
            self.asleep = sleep
            if not self.in_turn:
                out += self._sleep(sleep) + [self._mood("sleepy" if sleep else "neutral")]
        caption = act.get("caption")
        if isinstance(caption, str) and caption.strip():
            out.append(("device", {"caption": {"text": caption.strip()[:200], "who": "status", "ms": 15000}}))
        greet = act.get("greet")
        if isinstance(greet, dict) and isinstance(greet.get("name"), str) and greet["name"].strip():
            out += self._greet(greet["name"].strip()[:40], now)
        brightness = act.get("brightness")
        if type(brightness) is int and 0 <= brightness <= 255:
            # The day's brightness until the bridge restarts; at night it stays dim.
            self.config.brightness = brightness
            if not self.asleep:
                out.append(("device", {"config": {"brightness": brightness}}))
        if "led" in act:
            led = act["led"]
            rgb = led.get("rgb") if isinstance(led, dict) else None
            if led is None:
                out.append(("device", {"led": None}))
            elif isinstance(rgb, list) and len(rgb) == 3 and all(type(c) is int and 0 <= c <= 255 for c in rgb):
                out.append(("device", {"led": {"rgb": rgb}}))
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
            if self.present:
                self.mirror_probe = True  # it can see someone: worth looking the other way again
            out.append(("router", {"state": self.router_state(now)}))
            out.append(("router", {"event": {"face": face}}))
            out += self._probe(now, PROBE_GAP_S)
        if "face_at" in msg:
            out += self._face_at(msg["face_at"], now)
        health = msg.get("health")
        if isinstance(health, dict):
            # Every minute from the robot: kept for Home Assistant, as given
            # when in range, else unknown.
            def number(key, lo, hi):
                value = health.get(key)
                return value if type(value) is int and lo <= value <= hi else None
            self.rssi = number("rssi", -127, 0)
            self.heap_kb = number("heap_kb", 0, 1 << 20)
            self.psram_kb = number("psram_kb", 0, 1 << 20)
            self.head_yaw = number("yaw", -90, 90)
            self.nfc_mode = health.get("nfc") if health.get("nfc") in ("tag", "reader") else None
        battery = msg.get("battery")
        if isinstance(battery, dict):
            if type(battery.get("level")) is int:
                self.battery_level = max(0, min(100, battery["level"]))
            if type(battery.get("charging")) is bool:
                self.charging = battery["charging"]
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
            hello = msg["hello"] if isinstance(msg["hello"], dict) else {}
            link = hello.get("link", "usb")  # firmware before 1.2 had USB only
            self.link = link if link in ("usb", "wifi") else None
            self.firmware = str(hello.get("fw"))[:16] if hello.get("fw") else None
            reset = hello.get("reset")
            self.restart = reset if reset in RESET_REASONS else None
            up = hello.get("up_s")
            out.append(("log", f"robot firmware {hello.get('fw', '?')}, protocol {hello.get('proto', '?')}, over {link}"
                        + (f"; started by {self.restart}" if self.restart else "")
                        + (f", {up} s ago" if type(up) is int else "")))
            if hello.get("proto") != PROTOCOL:
                out.append(("log", f"the robot speaks protocol {hello.get('proto')}, this bridge {PROTOCOL}: reflash it (docs/stackchan.md)"))
            if hello.get("camera") is False:
                out.append(("log", "the robot has no camera: it won't look at people"))
            ear = hello.get("ear") is True
            if ear != self.ear:
                self.ear = ear
                out.append(("router", {"state": self.router_state(now)}))
            out += self.full_state(now)
            if hello.get("nfc") is True and self.tap_url:
                out.append(("device", {"tap": {"url": self.tap_url}}))
            # Whose phones to look for, likewise: over USB, when they differ.
            want_phones = self.phones["id"] if self.phones else ""
            if link == "usb" and "phones" in hello and hello["phones"] != want_phones:
                out.append(("device", {"phones": self.phones}))
                out.append(("log", "telling the robot whose phones to look for" if self.phones
                            else "telling the robot to forget the phones"))
            # The Wi-Fi details, when the robot's differ from ours: over USB
            # only, and only to firmware that knows them (it says "wifi").
            want = self.wifi["id"] if self.wifi else ""
            if link == "usb" and "wifi" in hello and hello["wifi"] != want:
                out.append(("device", {"wifi": self.wifi}))
                out.append(("log", "giving the robot the Wi-Fi details" if self.wifi
                            else "telling the robot to forget its Wi-Fi details"))
        senses = msg.get("senses")
        if isinstance(senses, dict):
            if type(senses.get("lux")) is int and senses["lux"] >= 0:
                self.lux = senses["lux"]
            if type(senses.get("near")) is bool:
                self.near = senses["near"]
        if msg.get("wave") is True:
            # A wave: hush. A ringing timer first, else the reply.
            if any(t.ringing for t in self.timers.values()):
                out.append(("lva", {"command": "stop_timer_ringing"}))
            elif self.in_turn:
                out.append(("lva", {"command": "stop_pipeline"}))
            out.append(("router", {"event": {"wave": True}}))
        tag = msg.get("nfc")
        if isinstance(tag, str) and 8 <= len(tag) <= 20 and all(c in "0123456789ABCDEF" for c in tag):
            # A tag tapped on the robot: Home Assistant's tag scanner, or a
            # person's card (the router knows which).
            out.append(("router", {"event": {"nfc": tag}}))
        if msg.get("tapped") is True:
            # A phone read the robot's link: the router learns who from its page.
            out.append(("router", {"event": {"tapped": True}}))
        seen = msg.get("seen")
        if isinstance(seen, list):
            # Phones near the robot, for the router: person keys and strengths.
            clean = [{"person": s["person"], "rssi": s["rssi"], "via": "ble"} for s in seen[:32]
                     if isinstance(s, dict) and isinstance(s.get("person"), str) and len(s["person"]) <= 40
                     and type(s.get("rssi")) is int]
            if clean:
                out.append(("router", {"seen": clean}))
        touch = msg.get("touch")
        if touch in ("tap", "stroke"):
            zone = msg.get("zone")
            out.append(("router", {"event": {"touch": touch,
                                             "zone": zone if zone in ("front", "middle", "back", "screen") else None}}))
        if touch:
            if any(t.ringing for t in self.timers.values()):
                out.append(("lva", {"command": "stop_timer_ringing"}))
            elif touch == "tap" and self.config.touch_to_talk:
                out.append(("lva", {"command": "stop_pipeline" if self.in_turn else "start_listening"}))
            elif not self.in_turn:
                out += [self._mood("happy"), ("device", {"gesture": "nod"})]
        if msg.get("servo_fault") is not None:
            out.append(("log", f"servo {msg['servo_fault']} reported a fault"))
            if type(msg["servo_fault"]) is int:
                out.append(("router", {"event": {"servo_fault": msg["servo_fault"]}}))
        if msg.get("log"):
            out.append(("log", f"robot: {msg['log']}"))
        return out

    def _greet(self, name: str, now: float) -> list:
        """Someone it knows came by (the router): awake, happy, a wag and a
        nod with three green breaths, and "Hi <name>!". Not while asleep, nor
        in the middle of a turn."""
        if self.asleep or self.in_turn:
            return []
        self.greeted_until = now + GREET_MS / 1000
        return [self._mood("happy"), ("device", {"greet": True}),
                ("device", {"caption": {"text": f"Hi {name}!", "who": "status", "ms": GREET_MS}}),
                ("log", f"greeting {name}")]

    def _probe(self, now: float, gap: float) -> list:
        """Show the router the face in view, so it can say who is here: not
        while asleep, and not more often than `gap` (a minute over Wi-Fi)."""
        if not self.probes or not self.present or self.asleep:
            return []
        if self.link == "wifi":
            gap = max(gap, PROBE_WIFI_S)
        if self.probe_at is not None and now - self.probe_at < gap:
            return []
        self.probe_at = now
        return [("probe", "seen")]

    def _face_at(self, seen, now: float) -> list:
        """The robot's answer after a turn to a voice: a calibration sample,
        or, before there is a fit, one look at the mirror angle; nobody there
        either, back to the voice."""
        voice, out = self.last_voice, []
        if voice is None or now - voice["at"] > 8:
            return out
        if isinstance(seen, dict):
            self.mirror_probe = True  # a face, wherever: the camera can see
        if isinstance(seen, dict) and seen.get("faces") == 1 and type(seen.get("yaw")) is int \
                and abs(seen["yaw"]) < FACE_LIMIT:
            self.last_voice = None
            if self.calibration.add(voice["raw"], seen["yaw"]):
                f = self.calibration.fit
                out.append(("log", f"turning calibrated: gain {f['gain']}, offset {f['offset']} degrees, "
                                   f"rms {f['rms']} ({f['n']} samples)"))
            out.append(("save", {"turn_samples": self.calibration.samples}))
            return out
        if seen is None and voice["probed"]:
            # Nobody that way either: back to the voice, and no more looking
            # away until the camera has seen someone again.
            self.last_voice, self.mirror_probe = None, False
            target = max(-70.0, min(70.0, voice["target"]))
            self.sound_angle = target
            return [("device", {"yaw": int(round(target * 10))}),
                    ("log", f"nobody either way: back to {target:.0f} degrees, and no more looking away "
                            "until it sees someone")]
        if seen is None and self.mirror_probe and self.auto_turn and self.calibration.fit is None:
            # Nobody where it turned: the array may be mirrored. Look once the other way.
            voice["probed"] = True
            mirror = max(-70.0, min(70.0, -voice["manual"]))
            self.sound_angle = mirror
            return [("device", {"yaw": int(round(mirror * 10))}), ("device", {"face_at": FACE_AT_MS}),
                    ("log", f"nobody at {voice['manual']:.0f} degrees: looking at {mirror:.0f}")]
        return out

    def on_sound(self, angle: float, confidence: float, now: float) -> list:
        """The voice's direction (estimate_direction, from the satellite's
        microphones): the head turns to it so the camera meets the speaker.
        It moves only when the voice has moved on by sound_turn_min_deg: the
        estimate wobbles a few degrees between windows, and a turn is not a
        free gesture."""
        self.now = now
        manual = (-angle if self.sound_mirror else angle) + self.config.sound_offset_deg
        fitted = self.calibration.turn(angle) if self.auto_turn else None
        target = manual if fitted is None else fitted
        self.voice_dir = int(round(target))
        # Then, wherever the head ends up, where the person turns out to be:
        # a sample for the calibration (on_device, face_at).
        self.last_voice = {"raw": angle, "at": now, "target": target, "manual": manual, "probed": False}
        ask = [("device", {"face_at": FACE_AT_MS})]
        if _angle_moved(target, self.sound_angle) < self.config.sound_turn_min_deg:
            return ask
        self.sound_angle = target
        yaw = int(round(max(-70.0, min(70.0, target)) * 10))
        return [
            ("device", {"yaw": yaw}),
            ("log", f"voice at {angle:.0f} degrees (confidence {confidence:.2f}): turning"
                    + (" (calibrated)" if fitted is not None else "")),
        ] + ask

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
        if self.greeted_until is not None and now >= self.greeted_until:
            self.greeted_until = None  # the greeting is over: back to its usual face
            if not self.in_turn and not self.asleep and self.mood == "happy":
                out.append(self._mood("neutral"))
        out += self._probe(now, PROBE_AGAIN_S)  # someone still here: who, again
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
# atan2 give the angle. Mics in a line (the PlayStation Eye's) have no front
# and back: the leads across the line give sin θ alone, a voice behind reads as
# its mirror in front, which is where the head should turn anyway. Pure Python
# on a 0.3 s window: a few milliseconds, run in a worker thread. The mics'
# channel order is a calibration step (docs/stackchan.md).
DOA_RATE = 48000  # the card's own rate: no resampling
DOA_CHANNELS = 4
DOA_WINDOW_S = 0.3
DOA_CHUNK = 256  # frames per read, one pass through the window's deques
DOA_RETRY_S = 3  # a failed recorder: retried after this, doubling...
DOA_RETRY_MAX_S = 300  # ...up to this (a microphone left unplugged costs nothing)
DOA_USB_POLL_S = 2.0  # how often an idle direction listener looks for the robot on USB

CAPTURE_SETTLE_S = 1.2  # the head just turned: let it arrive before the frame
CAPTURE_TIMEOUT_S = 5.0  # the robot answers a capture with a face_image

_DOA_MIN_SAMPLES = 512
_DOA_CONFIDENCE_MIN = 0.25  # below this the mics do not agree on a direction
_DOA_MIN_BASELINE = 0.005  # 5 mm: an axis that short cannot be read
_DOA_LINE_TOLERANCE = 0.001  # mics within 1 mm of one line are a line


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
    if max(p[1] for p in points) - min(p[1] for p in points) < _DOA_LINE_TOLERANCE:
        return _line_direction(channels, fs, points, speed_of_sound)
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


def _line_direction(channels: list[list[float]], fs: int, points: list[tuple[float, float]],
                    speed_of_sound: float) -> tuple[float, float] | None:
    """estimate_direction for mics in a line along x: each pair's lead of its
    right mic over its left one is −sin θ · spacing / c. Neighbouring pairs and
    the outer pair are summed, so the long outer baseline weighs most."""
    order = sorted(range(4), key=lambda i: points[i][0])
    pairs = [(order[k], order[k + 1]) for k in range(3)] + [(order[0], order[3])]
    spans = [points[r][0] - points[l][0] for l, r in pairs]
    if min(spans) < _DOA_MIN_BASELINE:
        return None
    max_lag = int(math.ceil(max(spans) * fs / speed_of_sound)) + 2
    energy = [sum(map(mul, c, c)) for c in channels]
    leads = []
    for l, r in pairs:
        out = _pair_lead(channels[l], channels[r], max_lag, energy[l], energy[r])
        if out is None:
            return None
        leads.append(out)
    confidence = sum(c for _, c in leads) / len(leads)
    if confidence < _DOA_CONFIDENCE_MIN:
        return None
    sin_theta = -sum(lead for lead, _ in leads) * speed_of_sound / fs / sum(spans)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_theta)))), confidence


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


# ── the Wi-Fi link ─────────────────────────────────────────────────────────
# When the robot isn't plugged into the Pi it dials WIFI_HOST:WIFI_PORT over
# TLS. The bridge's certificate is its own (made on first start, kept in the
# state directory) and the robot pins it by its SHA-256 fingerprint; the
# robot's first line is its hello with a token from the same directory. The
# network's name and password come from a systemd credential. All of it
# reaches the robot over USB only (Brain.on_device, Bridge.send_device).
WIFI_CREDENTIAL = "stackchan-wifi"
WIFI_HELLO_TIMEOUT_S = 10.0
LINE_LIMIT = 256 * 1024  # a face's picture is the longest line

# The robot's ear: 16-bit 16 kHz mono, a sentence at most 15 s; shorter than
# half a second is not worth scoring (voice-id wants a second of speech).
EAR_BYTES_PER_S = 32000
EAR_MAX_BYTES = 15 * EAR_BYTES_PER_S
EAR_MIN_BYTES = EAR_BYTES_PER_S // 2


def read_wifi_credentials(path: str) -> tuple[str, str] | None:
    """(ssid, password) from "ssid=..." and "password=..." lines, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("ssid", "password"):
            values[key.strip()] = value.rstrip("\r")
    ssid = values.get("ssid", "")
    return (ssid, values.get("password", "")) if ssid else None


def phones_payload(beacons: str, irks: str) -> dict | None:
    """The robot's {"phones": ...} line from BEACONS ("alex=<uuid>,...") and
    the room-presence-irks lines ("<key> <32 hex>"), or None when there are
    none. As pkgs/room-presence reads them."""
    found: dict = {"beacons": {}, "irks": {}}
    for item in (beacons or "").split(","):
        key, _, uuid = item.strip().partition("=")
        uuid = uuid.replace("-", "").strip().lower()
        if key.strip() and len(uuid) == 32 and all(c in "0123456789abcdef" for c in uuid):
            found["beacons"][uuid] = key.strip()
    for line in (irks or "").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and len(parts[1]) == 32 and all(c in "0123456789abcdefABCDEF" for c in parts[1]):
            found["irks"].setdefault(parts[0], []).append(parts[1].lower())
    if not found["beacons"] and not found["irks"]:
        return None
    found["id"] = hashlib.sha256(json.dumps(found, sort_keys=True).encode()).hexdigest()[:12]
    return found


def read_phones(env=os.environ) -> dict | None:
    irks = ""
    path = os.path.join(env.get("CREDENTIALS_DIRECTORY", ""), "room-presence-irks")
    if env.get("CREDENTIALS_DIRECTORY") and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            irks = f.read()
    return phones_payload(env.get("BEACONS", ""), irks)


def wifi_payload(ssid: str, password: str, host: str, port: int, fingerprint: str, token: str) -> dict:
    """The robot's {"wifi": ...} line; "id" changes whenever anything does,
    so the robot's hello tells whether it has these."""
    details = {"ssid": ssid, "password": password, "host": host, "port": port,
               "fingerprint": fingerprint, "token": token}
    details["id"] = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()[:12]
    return details


def ensure_identity(state_dir: str, openssl: str = "openssl") -> tuple[str, str, str, str]:
    """(certificate path, key path, token, fingerprint): made on first start,
    kept after. P-256: the robot's TLS handshake is quick with it."""
    cert, key, token_path = (os.path.join(state_dir, n) for n in ("wifi-cert.pem", "wifi-key.pem", "wifi-token"))
    if not (os.path.exists(cert) and os.path.exists(key)):
        subprocess.run(
            [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
             "-nodes", "-days", "36500", "-subj", "/CN=lva-stackchan", "-keyout", key, "-out", cert],
            check=True, capture_output=True)
        os.chmod(key, 0o600)
    if not os.path.exists(token_path):
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(16))
    with open(token_path, encoding="utf-8") as f:
        token = f.read().strip()
    with open(cert, encoding="ascii") as f:
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest()
    return cert, key, token, fingerprint


@dataclass
class WifiLink:
    host: str  # where the robot dials this Pi
    port: int
    cert: str
    key: str
    token: str
    fingerprint: str

    def token_ok(self, token) -> bool:
        return isinstance(token, str) and hmac.compare_digest(token.encode(), self.token.encode())


def setup_wifi(env=os.environ) -> tuple[WifiLink | None, dict | None]:
    """The Wi-Fi link and the details for the robot, from the environment;
    (None, None) when Wi-Fi is off or the credentials are missing."""
    host = env.get("WIFI_HOST", "")
    if not host:
        return None, None
    creds = read_wifi_credentials(os.path.join(env.get("CREDENTIALS_DIRECTORY", ""), WIFI_CREDENTIAL))
    if creds is None:
        log(f"Wi-Fi: no {WIFI_CREDENTIAL} credential with an ssid; USB only")
        return None, None
    port = int(env.get("WIFI_PORT", "8771"))
    cert, key, token, fingerprint = ensure_identity(env["STATE_DIRECTORY"], env.get("OPENSSL", "openssl"))
    link = WifiLink(host, port, cert, key, token, fingerprint)
    return link, wifi_payload(creds[0], creds[1], host, port, fingerprint, token)


# ── I/O ────────────────────────────────────────────────────────────────────
class Bridge:
    def __init__(self, config: Config, device: str, lva_url: str, router_url: str = "", room: str = "",
                 wifi: WifiLink | None = None, wifi_details: dict | None = None,
                 phones: dict | None = None) -> None:
        self.brain = Brain(config, now=time.time(), wifi=wifi_details, phones=phones)
        self.device = device
        self.lva_url = lva_url
        self.router_url = router_url
        self.room = room
        self.wifi = wifi
        self.serial_writer: asyncio.StreamWriter | None = None
        self.wifi_writer: asyncio.StreamWriter | None = None
        self.ws = None
        self.router_ws = None
        self.mouth_task: asyncio.Task | None = None
        self.config = config
        self.mic_window: MicWindow | None = None
        self.router_faces = False  # the router has a face library to probe
        self.crash_lines = (0, 0)  # (minute, lines of a crash's report logged in it)
        self.face_waiter: asyncio.Future | None = None  # a capture in flight
        self.router_sent: dict | None = None  # the last state the router got
        self.ear_audio = bytearray()  # the sentence the robot is recording
        self.ear_on = False
        self.capture_task: asyncio.Task | None = None

    def beside_mics(self) -> bool:
        """Whether the robot is beside this Pi's microphones: plugged into it,
        or on Wi-Fi when it is only unplugged for its power
        (wifi.besideMicrophones). Elsewhere these mics can't aim it."""
        return self.serial_writer is not None or (self.config.sound_over_wifi and self.wifi_writer is not None)

    async def send_device(self, line: dict) -> None:
        """A line for the robot, over USB when it is plugged in, else over
        Wi-Fi. The Wi-Fi details and the phones' keys go over USB or not at all."""
        writer = self.serial_writer
        if writer is None and "wifi" not in line and "phones" not in line:
            writer = self.wifi_writer
        if writer is None:
            return
        try:
            writer.write(json.dumps(line, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
        except (OSError, ConnectionError) as err:
            log(f"robot write failed: {err}")
            if writer is self.serial_writer:
                self.serial_writer = None
            elif writer is self.wifi_writer:
                self.wifi_writer = None

    async def ear(self, what: str) -> None:
        """The robot's recording: start it, drop it, or send it to the router
        as the sentence `what` (then drop it): it is never kept here."""
        if what == "start":
            self.ear_audio, self.ear_on = bytearray(), True
            return
        audio, self.ear_audio, self.ear_on = bytes(self.ear_audio), bytearray(), False
        if what == "drop" or len(audio) < EAR_MIN_BYTES or self.router_ws is None:
            return
        try:
            await self.router_ws.send(json.dumps({"voice_sample": {
                "text": what, "pcm": base64.b64encode(audio).decode("ascii")}}))
        except Exception as err:  # pylint: disable=broad-except
            log(f"router write failed: {err}")

    async def on_line(self, raw: bytes) -> None:
        """A line from the robot, on either link."""
        try:
            msg = json.loads(raw)
        except ValueError:
            # Boot noise from the ROM before the firmware speaks, or a crash's report.
            text = raw.decode("utf-8", "replace").strip()
            if text and any(m in text for m in CRASH_MARKERS):
                minute = int(time.monotonic() // 60)
                if self.crash_lines[0] != minute:
                    self.crash_lines = (minute, 0)
                if self.crash_lines[1] < CRASH_LINES_PER_MIN:
                    self.crash_lines = (minute, self.crash_lines[1] + 1)
                    log(f"robot says: {text[:200]}")
            return
        if not isinstance(msg, dict):
            return
        if "audio" in msg:
            # The ear's audio, kept only until the sentence's transcript.
            if self.ear_on and isinstance(msg["audio"], str) and len(self.ear_audio) < EAR_MAX_BYTES:
                try:
                    self.ear_audio += base64.b64decode(msg["audio"], validate=True)
                except (binascii.Error, ValueError):
                    pass
            return
        img = msg.get("face_image")
        if isinstance(img, str) and self.face_waiter is not None:
            waiter, self.face_waiter = self.face_waiter, None
            if not waiter.done():
                waiter.set_result(img)
            return  # the capture's answer, not a sensor event
        await self.act(self.brain.on_device(msg, time.time()))

    async def act(self, actions: list) -> None:
        await self._act(actions)
        # The router shows the robot in Home Assistant: its state whenever it
        # changed (a mood, the battery), not only when the brain says so.
        state = self.brain.router_state(time.time())
        state.pop("present_since_s", None)
        if self.router_ws is not None and state != self.router_sent:
            await self._act([("router", {"state": self.brain.router_state(time.time())})])

    async def _act(self, actions: list) -> None:
        for kind, value in actions:
            if kind == "device":
                await self.send_device(value)
                if isinstance(value, dict) and value.get("gesture") in GESTURES and self.router_ws is not None:
                    try:
                        await self.router_ws.send(json.dumps({"event": {"gesture": value["gesture"]}}))
                    except Exception as err:  # pylint: disable=broad-except
                        log(f"router write failed: {err}")
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
                    if "state" in value:
                        self.router_sent = {k: v for k, v in value["state"].items() if k != "present_since_s"}
                    try:
                        await self.router_ws.send(json.dumps(value))
                    except Exception as err:  # pylint: disable=broad-except
                        log(f"router write failed: {err}")
            elif kind == "probe":
                self._start_capture(value)
            elif kind == "save":
                save_settings(value)
            elif kind == "ear":
                await self.ear(value)
            elif kind == "log":
                log(value)

    async def serial_loop(self) -> None:
        import serial_asyncio_fast

        while True:
            try:
                reader, self.serial_writer = await serial_asyncio_fast.open_serial_connection(
                    url=self.device, baudrate=115200, limit=LINE_LIMIT)
                log(f"robot connected on {self.device}")
                await self.act(self.brain.full_state(time.time()))
                # Its hello says what it has (the Wi-Fi details); one that
                # was already up when the bridge started would not send it.
                await self.send_device({"hello": 1})
                while True:
                    raw = await reader.readline()
                    if not raw:
                        break
                    await self.on_line(raw)
            except (OSError, ConnectionError, ValueError) as err:
                if self.serial_writer is not None:
                    log(f"robot gone from USB: {err}")
            self.serial_writer = None
            await asyncio.sleep(3)

    async def wifi_loop(self) -> None:
        """The robot over Wi-Fi, when it isn't plugged in: a TLS port it
        dials; its first line is a hello with the token, or it is dropped."""
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.wifi.cert, self.wifi.key)
        server = await asyncio.start_server(self.wifi_client, port=self.wifi.port, ssl=ctx, limit=LINE_LIMIT)
        log(f"robot can dial {self.wifi.host}:{self.wifi.port} over Wi-Fi")
        async with server:
            await server.serve_forever()

    async def wifi_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            raw = await asyncio.wait_for(reader.readline(), WIFI_HELLO_TIMEOUT_S)
            msg = json.loads(raw) if raw else None
            hello = msg.get("hello") if isinstance(msg, dict) else None
            if not isinstance(hello, dict) or not self.wifi.token_ok(hello.pop("token", None)):
                log(f"refused a robot on Wi-Fi from {peer}: no hello with the right token")
                return
            old, self.wifi_writer = self.wifi_writer, writer
            if old is not None:
                old.close()
            log(f"robot connected over Wi-Fi from {peer}")
            await self.act(self.brain.on_device(msg, time.time()))  # the hello: the whole state
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                await self.on_line(raw)
        except (OSError, ConnectionError, ValueError, asyncio.TimeoutError) as err:
            log(f"robot on Wi-Fi from {peer}: {err or type(err).__name__}")
        finally:
            if self.wifi_writer is writer:
                self.wifi_writer = None
                log("robot gone from Wi-Fi")
            writer.close()

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
                async with websockets.connect(self.router_url, max_size=2**22) as ws:
                    self.router_ws, delay = ws, 1
                    self.brain.router = True
                    self.router_sent = None
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
                                self.brain.probes = self.router_faces
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
            self.brain.router = self.brain.probes = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def clock_loop(self) -> None:
        while True:
            await asyncio.sleep(3)
            self.brain.pi_undervolt = pi_undervolt()
            await self.act(self.brain.tick(time.time()))
            # Heartbeat: the robot shows it has lost its Pi after 10 s without a line.
            await self.send_device({"ping": 1})

    async def doa_loop(self) -> None:
        """The rolling window behind hear_direction: the microphone node at
        its full channel count, not the mono LVA listens on."""
        delay = DOA_RETRY_S
        while True:
            # Only while the robot is next to these mics (on this Pi's USB, or
            # on Wi-Fi beside them): elsewhere hear_direction ignores them, so
            # the four channels (and PipeWire's work for them) would be wasted.
            while not self.beside_mics():
                self.mic_window = None
                await asyncio.sleep(DOA_USB_POLL_S)
            self.mic_window = MicWindow(DOA_CHANNELS, int(DOA_WINDOW_S * DOA_RATE))
            proc: asyncio.subprocess.Process | None = None
            started = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    os.environ.get("PW_RECORD", "pw-record"),
                    "--rate", str(DOA_RATE), "--channels", str(DOA_CHANNELS), "--format", "s16",
                    "-P", f"{{ node.name = lva-stackchan-doa target.object = {self.config.sound_source} }}",
                    "-",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                if delay == DOA_RETRY_S:
                    log(f"hearing direction on {self.config.sound_source}")
                while self.beside_mics():
                    # A stalled recorder (no data in 2 s) is restarted, and the
                    # robot leaving is seen even then.
                    self.mic_window.push(await asyncio.wait_for(
                        proc.stdout.readexactly(DOA_CHUNK * DOA_CHANNELS * 2), 2.0))
                self.mic_window = None  # the robot left USB: wait for it, quietly
                continue
            except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as err:
                self.mic_window = None  # nothing current to estimate from
                if delay == DOA_RETRY_S:
                    log(f"direction listener stopped ({err}); retrying, less often while it fails")
            finally:
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), 2.0)
                    except asyncio.TimeoutError:
                        proc.kill()
            # A node that is missing (the microphone unplugged) fails at once:
            # back off, and start over only after a recorder that kept running.
            delay = DOA_RETRY_S if time.monotonic() - started > 30 else min(delay * 2, DOA_RETRY_MAX_S)
            await asyncio.sleep(delay)

    async def hear_direction(self) -> None:
        """The voice was just heard: turn to it, and with the head aimed take
        a frame of the speaker for the router's face library."""
        if not self.beside_mics():
            return  # on Wi-Fi the robot is elsewhere: these mics can't aim it
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
            self.brain.probe_at = time.time()  # the minute's look, too
            self._start_capture()

    def _start_capture(self, why: str = "turn") -> None:
        """A frame for the router: of the speaker after a turn ("turn"), or of
        whoever is in view ("seen")."""
        if self.capture_task is None or self.capture_task.done():
            settle = CAPTURE_SETTLE_S if why == "turn" else PROBE_SETTLE_S
            self.capture_task = asyncio.create_task(self.face_for_router(settle_s=settle, why=why))

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

    async def face_for_router(self, settle_s: float = 0.0, why: str = "turn") -> None:
        """A face sent to the router as a probe so it can be recognised: of
        whoever is talking after a turn ("turn", the DOA flow), or of whoever
        came into view ("seen"). Enrolment is the router's own capture request."""
        b64 = await self.face_capture(settle_s)
        if b64 and self.router_ws is not None:
            try:
                await self.router_ws.send(json.dumps({"face_probe": b64, "why": why}))
            except Exception as err:  # pylint: disable=broad-except
                log(f"router write failed: {err}")

    async def run(self) -> None:
        loops = [self.serial_loop(), self.lva_loop(), self.clock_loop()]
        if self.wifi is not None:
            loops.append(self.wifi_loop())
        if self.router_url and self.room:
            loops.append(self.router_loop())
        if self.config.sound_direction:
            loops.append(self.doa_loop())
        await asyncio.gather(*loops)


def main() -> None:
    wifi, wifi_details = setup_wifi()
    bridge = Bridge(
        Config.from_env(),
        device=os.environ.get("STACKCHAN_DEVICE", "/dev/stackchan"),
        lva_url=os.environ.get("LVA_PERIPHERAL_URL", "ws://127.0.0.1:6055"),
        router_url=os.environ.get("ROUTER_BODY_URL", ""),
        room=os.environ.get("ROOM", ""),
        wifi=wifi,
        wifi_details=wifi_details,
        phones=read_phones(),
    )
    settings = load_settings()
    bridge.brain.sound_mirror = settings.get("sound_mirror") is True
    bridge.brain.auto_turn = settings.get("auto_turn") is not False
    bridge.brain.calibration = TurnCalibration(settings.get("turn_samples"))
    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
