#!/usr/bin/env python3
"""A satellite's USB camera (the PlayStation Eye's) as a face source for the assistant router.

The router recognises faces against Frigate's face library (docs/people.md). A
camera with no detector of its own would flood it, so this sends a frame only
when something moves, and then at most every GAP_S seconds (AGAIN_S once the
router named someone, and after a few unnamed frames in a row). The camera is
opened for one frame at a time and closed again; nothing streams, and nothing
is kept here.

It is a router client of its own kind ("satellite-camera"), not a body: a room
may have a Stack-chan or a screen as well, and this does not replace it. The
router also asks it for a frame ({"capture": true}) when someone says "remember
me" or taps a phone, which is answered at once, moving or not. Frames are always
sent as "seen": a fixed camera is not aimed at whoever is speaking.

Configuration, from the environment: ROUTER_BODY_URL, ROOM, DEVICE, FFMPEG,
SAMPLE_S, MOTION, GAP_S, AGAIN_S.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time

PROTOCOL = 1
SAMPLE_S = 3.0    # how often the camera is looked at, for movement
MOTION = 6.0      # mean change per pixel (0-255) that counts as movement
GAP_S = 20.0      # between two frames sent while nobody is named
AGAIN_S = 60.0    # between two frames sent after the router named someone
UNNAMED_MAX = 3   # unnamed frames in a row before it slows to AGAIN_S
STILL_S = 60.0    # this long without movement forgets who was there
SAMPLE_SIZE = (160, 120)  # the frame movement is judged on, grey
SEND_SIZE = (320, 240)    # the frame sent: what the robots send too


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def motion(a: bytes, b: bytes) -> float:
    """The mean change per pixel between two grey frames of one size (0-255)."""
    if not a or len(a) != len(b):
        return 0.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


class Gate:
    """When a frame goes to the router: on movement, spaced out, and quieter once it keeps
    finding no one it knows. Pure: the clock is passed in."""

    def __init__(self, threshold: float = MOTION, gap: float = GAP_S, again: float = AGAIN_S) -> None:
        self.threshold, self.gap, self.again = threshold, gap, again
        self.faces = False        # the router has a face library (its body_hello)
        self._sent = float("-inf")
        self._named = False
        self._unnamed = 0
        self._moved = float("-inf")

    def moving(self, now: float, score: float) -> None:
        if score >= self.threshold:
            self._moved = now

    def due(self, now: float) -> bool:
        """A frame should be sent now."""
        if not self.faces or now - self._moved > STILL_S:
            return False  # nothing moved lately (or nobody can use a frame)
        wait = self.again if (self._named or self._unnamed >= UNNAMED_MAX) else self.gap
        return now - self._sent >= wait and now - self._moved <= SAMPLE_S * 2

    def sent(self, now: float) -> None:
        self._sent = now

    def answered(self, named: bool) -> None:
        """The router's face_result: whether it named someone."""
        self._named = named
        self._unnamed = 0 if named else self._unnamed + 1

    def still(self, now: float) -> None:
        """Nothing moved for a while: the next person is new."""
        if now - self._moved > STILL_S:
            self._named, self._unnamed = False, 0


async def grab(ffmpeg: str, device: str, size: tuple[int, int], grey: bool) -> bytes | None:
    """One frame from the camera, or None: a small grey one to judge movement on, or a JPEG.
    The first frames of a webcam are dark, so two are skipped."""
    out = ["-pix_fmt", "gray", "-f", "rawvideo"] if grey else ["-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5"]
    cmd = [ffmpeg, "-loglevel", "error", "-f", "video4linux2", "-i", device,
           "-vf", f"select=gte(n\\,2),scale={size[0]}:{size[1]}", "-frames:v", "1", *out, "-"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    try:
        data, _ = await asyncio.wait_for(proc.communicate(), 15)
    except (asyncio.TimeoutError, asyncio.CancelledError) as err:
        # A camera that hangs, or a router that went away: don't leave ffmpeg holding the device.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if isinstance(err, asyncio.CancelledError):
            raise
        return None
    expected = size[0] * size[1]
    if proc.returncode != 0 or not data or (grey and len(data) != expected):
        return None
    return data


class Camera:
    def __init__(self, ffmpeg: str, device: str, gate: Gate, sample_s: float = SAMPLE_S) -> None:
        self.ffmpeg, self.device, self.gate, self.sample_s = ffmpeg, device, gate, sample_s
        self._busy = asyncio.Lock()  # the camera is used by one grab at a time

    async def frame(self) -> bytes | None:
        async with self._busy:
            return await grab(self.ffmpeg, self.device, SEND_SIZE, grey=False)

    async def sample(self) -> bytes | None:
        async with self._busy:
            return await grab(self.ffmpeg, self.device, SAMPLE_SIZE, grey=True)


async def watch(ws, camera: Camera, clock=time.monotonic) -> None:
    """Look at the camera now and then; send a frame when something moved and the gate says so."""
    previous = b""
    while True:
        await asyncio.sleep(camera.sample_s)
        now = clock()
        grey = await camera.sample()
        if grey is not None:
            camera.gate.moving(now, motion(previous, grey))
            previous = grey
        camera.gate.still(now)
        if camera.gate.due(now):
            jpeg = await camera.frame()
            camera.gate.sent(clock())
            if jpeg:
                await ws.send(json.dumps({"face_probe": base64.b64encode(jpeg).decode(), "why": "seen"}))


async def listen(ws, camera: Camera) -> None:
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if isinstance(msg.get("body_hello"), dict):
            camera.gate.faces = bool(msg["body_hello"].get("faces"))
            log(f"the router {'has' if camera.gate.faces else 'has no'} face library")
        if isinstance(msg.get("face_result"), dict):
            camera.gate.answered(bool(msg["face_result"].get("person")))
        if msg.get("capture"):
            jpeg = await camera.frame()
            await ws.send(json.dumps({"face_image": base64.b64encode(jpeg).decode() if jpeg else None}))


async def run(url: str, room: str, camera: Camera) -> None:
    import websockets

    delay = 1
    while True:
        try:
            async with websockets.connect(url, max_size=2 ** 22) as ws:
                await ws.send(json.dumps({"hello": {"room": room, "kind": "satellite-camera", "proto": PROTOCOL}}))
                log(f"camera of {room} at {url}")
                delay = 1
                tasks = [asyncio.create_task(watch(ws, camera)), asyncio.create_task(listen(ws, camera))]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()  # the connection closed: raise and reconnect
        except (OSError, websockets.exceptions.WebSocketException) as err:
            log(f"router unreachable ({err}); retrying")
        camera.gate.faces = False
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def main() -> None:
    env = os.environ
    gate = Gate(float(env.get("MOTION", MOTION)), float(env.get("GAP_S", GAP_S)), float(env.get("AGAIN_S", AGAIN_S)))
    camera = Camera(env.get("FFMPEG", "ffmpeg"), env.get("DEVICE", "/dev/video0"), gate,
                    float(env.get("SAMPLE_S", SAMPLE_S)))
    asyncio.run(run(env["ROUTER_BODY_URL"], env["ROOM"], camera))


if __name__ == "__main__":
    main()
