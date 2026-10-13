"""assistant-router --port 8092 --local-url ... --cloud-url ... --mode local-first
                 [--body-port 8770 --body-host 0.0.0.0 --persona-file persona.txt]"""
from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import web

import json

from .body import Bodies
from .frigate import Frigate
from .people import People
from .voice import VoiceIdClient
from .people_store import PeopleStore
from .server import Config, make_app, make_body_app


async def serve(cfg: Config, a: argparse.Namespace) -> None:
    persona = ""
    if a.persona_file:
        with open(a.persona_file, encoding="utf-8") as f:
            persona = f.read()
    bodies = Bodies(persona)
    names = {}
    if a.people_file:
        with open(a.people_file, encoding="utf-8") as f:
            names = {str(k): str(v) for k, v in json.load(f).items()}
    people = People(names)
    people_store = PeopleStore(people, a.people_state) if a.people_state else None
    frigate = Frigate(a.frigate_url) if a.frigate_url else None
    voice = VoiceIdClient(a.voice_id_url) if a.voice_id_url else None
    runners = [web.AppRunner(make_app(cfg, bodies=bodies, people=people, people_store=people_store, frigate=frigate,
                                      capture_face=bodies.capture, voice=voice))]
    await runners[0].setup()
    await web.TCPSite(runners[0], a.host, a.port).start()
    if a.body_port:
        runners.append(web.AppRunner(make_body_app(bodies, people=people, frigate=frigate, voice=voice)))
        await runners[1].setup()
        await web.TCPSite(runners[1], a.body_host, a.body_port).start()
    try:
        await asyncio.Event().wait()
    finally:
        for runner in runners:
            await runner.cleanup()


def config(argv: list[str] | None = None) -> tuple[Config, argparse.Namespace]:
    p = argparse.ArgumentParser(prog="assistant-router")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8092)
    p.add_argument("--local-url", required=True)
    p.add_argument("--local-model", required=True)
    p.add_argument("--cloud-url", required=True)
    p.add_argument("--cloud-model", default="smart")
    p.add_argument("--mode", choices=["local-first", "cloud-first", "local-only"], default="local-first")
    p.add_argument("--local-timeout", type=float, default=2.5,
                   help="seconds the local model may take before the request goes to the cloud")
    p.add_argument("--log-dir")
    p.add_argument("--log-days", type=int, default=14)
    p.add_argument("--no-log-text", action="store_true", help="log tier, route and timing, not what was said")
    p.add_argument("--body-host", default="127.0.0.1", help="where robot bodies connect (assistant_router/body.py)")
    p.add_argument("--body-port", type=int, default=0, help="0: no bodies")
    p.add_argument("--persona-file", help="who the assistant is when a body is in the room")
    p.add_argument("--people-file", help="JSON {key: name}: the people it may recognise (people.py)")
    p.add_argument("--people-state", help="JSON {key: name} in the state dir: people enrolled at runtime, merged over --people-file (people_store.py)")
    p.add_argument("--voice-id-url", help="voice-id's API (services/voice-id.nix): enrol, refine and forget voices")
    p.add_argument("--frigate-url", help="Frigate's base URL: the face library for enrolment (frigate.py); unset when Frigate is not on this host")
    a = p.parse_args(argv)
    cfg = Config(a.local_url, a.local_model, a.cloud_url, a.cloud_model, a.mode, local_timeout=a.local_timeout,
                 log_dir=a.log_dir, log_days=a.log_days, log_text=not a.no_log_text)
    return cfg, a


def main() -> None:
    cfg, a = config()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(serve(cfg, a))


if __name__ == "__main__":
    main()
