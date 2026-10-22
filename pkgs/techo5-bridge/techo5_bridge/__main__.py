"""The bridge's I/O: each Echo over the ESPHome API, the router over its body
socket, and the sign-in codes over HTTP (brain.py decides).

Configuration is one JSON file (CONFIG_FILE, written by
services/techo5-bridge.nix): the router's URL, the people and their beacons,
where the codes are served, and the devices, each with its address, room,
noise key file, settings and profiles. Home Assistant's token is read from
HA_TOKEN_FILE, when there is one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import ssl
import time
from datetime import datetime

import aiohttp
from aiohttp import web

from . import ca, qr
from .brain import Brain, DeviceConfig
from .lists import Ha

LOG = logging.getLogger("techo5-bridge")
IMAGE_TIMEOUT_S = 8.0


class Echo:
    """One device: its ESPHome connection and its router socket."""

    def __init__(self, data: dict, people: dict[str, str], beacons: dict[str, str], router_url: str,
                 token: str, codes: dict[str, "Echo"], code_base: str, trust: dict | None = None,
                 ha: Ha | None = None) -> None:
        self.address = data["address"]
        self.key = open(data["key_file"], encoding="utf-8").read().strip()
        self.code_id = secrets.token_urlsafe(16)
        codes[self.code_id] = self
        if code_base:
            data = dict(data, code_url=f"{code_base}/code/{self.code_id}.png")
        self.trust = trust or {}  # the CA, and SSH to put it on the device (ca.py)
        self.brain = Brain(DeviceConfig.from_json(data, token, self.trust.get("keys", "")), people, beacons)
        self.ha = ha
        self.router_url = router_url
        self.client = None
        self.entities: dict[str, object] = {}   # object_id -> its info
        self.by_key: dict[int, str] = {}        # key -> object_id
        self.services: dict[str, object] = {}
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.images: list[str] = []             # frames asked for, in order
        self.image_done: asyncio.Event = asyncio.Event()
        self.code_png: bytes | None = None
        self.code_for: str | None = None

    @property
    def name(self) -> str:
        return self.brain.cfg.name

    # ── outputs ─────────────────────────────────────────────────────────────
    async def carry(self, outs: list) -> None:
        for note in self.brain.take_notes():  # what it decided, and why
            LOG.info(json.dumps({"device": self.name, **{k.rstrip("_"): v for k, v in note.items()}}))
        for out in outs:
            try:
                await self._carry(out)
            except Exception as err:  # pylint: disable=broad-except
                LOG.warning(json.dumps({"device": self.name, "output": str(out[:2]), "error": str(err)[:200]}))

    async def _carry(self, out: tuple) -> None:
        kind = out[0]
        if kind == "router":
            if self.ws is not None and not self.ws.closed:
                await self.ws.send_json(out[1])
        elif kind == "set":
            self._set(out[1], out[2])
        elif kind == "call":
            service = self.services.get(out[1])
            if service is None or self.client is None:
                return
            await self.client.execute_service(service, out[2])
        elif kind == "ca":
            asyncio.ensure_future(self._install_ca())
        elif kind == "image":
            if self.client is None:
                await self.carry(self.brain.image(out[1], None))
                return
            self.images.append(out[1])
            self.client.request_single_image()
            asyncio.get_running_loop().call_later(IMAGE_TIMEOUT_S, self._image_timeout, out[1])
        elif kind == "fetch":
            await self._fetch_items(out[1], out[2], out[3])

    async def _fetch_items(self, for_person: str | None, profile, gen: int) -> None:
        outs = await fetch_items_from_ha(self.brain, self.ha, for_person, profile, gen)
        if outs:
            await self.carry(outs)

    def _set(self, object_id: str, value) -> None:
        info = self.entities.get(object_id)
        if info is None or self.client is None:
            return
        kind = type(info).__name__
        if kind == "SwitchInfo":
            self.client.switch_command(info.key, bool(value))
        elif kind == "SelectInfo":
            self.client.select_command(info.key, str(value))
        elif kind == "NumberInfo":
            self.client.number_command(info.key, float(value))
        elif kind == "TextInfo":
            self.client.text_command(info.key, str(value))
        elif kind == "LightInfo":
            self.client.light_command(info.key, state=bool(value))

    async def _install_ca(self) -> None:
        t = self.trust
        ok, said = await ca.install(self.address, t["key_file"], t["known_hosts"], t["ca_pem"], t.get("ssh", "ssh"))
        LOG.log(logging.INFO if ok else logging.WARNING,
                json.dumps({"device": self.name, "ca": said if ok else "failed", "error": None if ok else said}))
        self.brain.ca_done(ok, time.monotonic())

    def _image_timeout(self, why: str) -> None:
        if why in self.images:
            self.images.remove(why)
            asyncio.ensure_future(self.carry(self.brain.image(why, None)))

    # ── the device ──────────────────────────────────────────────────────────
    def _on_state(self, state) -> None:
        object_id = self.by_key.get(state.key)
        if object_id is None:
            return
        if hasattr(state, "data") and type(state).__name__ == "CameraState":
            if self.images:
                why = self.images.pop(0)
                asyncio.ensure_future(self.carry(self.brain.image(why, bytes(state.data))))
            return
        value = getattr(state, "event_type", None) if type(state).__name__ == "Event" else getattr(state, "state", None)
        if getattr(state, "missing_state", False):
            value = None
        asyncio.ensure_future(self.carry(self.brain.state(object_id, value, time.monotonic())))

    def _on_adverts(self, resp) -> None:
        now = time.monotonic()
        for ad in resp.advertisements:
            self.brain.advert(ad.address, ad.rssi, bytes(ad.data), now)

    async def device_loop(self) -> None:
        from aioesphomeapi import APIClient, APIConnectionError

        delay = 2
        while True:
            stopped = asyncio.Event()

            async def on_stop(_expected: bool) -> None:
                stopped.set()

            client = APIClient(self.address, 6053, None, noise_psk=self.key, client_info="lanbat-techo5-bridge")
            try:
                await client.connect(on_stop=on_stop, login=True)
                infos, services = await client.list_entities_services()
                self.entities = {i.object_id: i for i in infos}
                self.by_key = {i.key: i.object_id for i in infos}
                self.services = {s.name: s for s in services}
                self.client = client
                client.subscribe_states(self._on_state)
                unsubscribe = None
                if "bluetooth_proxy" in self.entities:
                    unsubscribe = client.subscribe_bluetooth_le_raw_advertisements(self._on_adverts)
                LOG.info(json.dumps({"device": self.name, "connected": self.address,
                                     "entities": len(infos), "actions": len(services)}))
                delay = 2
                await self.carry(self.brain.connected(time.monotonic()))
                await stopped.wait()
                if unsubscribe is not None:
                    try:
                        unsubscribe()
                    except Exception:  # pylint: disable=broad-except
                        pass
            except (APIConnectionError, OSError, asyncio.TimeoutError) as err:
                LOG.warning(json.dumps({"device": self.name, "unreachable": self.address, "error": str(err)[:200]}))
            finally:
                self.client = None
                try:
                    await client.disconnect()
                except Exception:  # pylint: disable=broad-except
                    pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    # ── the router ──────────────────────────────────────────────────────────
    async def router_loop(self, session: aiohttp.ClientSession) -> None:
        delay = 1
        while True:
            try:
                async with session.ws_connect(self.router_url, heartbeat=20) as ws:
                    self.ws = ws
                    delay = 1
                    await self.carry(self.brain.router_connected(time.monotonic()))
                    await ws.send_json(self.brain.hello())
                    await ws.send_json({"state": self.brain._state(time.monotonic())})  # pylint: disable=protected-access
                    LOG.info(json.dumps({"device": self.name, "router": "connected", "room": self.brain.cfg.room}))
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except ValueError:
                            continue
                        await self.carry(self.brain.router(data, time.monotonic()))
            except (aiohttp.ClientError, OSError) as err:
                LOG.warning(json.dumps({"device": self.name, "router": "unreachable", "error": str(err)[:200]}))
            finally:
                self.ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def tick_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            outs = self.brain.tick(now)
            if self.client is not None:
                outs += self.brain.settings(now)
            await self.carry(outs)

    def code(self) -> bytes | None:
        """The sign-in code for the room's current link, as a PNG."""
        if self.brain.link is None:
            return None
        if self.code_for != self.brain.link:
            self.code_png, self.code_for = qr.png(self.brain.link), self.brain.link
        return self.code_png


