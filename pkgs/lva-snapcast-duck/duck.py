#!/usr/bin/env python3
"""Fade Snapcast down while Linux Voice Assistant listens or speaks.

Follows LVA's peripheral WebSocket. The music fades down on the wake word and
stays down through listening, thinking, the reply and any follow-up turn (LVA
sends tts_finished and then listening when the conversation continues), and
fades back up on idle. The volume each Snapcast stream had before the fade is
the one it returns to.

Two levels: near silence while the microphone is open (a speech station at a
quarter of its volume is still clear speech to a microphone a metre away, and
ends up in the transcript), and a quieter background while the assistant
thinks and answers.

It also lets the assistant speak up in a loud room (ADAPT_MIC_NODE set): while
nothing is happening it takes a one-second level of the satellite's
microphone every 30 s, and the median of the last five is the room's
loudness. When a reply starts, LVA's playback streams get a boost that grows
with how far the room is above quiet (half a dB per dB, at most
ADAPT_MAX_BOOST_DB), and never past full scale: LVA's own volume (Home
Assistant's, from its preferences) times the boost stays at or below 1, so it
can't clip. In a quiet room the reply is at Home Assistant's volume. Only the
level is kept, never the audio.
"""
from __future__ import annotations

import asyncio
import collections
import json
import math
import os
import signal
import statistics
import struct
import subprocess
import sys

try:
    import websockets
except ImportError:
    print("lva-snapcast-duck: websockets package missing", file=sys.stderr)
    sys.exit(1)

# The microphone is open.
LISTEN_EVENTS = frozenset({"wake_word_detected", "listening"})
# The assistant is working out or giving its answer.
SPEAK_EVENTS = frozenset({"thinking", "tts_speaking", "timer_ringing"})
# Not tts_finished: a continued conversation follows it with listening, and
# restoring there would pump the music up and down between turns.
RESTORE_EVENTS = frozenset({"idle", "pipeline_error", "disconnected"})

PW_DUMP = os.environ.get("PW_DUMP", "pw-dump")
WPCTL = os.environ.get("WPCTL", "wpctl")
DUCK_VOLUME = float(os.environ.get("DUCK_VOLUME", "0.25"))
DUCK_LISTEN_VOLUME = float(os.environ.get("DUCK_LISTEN_VOLUME", "0.05"))
FADE_DOWN = float(os.environ.get("FADE_DOWN_SECONDS", "0.2"))
FADE_UP = float(os.environ.get("FADE_UP_SECONDS", "0.8"))
# The reply's boost comes off after its tail has played out, and ramps down.
RELEASE_DELAY = float(os.environ.get("VOICE_RELEASE_DELAY_S", "0.8"))
RELEASE_FADE = float(os.environ.get("VOICE_RELEASE_FADE_S", "0.4"))
# A pipeline that never reports idle (Home Assistant dropped mid-turn without
# the connection closing) must not leave the music down for good.
MAX_DUCK = float(os.environ.get("MAX_DUCK_SECONDS", "120"))
# The volumes to return to, kept on disk while the music is down, so that a
# restarted ducker (a deploy, a crash) puts them back instead of leaving the
# music at a fraction of its volume for good.
STATE_FILE = os.environ.get("STATE_FILE")
# The programs whose streams fade: Snapcast's client, and on a TV box Kodi's
# own music.
DUCK_BINARIES = frozenset(
    b for b in os.environ.get("DUCK_BINARIES", "snapclient").split(",") if b
)
STEP = 0.05

# The voice following the room's loudness; off when no microphone node is given.
ADAPT_MIC_NODE = os.environ.get("ADAPT_MIC_NODE", "")
ADAPT_QUIET_DB = float(os.environ.get("ADAPT_QUIET_DB", "-55"))
ADAPT_MAX_BOOST_DB = float(os.environ.get("ADAPT_MAX_BOOST_DB", "8"))
ADAPT_SLOPE = 0.5  # dB of boost per dB of room above quiet
ADAPT_EVERY_S = 30.0
ADAPT_READINGS = 5
LVA_PREFS = os.environ.get("LVA_PREFS", "")
PW_RECORD = os.environ.get("PW_RECORD", "pw-record")
# LVA plays through libmpv inside its own Python process.
VOICE_BINARY_PREFIX = os.environ.get("VOICE_BINARY_PREFIX", "python")


