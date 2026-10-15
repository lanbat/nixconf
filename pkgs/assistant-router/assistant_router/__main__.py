"""assistant-router --port 8092 --local-url ... --cloud-url ... --mode local-first
                 [--body-port 8770 --body-host 0.0.0.0 --persona-file persona.txt]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from aiohttp import web

import json

from .body import Bodies
from .cards import Cards
from .frigate import Frigate
from .greet import Greeter
from .people import People
from .voice import VoiceIdClient
from .people_store import PeopleStore
from .mqtt import HomeAssistant, run_mqtt
from .server import Config, forget_person, make_app, make_body_app
from .tap import Taps, make_tap_app


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
    taps = None
    cards = Cards(a.cards_state)
    if a.tap_url:
        def remember(key: str, name: str) -> None:
            if people_store is not None:
                people_store.add(key, name)
            else:
                people.add(key, name)

        async def forget(key: str) -> bool:
            return await forget_person(key, people_store, voice, frigate, cards)

        taps = Taps(a.tap_url, people, time.monotonic, phones_path=a.phones_state, remember=remember,
                    runtime=people_store.has if people_store is not None else None, store=people_store,
                    user_header=a.tap_user_header, name_header=a.tap_name_header,
                    learns=tuple(w for w, on in (("voice", voice), ("face", frigate)) if on is not None))
    runners = [web.AppRunner(make_app(cfg, bodies=bodies, people=people, people_store=people_store, frigate=frigate,
                                      capture_face=bodies.capture, voice=voice, cards=cards,
                                      forget_phones=taps.forget_phones if taps is not None else None))]
    await runners[0].setup()
    await web.TCPSite(runners[0], a.host, a.port).start()
    if a.body_port:
        runners.append(web.AppRunner(make_body_app(bodies, people=people, frigate=frigate, voice=voice,
                                                   cards=cards, taps=taps)))
        await runners[-1].setup()
        await web.TCPSite(runners[-1], a.body_host, a.body_port).start()
    if taps is not None:
        runners.append(web.AppRunner(make_tap_app(taps, bodies.rooms, forget)))
        await runners[-1].setup()
        await web.TCPSite(runners[-1], "127.0.0.1", a.tap_port).start()
    mqtt = None
    if a.mqtt_host:
        password = None
        if a.mqtt_password_file:
            with open(a.mqtt_password_file, encoding="utf-8") as f:
                password = f.read().strip()
        ha = HomeAssistant(bodies, people, time.monotonic)
        mqtt = asyncio.create_task(run_mqtt(ha, a.mqtt_host, a.mqtt_port, a.mqtt_user, password))
    greeter = asyncio.create_task(Greeter(bodies, people, time.monotonic).run()) \
        if a.body_port and not a.no_greet else None
    try:
        await asyncio.Event().wait()
    finally:
        for task in (mqtt, greeter):
            if task is not None:
                task.cancel()
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
    p.add_argument("--no-offer-to-meet", action="store_true",
                   help="don't ask an unknown voice's name (identity.py, \"Meeting Nabu\")")
    p.add_argument("--no-greet", action="store_true",
                   help="the robots don't greet the people they recognise (greet.py)")
    p.add_argument("--body-host", default="127.0.0.1", help="where robot bodies connect (assistant_router/body.py)")
    p.add_argument("--body-port", type=int, default=0, help="0: no bodies")
    p.add_argument("--persona-file", help="who the assistant is when a body is in the room")
    p.add_argument("--people-file", help="JSON {key: name}: the people it may recognise (people.py)")
    p.add_argument("--people-state", help="JSON {key: name} in the state dir: people enrolled at runtime, merged over --people-file (people_store.py)")
    p.add_argument("--voice-id-url", help="voice-id's API (services/voice-id.nix): enrol, refine and forget voices")
    p.add_argument("--cards-state", help="JSON {UID: key} in the state dir: people's NFC cards, learned by "
                   "\"remember this card\" (cards.py)")
    p.add_argument("--tap-url", help="https://who.<domain>: the page a phone held to a robot opens (tap.py); unset: none")
    p.add_argument("--tap-port", type=int, default=8772, help="where the tap page listens, on the loopback, for Caddy")
    p.add_argument("--tap-user-header", default="X-Authentik-Username",
                   help="who signed in, set by the forward-auth provider after its check (Caddy drops a client's)")
    p.add_argument("--tap-name-header", help="their display name, likewise; unset: the username")
    p.add_argument("--phones-state", help="JSON {mac: key} in the state dir: phones learned from taps (tap.py)")
    p.add_argument("--mqtt-host", help="the MQTT broker: publish the robots and who is around to Home Assistant (mqtt.py)")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-user")
    p.add_argument("--mqtt-password-file", help="the broker password, one line (a systemd credential)")
    p.add_argument("--frigate-url", help="Frigate's base URL: the face library for enrolment (frigate.py); unset when Frigate is not on this host")
    a = p.parse_args(argv)
    cfg = Config(a.local_url, a.local_model, a.cloud_url, a.cloud_model, a.mode, local_timeout=a.local_timeout,
                 log_dir=a.log_dir, log_days=a.log_days, log_text=not a.no_log_text,
                 offer_to_meet=not a.no_offer_to_meet)
    return cfg, a


def main() -> None:
    cfg, a = config()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(serve(cfg, a))


if __name__ == "__main__":
    main()