async def fetch_items_from_ha(brain: Brain, ha: Ha | None, for_person: str | None, profile, gen: int) -> list:
    if ha is None:
        return []
    now = datetime.now().astimezone()
    rem = todo = ev = None
    if profile.reminders:
        rem = await ha.todo_items(profile.reminders)
    if profile.todo:
        todo = await ha.todo_items(profile.todo)
    cals = profile.next_up or profile.calendars or brain.cfg.household.calendars
    if cals:
        ev = await ha.events(cals)
    return brain.apply_fetch(for_person, gen, rem, todo, ev, now=now, mono=time.monotonic())


def trust_from(cfg: dict) -> dict | None:
    """The CA and the SSH that puts it on the devices: the bridge's key, whose
    public half (and the profile's admin key) go in their authorized_keys."""
    from cryptography.hazmat.primitives import serialization

    try:
        ca_pem = open(cfg["ca_file"], encoding="utf-8").read()
        with open(cfg["ssh_key_file"], "rb") as f:
            key = serialization.load_ssh_private_key(f.read(), password=None)
    except (OSError, ValueError) as err:
        LOG.warning("no CA for the devices (%s): they keep their own trust", err)
        return None
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                           serialization.PublicFormat.OpenSSH).decode()
    keys = "\n".join([f"{public} lanbat-techo5-bridge"] + [k for k in cfg.get("ssh_extra_keys") or [] if k])
    return {"ca_pem": ca_pem, "key_file": cfg["ssh_key_file"], "known_hosts": cfg["known_hosts"],
            "ssh": cfg.get("ssh") or "ssh", "keys": keys}


