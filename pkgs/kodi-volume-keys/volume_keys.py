#!/usr/bin/env python3
"""Forward a TV keyboard's media volume keys to Kodi.

Kodi's GBM evdev input grabs every /dev/input device but only registers its
main keyboard. A wireless keyboard whose volume keys are a separate HID
"Consumer Control" interface therefore loses them: Kodi grabs the events and
never maps them. modules/storage/tv-box.nix makes that device root-only so
Kodi cannot open it; this service (root) reads it and sends the matching
action to Kodi's password-less TCP JSON-RPC.

The device is found by name (VOLUME_DEVICE, a glob) and by the volume keys it
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
    ecodes.KEY_VOLUMEUP: "volumeup",
    ecodes.KEY_VOLUMEDOWN: "volumedown",
    ecodes.KEY_MUTE: "mute",
}


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


async def watch(device: evdev.InputDevice) -> None:
    try:
        async for event in device.async_read_loop():
            if event.type != ecodes.EV_KEY or event.value == 0:
                continue
            action = ACTIONS.get(event.code)
            if action is not None:
                # The socket call blocks; keep the event loop free for repeats.
                await asyncio.to_thread(kodi_action, action)
    except OSError:
        pass  # unplugged
    finally:
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