def run(*args: str) -> str:
    # wpctl and pw-dump warn on stderr that a system service gets no realtime
    # scheduling; that would fill the journal on every reply.
    return subprocess.run(
        args, check=False, capture_output=True, text=True
    ).stdout


def snapcast_nodes() -> list[int]:
    """PipeWire node ids of the ducked programs' playback streams (the process
    binary is a property of its client, not of the nodes)."""
    return _output_nodes(lambda binary: binary in DUCK_BINARIES)


def voice_nodes() -> list[int]:
    """PipeWire node ids of LVA's own playback streams (its replies, its
    sounds): played by its Python process, not by a ducked program."""
    return _output_nodes(lambda binary: binary.startswith(VOICE_BINARY_PREFIX)
                         and binary not in DUCK_BINARIES)


def _output_nodes(wanted) -> list[int]:
    try:
        objects = json.loads(run(PW_DUMP) or "[]")
    except json.JSONDecodeError:
        return []
    clients = {
        o["id"]
        for o in objects
        if o.get("type") == "PipeWire:Interface:Client"
        and wanted(str(o.get("info", {}).get("props", {}).get("application.process.binary") or ""))
    }
    return [
        o["id"]
        for o in objects
        if o.get("type") == "PipeWire:Interface:Node"
        and o.get("info", {}).get("props", {}).get("client.id") in clients
        and o.get("info", {}).get("props", {}).get("media.class") == "Stream/Output/Audio"
    ]


def get_volume(node: int) -> float | None:
    # "Volume: 0.40" or "Volume: 0.40 [MUTED]"
    parts = run(WPCTL, "get-volume", str(node)).split()
    try:
        return float(parts[1])
    except (IndexError, ValueError):
        return None


def set_volume(node: int, volume: float) -> None:
    run(WPCTL, "set-volume", str(node), f"{volume:.3f}")


async def fade(targets: dict[int, tuple[float, float]], seconds: float) -> None:
    """Move each node from its start to its end volume over `seconds`. Paced
    by the clock, since each wpctl call itself takes a while on a Pi 3."""
    loop = asyncio.get_running_loop()
    begin = loop.time()
    while True:
        t = min(1.0, (loop.time() - begin) / seconds) if seconds > 0 else 1.0
        for node, (start, end) in targets.items():
            set_volume(node, start + (end - start) * t)
        if t >= 1.0:
            return
        await asyncio.sleep(STEP)


def save_state(volumes: dict[int, float]) -> None:
    if not STATE_FILE:
        return
    if volumes:
        tmp = STATE_FILE + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in volumes.items()}, f)
        os.replace(tmp, STATE_FILE)
    else:
        try:
            os.remove(STATE_FILE)
        except FileNotFoundError:
            pass


def load_state() -> dict[int, float]:
    if not STATE_FILE:
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return {int(k): float(v) for k, v in json.load(f).items()}
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {}


class Ducker:
    def __init__(self) -> None:
        self.saved: dict[int, float] = {}  # volume before the duck, per node
        self.rising: dict[int, float] = {}  # where a fade up is heading
        self.level: float | None = None  # fraction of the saved volume now
        self.task: asyncio.Task | None = None
        self.timeout: asyncio.TimerHandle | None = None

    @property
    def ducked(self) -> bool:
        return bool(self.saved)

    def _start(self, coro) -> None:
        # A new fade replaces one still running, from wherever it got to.
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = asyncio.ensure_future(coro)

    def duck(self, level: float) -> None:
        if self.timeout:
            self.timeout.cancel()
        self.timeout = asyncio.get_running_loop().call_later(MAX_DUCK, self.restore)
        if self.ducked:
            if level != self.level:
                # Listening to speaking or back: from wherever it is now.
                self.level = level
                targets = {}
                for node, volume in self.saved.items():
                    now = get_volume(node)
                    if now is not None:
                        targets[node] = (now, volume * level)
                self._start(fade(targets, FADE_DOWN))
            return
        self.level = level
        targets = {}
        for node in snapcast_nodes():
            now = get_volume(node)
            if now is None:
                continue
            # Woken again while the music is still fading up: the volume to
            # return to is where that fade was heading, not where it got to.
            self.saved[node] = self.rising.get(node, now)
            targets[node] = (now, self.saved[node] * level)
        self.rising = {}
        save_state(self.saved)
        self._start(fade(targets, FADE_DOWN))

    def restore_now(self) -> None:
        """Put every stream straight back, without a fade (shutting down)."""
        for node, volume in {**self.rising, **self.saved}.items():
            set_volume(node, volume)
        self.saved, self.rising = {}, {}
        save_state({})

    def restore(self) -> None:
        if self.timeout:
            self.timeout.cancel()
            self.timeout = None
        if not self.ducked:
            return
        self.rising, self.saved = self.saved, {}
        self.level = None
        targets = {}
        for node, volume in self.rising.items():
            now = get_volume(node)
            if now is not None:  # the stream may have gone meanwhile
                targets[node] = (now, volume)
        self._start(self._rise(targets))

    async def _rise(self, targets: dict[int, tuple[float, float]]) -> None:
        await fade(targets, FADE_UP)
        self.rising = {}
        save_state({})


