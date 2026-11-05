#!/usr/bin/env python3
"""Forward a TV keyboard's media keys to Kodi.

Kodi's GBM evdev input grabs every /dev/input device but only registers its
main keyboard. A wireless keyboard whose media keys (volume, mute, play/pause,
stop, next/previous, fast-forward/rewind) live on a separate HID "Consumer
Control" interface therefore loses them: Kodi grabs the events and never maps
them. modules/storage/tv-box.nix makes that device root-only so Kodi cannot
open it; this service (root) reads it and sends the matching action to Kodi's
password-less TCP JSON-RPC.

The device is found by name (VOLUME_DEVICE, a glob) and by the media keys it
advertises, so a replug or a different event number needs no change.
"""

import asyncio
import fnmatch
import json
import os
import socket

import evdev
from evdev import ecodes

KODI_HOST = os.environ.get("KODI_HOST", "127.0.0.1")
KODI_PORT = int(os.environ.get("KODI_PORT", "9090"))
DEVICE_MATCH = os.environ.get("VOLUME_DEVICE", "*Consumer Control*")
POLL_SECONDS = 3.0

ACTIONS = {
    # Volume and mute.
    ecodes.KEY_VOLUMEUP: "volumeup",
    ecodes.KEY_VOLUMEDOWN: "volumedown",
    ecodes.KEY_MUTE: "mute",
    # Transport. Play/pause toggles; the rest are single actions.
    ecodes.KEY_PLAYPAUSE: "playpause",
    ecodes.KEY_PLAYCD: "playpause",
    ecodes.KEY_PLAY: "playpause",
    ecodes.KEY_PAUSECD: "pause",
    ecodes.KEY_PAUSE: "pause",
    ecodes.KEY_STOPCD: "stop",
    ecodes.KEY_STOP: "stop",
    ecodes.KEY_NEXTSONG: "skipnext",
    ecodes.KEY_PREVIOUSSONG: "skipprevious",
    ecodes.KEY_FASTFORWARD: "fastforward",
    ecodes.KEY_REWIND: "rewind",
}

# Keys whose action repeats while held. Volume keys are stepped repeatedly;
# every other key is a one-shot or a toggle (mute, play/pause), so it fires
# once per press and would flicker on/off if it kept repeating.
REPEATABLE = {ecodes.KEY_VOLUMEUP, ecodes.KEY_VOLUMEDOWN}
REPEAT_DELAY = 0.5  # hold this long before the volume starts stepping
REPEAT_INTERVAL = 0.15  # step the volume this often while held


def log(msg: str) -> None:
    print(f"kodi-volume-keys: {msg}", flush=True)


def kodi_action(action: str) -> None:
    """Send one Input.ExecuteAction to Kodi's TCP JSON-RPC."""
    message = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "Input.ExecuteAction",
                "params": {"action": action},
            }
        ).encode()
    )
    try:
        with socket.create_connection((KODI_HOST, KODI_PORT), timeout=2.0) as sock:
            sock.sendall(message)
    except OSError as exc:
        log(f"{action}: {exc}")
    else:
        log(action)


async def _repeat(action: str) -> None:
    """Step the action repeatedly, until cancelled, while a key stays down.

    The Consumer Control interface the volume keys live on sends a single
    press and release and does not advertise EV_REP, so neither the device nor
    the kernel generates repeats — holding a key must therefore be expanded
    here. The socket call blocks; it is run off the loop so the timers below
    keep firing while it is in flight.
    """
    await asyncio.sleep(REPEAT_DELAY)
    try:
        while True:
            await asyncio.to_thread(kodi_action, action)
            await asyncio.sleep(REPEAT_INTERVAL)
    except asyncio.CancelledError:
        pass


async def watch(device: evdev.InputDevice) -> None:
    held: dict[int, asyncio.Task] = {}
    try:
        async for event in device.async_read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            action = ACTIONS.get(event.code)
            if action is None:
                continue
            if event.value == 0:  # release: stop stepping this key
                task = held.pop(event.code, None)
                if task is not None:
                    task.cancel()
                continue
            if event.value == 2:  # the device/kernel already repeats natively
                # Let the native stream drive it; drop our timer so we never
                # fire both.
                task = held.pop(event.code, None)
                if task is not None:
                    task.cancel()
                await asyncio.to_thread(kodi_action, action)
                continue
            # value == 1: a fresh press
            if event.code in held:
                continue
            await asyncio.to_thread(kodi_action, action)
            if event.code in REPEATABLE:
                held[event.code] = asyncio.create_task(_repeat(action))
    except OSError:
        pass  # unplugged
    finally:
        for task in held.values():
            task.cancel()
        device.close()


async def run() -> None:
    watched: set[str] = set()
    ignored: set[str] = set()
    while True:
        present = set(evdev.list_devices())
        # Forget devices that went away, so a replug (same or new event
        # number) is watched again; a watcher that ended left its path here.
        watched &= present
        ignored &= present
        for path in present - watched - ignored:
            try:
                device = evdev.InputDevice(path)
                keys = set(device.capabilities().get(ecodes.EV_KEY, []))
            except OSError:
                continue
            if fnmatch.fnmatch(device.name, DEVICE_MATCH) and keys & ACTIONS.keys():
                watched.add(path)
                log(f"watching {device.name} ({path})")
                asyncio.create_task(watch(device))
            else:
                ignored.add(path)
                device.close()
        await asyncio.sleep(POLL_SECONDS)


asyncio.run(run())