def code_app(codes: dict[str, Echo]) -> web.Application:
    async def code(request: web.Request) -> web.Response:
        echo = codes.get(request.match_info["id"])
        png = echo.code() if echo is not None else None
        if png is None:
            raise web.HTTPNotFound()
        return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})

    app = web.Application()
    app.router.add_get("/code/{id}.png", code)
    return app


async def run(cfg: dict) -> None:
    token = ""
    if os.environ.get("HA_TOKEN_FILE"):
        try:
            token = open(os.environ["HA_TOKEN_FILE"], encoding="utf-8").read().strip()
        except OSError as err:
            LOG.warning("no Home Assistant token (%s): dashboards and pictures stay off", err)
    trust = None
    if cfg.get("ca_file") and cfg.get("ssh_key_file"):
        trust = trust_from(cfg)
    ssl_context = ssl.create_default_context(cafile=cfg["ca_file"]) if cfg.get("ca_file") else None
    codes: dict[str, Echo] = {}
    base = cfg.get("code_base") or ""
    echoes = [Echo(d, cfg.get("people") or {}, cfg.get("beacons") or {}, cfg["router_url"], token, codes, base,
                   trust) for d in cfg["devices"]]
    runner = None
    if base and cfg.get("code_port"):
        runner = web.AppRunner(code_app(codes))
        await runner.setup()
        await web.TCPSite(runner, cfg.get("code_host") or "0.0.0.0", int(cfg["code_port"])).start()
    async with aiohttp.ClientSession() as session:
        for echo, device in zip(echoes, cfg["devices"], strict=True):
            if token and device.get("ha_url"):
                echo.ha = Ha(device["ha_url"], token, session, ssl_context)
        tasks = [t for e in echoes for t in (asyncio.create_task(e.device_loop()),
                                              asyncio.create_task(e.router_loop(session)),
                                              asyncio.create_task(e.tick_loop()))]
        try:
            await asyncio.gather(*tasks)
        finally:
            if runner is not None:
                await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open(os.environ["CONFIG_FILE"], encoding="utf-8") as f:
        cfg = json.load(f)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