def level_db(pcm: bytes) -> float:
    """The RMS level of s16 mono audio, in dB below full scale (-120 for silence)."""
    n = len(pcm) // 2
    if n == 0:
        return -120.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    rms = math.sqrt(sum(v * v for v in samples) / n) / 32768.0
    return 20 * math.log10(rms) if rms > 1e-6 else -120.0


def boost_db(room_db: float | None, quiet_db: float = ADAPT_QUIET_DB,
             max_boost_db: float = ADAPT_MAX_BOOST_DB, slope: float = ADAPT_SLOPE) -> float:
    """How much louder to speak in a room this loud: nothing at or below quiet."""
    if room_db is None:
        return 0.0
    return max(0.0, min(max_boost_db, (room_db - quiet_db) * slope))


def voice_gain(boost: float, base_volume: float) -> float:
    """The playback streams' volume for a boost, capped so that LVA's own
    volume times it stays at full scale or below."""
    gain = 10 ** (boost / 20)
    if base_volume > 0:
        gain = min(gain, 1.0 / base_volume)
    return max(1.0, gain)


def lva_volume() -> float:
    """LVA's own volume (Home Assistant sets it), from its preferences."""
    try:
        with open(LVA_PREFS, encoding="utf-8") as f:
            volume = float(json.load(f).get("volume", 1.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0
    return max(0.0, min(1.0, volume))


class Room:
    """How loud the room is: the median of the last few readings."""

    def __init__(self) -> None:
        self.readings: collections.deque = collections.deque(maxlen=ADAPT_READINGS)

    def add(self, db: float) -> None:
        self.readings.append(db)

    def loudness(self) -> float | None:
        return statistics.median(self.readings) if self.readings else None


class Voice:
    """Boosts LVA's playback while it speaks, by the room's loudness."""

    def __init__(self, room: Room) -> None:
        self.room = room
        self.busy = False  # listening or answering: no reading of the room then
        self.boosted: dict[int, float] = {}  # node -> the gain it is at (or heading for)
        self.task: asyncio.Task | None = None
        self.releasing: asyncio.Task | None = None

    def speak(self) -> None:
        self.busy = True
        self._keep()
        loud = self.room.loudness()
        gain = voice_gain(boost_db(loud), lva_volume())
        if gain <= 1.001:
            return
        print(f"lva-snapcast-duck: room {loud:.0f} dBFS: voice +{20 * math.log10(gain):.1f} dB",
              file=sys.stderr, flush=True)
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = asyncio.ensure_future(self._boost(gain))

    async def _boost(self, gain: float) -> None:
        # The reply's stream appears when it starts playing: look for a moment.
        for _ in range(20):
            nodes = [n for n in voice_nodes() if self.boosted.get(n) != gain]
            if nodes:
                # From where each is now: a stream still raised from the reply
                # before moves to this gain, it does not dip to 1.0 first.
                targets = {n: (self.boosted.get(n, 1.0), gain) for n in nodes}
                self.boosted.update({n: gain for n in nodes})
                await fade(targets, 0.15)
                return
            await asyncio.sleep(0.05)

    def _keep(self) -> None:
        """A new reply begins: a release still waiting to start is dropped."""
        if self.releasing and not self.releasing.done():
            self.releasing.cancel()

    def settle(self) -> None:
        """The reply is over: LVA's streams go back to their own volume, but not
        at once. The event comes as the player finishes, and the last word is
        still on its way through the audio buffers; taking the boost off in one
        step then made the end of every reply drop several dB (heard on a Pi 5
        with a film on, 2026-10-23, +7 dB of boost). It waits for the tail to
        play out and then ramps down."""
        if self.task and not self.task.done():
            self.task.cancel()
        self._keep()
        if self.boosted:
            self.releasing = asyncio.ensure_future(self._release(dict(self.boosted)))

    async def _release(self, nodes: dict[int, float]) -> None:
        await asyncio.sleep(RELEASE_DELAY)
        await fade({n: (gain, 1.0) for n, gain in nodes.items()}, RELEASE_FADE)
        for node in nodes:
            self.boosted.pop(node, None)

    def settle_now(self) -> None:
        """Straight back to LVA's own volume (shutting down)."""
        for task in (self.task, self.releasing):
            if task and not task.done():
                task.cancel()
        for node in self.boosted:
            set_volume(node, 1.0)
        self.boosted = {}

    def idle(self) -> None:
        self.settle()
        self.busy = False


async def read_room(room: Room, voice: Voice, ducker: "Ducker") -> None:
    """Every ADAPT_EVERY_S while nothing is happening, a one-second level of
    the microphone; never while the assistant listens or speaks."""
    while True:
        await asyncio.sleep(ADAPT_EVERY_S)
        if voice.busy or ducker.ducked:
            continue
        try:
            proc = await asyncio.create_subprocess_exec(
                PW_RECORD, "--target", ADAPT_MIC_NODE, "--rate", "16000", "--channels", "1",
                "--format", "s16", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            continue
        try:
            pcm = await asyncio.wait_for(proc.stdout.readexactly(32000), 3.0)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            pcm = b""
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2.0)
                except asyncio.TimeoutError:
                    proc.kill()
        if pcm and not voice.busy:
            room.add(level_db(pcm))


async def main_loop(ducker: Ducker, voice: Voice | None = None) -> None:
    url = os.environ.get("LVA_PERIPHERAL_URL", "ws://127.0.0.1:6055")
    delay = 2.0

    while True:
        try:
            async with websockets.connect(url) as ws:
                delay = 2.0
                async for raw in ws:
                    try:
                        event = json.loads(raw).get("event")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if event in LISTEN_EVENTS:
                        ducker.duck(DUCK_LISTEN_VOLUME)
                    elif event in SPEAK_EVENTS:
                        ducker.duck(DUCK_VOLUME)
                    elif event in RESTORE_EVENTS:
                        ducker.restore()
                    if voice is not None:
                        follow(voice, event)
        except Exception as exc:  # pylint: disable=broad-except
            ducker.restore()
            if voice is not None:
                voice.idle()
            print(f"lva-snapcast-duck: {exc}", file=sys.stderr)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)


def follow(voice: Voice, event) -> None:
    """The voice's boost through one of LVA's events."""
    if event == "tts_speaking":
        voice.speak()
    elif event == "tts_finished":
        voice.settle()
    elif event in LISTEN_EVENTS or event == "thinking":
        voice.busy = True
    elif event in RESTORE_EVENTS:
        voice.idle()


async def run_until_stopped() -> None:
    # Left down by an earlier run that didn't get to put them back.
    for node, volume in load_state().items():
        set_volume(node, volume)
    save_state({})

    ducker = Ducker()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    voice = None
    tasks = []
    if ADAPT_MIC_NODE:
        room = Room()
        voice = Voice(room)
        tasks.append(asyncio.ensure_future(read_room(room, voice, ducker)))
    tasks.append(asyncio.ensure_future(main_loop(ducker, voice)))
    await stopping.wait()
    for task in tasks:
        task.cancel()
    ducker.restore_now()
    if voice is not None:
        voice.settle_now()


def main() -> None:
    asyncio.run(run_until_stopped())


if __name__ == "__main__":
    main()
