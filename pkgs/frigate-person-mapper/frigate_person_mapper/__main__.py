"""frigate-person-mapper: Frigate faces -> rooms, the router, and Home Assistant.

Subscribes to Frigate's face events and camera status over MQTT, keeps per-room
state, and on each change reports the room's recognised people to the assistant
router's body socket (one face-source connection per room) and to Home Assistant
(retained state plus an event). Everything else lives in the pure sibling
modules and is unit-tested; this is the only I/O.

Config from the environment (services/person-mapper.nix):
  MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD_FILE,
  ROUTER_URL, and as JSON files (PEOPLE_FILE / CAMERAS_FILE) or, for ad-hoc
  runs and tests, inline (PEOPLE_JSON / CAMERAS_JSON):
  - the cameras, room -> Frigate camera name, as the module's
    settings.cameras says;
  - the people it may name, {key: name}; none: anyone the face library
    names (the router drops keys it doesn't know).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time

import paho.mqtt.client as mqtt

try:
    from paho.mqtt.enums import CallbackAPIVersion
    _API = CallbackAPIVersion.VERSION1
except ImportError:  # paho-mqtt < 2
    _API = None

try:
    from websockets.asyncio.client import connect as _ws_connect
except ImportError:  # websockets < 14
    from websockets import connect as _ws_connect

from . import ha, router
from .frigate import (
    parse_availability,
    parse_camera_status,
    parse_events,
    parse_tracked_object_update,
)
from .state import PersonState

SUBSCRIBE = [
    "frigate/tracked_object_update",
    "frigate/events",
    "frigate/+/status/detect",
    "frigate/available",
]


def _env(name: str, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _json_env(name: str, default):
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _json_from_env(file_env: str, inline_env: str, default):
    """JSON from a file named by `file_env`, else inline `inline_env`, else default.

    The NixOS service hands the person and camera maps in as JSON files (PEOPLE_FILE
    / CAMERAS_FILE) from the Nix store; unit tests and ad-hoc runs set the inline
    *_JSON variables instead.
    """
    path = os.environ.get(file_env)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh) or default
        except (OSError, ValueError):
            return default
    return _json_env(inline_env, default)


def cameras_by_name(rooms: dict) -> dict[str, str]:
    """The configured room -> camera map, as camera -> room (PersonState's)."""
    return {str(cam): str(room) for room, cam in rooms.items() if isinstance(cam, str) and cam}


def _log(msg: str) -> None:
    print(f"frigate-person-mapper: {msg}", file=sys.stderr, flush=True)


class Mapper:
    def __init__(self) -> None:
        self.mqtt_host = _env("MQTT_HOST", "127.0.0.1")
        self.mqtt_port = int(_env("MQTT_PORT", "1883"))
        self.mqtt_user = _env("MQTT_USER")
        self.mqtt_password = self._read_password(_env("MQTT_PASSWORD_FILE"))
        self.router_url = _env("ROUTER_URL", "ws://127.0.0.1:8770/v1/body")
        self.people = _json_from_env("PEOPLE_FILE", "PEOPLE_JSON", {}) or {}
        self.cameras = cameras_by_name(_json_from_env("CAMERAS_FILE", "CAMERAS_JSON", {}) or {})
        self.state = PersonState(set(self.people) or None, self.cameras)
        self.rooms = list(self.state.rooms)
        self.frigate_up = True
        self._loop = None
        self._mqtt = None
        self._stop = asyncio.Event()
        self._room_sockets: dict[str, object] = {}
        self._last: dict[str, tuple] = {}

    @staticmethod
    def _read_password(path) -> str | None:
        if not path:
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()

    # -- MQTT (runs on paho's network thread) ---------------------------------

    def _make_client(self):
        if _API is not None:
            client = mqtt.Client(_API, client_id="frigate-person-mapper")
        else:
            client = mqtt.Client(client_id="frigate-person-mapper")
        if self.mqtt_user:
            client.username_pw_set(self.mqtt_user, self.mqtt_password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def _on_connect(self, _client, _userdata, _flags, _reason_code) -> None:
        _log(f"mqtt connected to {self.mqtt_host}:{self.mqtt_port}")
        _client.subscribe(SUBSCRIBE)
        for room in self.rooms:
            for topic, cfg in ha.discovery_configs(room):
                _client.publish(topic, json.dumps(cfg), qos=1, retain=True)

    def _on_disconnect(self, _client, _userdata, _flags, _reason_code) -> None:
        _log("mqtt disconnected; paho will retry")

    def _on_message(self, _client, _userdata, msg) -> None:
        event = self._parse(msg.topic, msg.payload)
        if event is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(asyncio.ensure_future, self._apply(event))

    def _parse(self, topic: str, payload):
        upd = parse_tracked_object_update(topic, payload)
        if upd is not None:
            return ("face", upd)
        end = parse_events(topic, payload)
        if end is not None:
            return ("end", end)
        status = parse_camera_status(topic, payload)
        if status is not None:
            return ("camera", status)
        if topic == "frigate/available":
            up = parse_availability(payload)
            if up is not None:
                return ("available", up)
        return None

    # -- asyncio side ----------------------------------------------------------

    async def _apply(self, event) -> None:
        kind, value = event
        if kind == "face":
            self.state.on_face_update(value.camera, value.event_id, value.name, value.score)
        elif kind == "end":
            self.state.on_event_end(value.camera, value.event_id)
        elif kind == "camera":
            self.state.on_camera_status(value.camera, value.online)
        elif kind == "available":
            self.frigate_up = value
        await self._publish_changed()

    async def _publish_changed(self) -> None:
        now = time.time()
        for room in self.rooms:
            snap = self.state.snapshot(room)
            sig = (snap.faces, snap.unknown, snap.camera_online, self.frigate_up)
            if self._last.get(room) == sig:
                continue
            self._last[room] = sig
            await self._send_router(room, snap)
            self._send_ha(room, snap, now)

    async def _send_router(self, room: str, snap) -> None:
        ws = self._room_sockets.get(room)
        if ws is None:
            return
        try:
            await ws.send(json.dumps(router.faces(snap.faces, snap.events)))
        except Exception:
            pass  # the room's socket task reconnects

    def _send_ha(self, room: str, snap, at: float) -> None:
        if self._mqtt is None or not self._mqtt.is_connected():
            return
        for topic, value in ha.state_values(room, snap.faces, snap.unknown, snap.camera_online):
            self._mqtt.publish(topic, value, qos=1, retain=True)
        self._mqtt.publish(
            ha.EVENT_TOPIC,
            json.dumps(ha.event_payload(room, snap.faces, snap.unknown, at)),
            qos=0,
            retain=False,
        )

    async def _room_socket(self, room: str) -> None:
        while not self._stop.is_set():
            try:
                async with _ws_connect(self.router_url) as ws:
                    self._room_sockets[room] = ws
                    await ws.send(json.dumps(router.hello(room)))
                    snap = self.state.snapshot(room)
                    await ws.send(json.dumps(router.faces(snap.faces, snap.events)))
                    _log(f"body socket for {room} at {self.router_url}")
                    async for _ in ws:
                        pass  # face-source gets no messages; this keeps it open
            except Exception as err:
                _log(f"body socket for {room}: {err}")
            finally:
                self._room_sockets.pop(room, None)
            await asyncio.sleep(2)

    async def run(self) -> int:
        if not self.cameras:
            _log("no cameras configured (CAMERAS_FILE/CAMERAS_JSON empty); exiting")
            return 0
        self._loop = asyncio.get_running_loop()
        self._mqtt = self._make_client()
        self._mqtt.connect(self.mqtt_host, self.mqtt_port)
        self._mqtt.loop_start()

        stop = self._loop.create_future()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self._loop.add_signal_handler(
                sig, lambda: stop.set_result(None) if not stop.done() else None
            )
        tasks = [asyncio.create_task(self._room_socket(room), name=f"room-{room}") for room in self.rooms]
        try:
            await stop
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
        return 0


def main() -> int:
    try:
        return asyncio.run(Mapper().run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
