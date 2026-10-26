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
from .hass import Hass
from .household import Household
from .evidence import Evidence
from .sightings import Sightings
from .unknowns import Strangers, load_words
from .irks import Irks
from .lists import Lists, Personal
from .media import MediaWatch
from .people import People
from .voice import VoiceIdClient
from .youtube import YtShow
from .people_store import PeopleStore
from .ringer import Ringer
from .selfcheck import run as self_check
from .prefs import Prefs
from .mqtt import HomeAssistant, run_mqtt
from .server import Config, forget_person, make_app, make_body_app, screen_refresher
from .tap import Sessions, Taps, make_tap_app, session_secret


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
    household = None
    cards = Cards(a.cards_state)
    irks = Irks(a.irks_state) if a.irks_state else None
    sightings = Sightings(a.sightings_db, a.sightings_pictures, days=a.sightings_days,
                          picture_days=a.sightings_picture_days) if a.sightings_db else None
    evidence = None
    if a.evidence_db:
        evidence = Evidence(a.evidence_db, a.evidence_key, days=a.evidence_days)
        people.on_identified = evidence.person_seen
    strangers = None
    if a.unknown_people and people_store is not None and frigate is not None:
        adjectives, nouns = load_words(a.unknown_words_file)
        strangers = Strangers(people, people_store, frigate, sightings, adjectives=adjectives, nouns=nouns,
                              max_people=a.unknown_max)
    prefs = Prefs(a.prefs_state) if a.prefs_state else None
    hass = None
    if a.ha_url and a.ha_token_file:
        with open(a.ha_token_file, encoding="utf-8") as f:
            hass = Hass(a.ha_url, f.read().strip())
    media_rooms: dict[str, list[str]] = {}
    if a.media_rooms_file and hass is not None:
        with open(a.media_rooms_file, encoding="utf-8") as f:
            loaded_rooms = json.load(f)
        if isinstance(loaded_rooms, dict):
            media_rooms = {str(k): [str(p) for p in v] for k, v in loaded_rooms.items()
                           if isinstance(v, list) and v}
    lists = Lists(a.lists_state) if a.lists_state and hass is not None else None
    reminder_lists: dict[str, str] = {}
    if a.reminder_lists_file:
        with open(a.reminder_lists_file, encoding="utf-8") as f:
            loaded_lists = json.load(f)
        if isinstance(loaded_lists, dict):
            reminder_lists = {str(k): str(v) for k, v in loaded_lists.items()}
    personal = Personal(hass, lists, people.names, screen_refresher(bodies, people, time.monotonic),
                        reminder_lists) if lists is not None else None
    if a.tap_url:
        def remember(key: str, name: str) -> None:
            if people_store is not None:
                people_store.add(key, name)
            else:
                people.add(key, name)

        async def forget(key: str) -> bool:
            return await forget_person(key, people_store, voice, frigate, cards, sightings, prefs, lists)

        taps = Taps(a.tap_url, people, time.monotonic, phones_path=a.phones_state, remember=remember,
                    runtime=people_store.has if people_store is not None else None, store=people_store,
                    user_header=a.tap_user_header, name_header=a.tap_name_header,
                    learns=tuple(w for w, on in (("voice", voice), ("face", frigate)) if on is not None),
                    sessions=Sessions(session_secret(a.tap_session_key)), account_url=a.tap_account_url,
                    groups_header=a.tap_groups_header, admins=tuple(a.tap_admin_group or ()))
        taps.strangers = strangers
        if irks is not None:
            taps.forgetters.append(irks.forget)
        household = Household(people, people_store, taps=taps, voice=voice, frigate=frigate, cards=cards,
                              irks=irks, forget=forget, sightings=sightings, strangers=strangers, evidence=evidence,
                              prefs=prefs, lists=lists) if people_store is not None else None
    yt_show = YtShow(a.yt_show_url) if a.yt_show_url else None
    screen_rooms: tuple[str, ...] = ()
    if a.screen_rooms_file:
        with open(a.screen_rooms_file, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            screen_rooms = tuple(str(r) for r in loaded if isinstance(r, str) and r.strip())
    # The media results go to Home Assistant as MQTT sensors; the same object
    # runs the MQTT loop below, so a search can publish at once.
    ha_mqtt = HomeAssistant(bodies, people, time.monotonic, screen_rooms=screen_rooms) if a.mqtt_host else None
    runners = [web.AppRunner(make_app(cfg, bodies=bodies, people=people, people_store=people_store, frigate=frigate,
                                      capture_face=bodies.capture, voice=voice, cards=cards,
                                      forget_phones=taps.forget_phones if taps is not None else None,
                                      sightings=sightings, strangers=strangers, personal=personal,
                                      yt_show=yt_show, hass=hass, publisher=ha_mqtt))]
    await runners[0].setup()
    await web.TCPSite(runners[0], a.host, a.port).start()
    if a.body_port:
        runners.append(web.AppRunner(make_body_app(bodies, people=people, frigate=frigate, voice=voice,
                                                   cards=cards, taps=taps, irks=irks, sightings=sightings,
                                                   strangers=strangers, prefs=prefs, evidence=evidence)))
        await runners[-1].setup()
        await web.TCPSite(runners[-1], a.body_host, a.body_port).start()
    if taps is not None:
        runners.append(web.AppRunner(make_tap_app(taps, bodies.rooms, forget, household, prefs=prefs, hass=hass,
                                                personal=personal)))
        await runners[-1].setup()
        await web.TCPSite(runners[-1], "127.0.0.1", a.tap_port).start()
    mqtt = None
    if ha_mqtt is not None:
        password = None
        if a.mqtt_password_file:
            with open(a.mqtt_password_file, encoding="utf-8") as f:
                password = f.read().strip()
        mqtt = asyncio.create_task(run_mqtt(ha_mqtt, a.mqtt_host, a.mqtt_port, a.mqtt_user, password))
    greeter = asyncio.create_task(Greeter(bodies, people, time.monotonic).run()) \
        if a.body_port and not a.no_greet else None
    checked = asyncio.create_task(self_check(hass)) if hass is not None else None
    media = asyncio.create_task(MediaWatch(bodies, hass, media_rooms).run()) \
        if hass is not None and media_rooms else None
    ringer = asyncio.create_task(Ringer(bodies, people, personal, time.monotonic,
                                        state_path=a.rung_state).run()) \
        if a.body_port and personal is not None else None
    try:
        await asyncio.Event().wait()
    finally:
        for task in (mqtt, greeter, ringer, checked, media):
            if task is not None:
                task.cancel()
        if hass is not None:
            await hass.aclose()
        if yt_show is not None:
            await yt_show.aclose()
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
    p.add_argument("--tap-session-key", help="the key the page signs its cookies with, in the state dir "
                   "(made on first start); unset: new each start, so everyone signs in again")
    p.add_argument("--tap-account-url", help="where an account's name is changed (the provider's user "
                   "settings), linked from the page")
    p.add_argument("--tap-groups-header", help="the signed-in user's groups, set by the forward-auth "
                   "provider after its check (\"a|b\"); unset: no admin page")
    p.add_argument("--tap-admin-group", action="append",
                   help="a group whose members see /admin, People Nabu knows (household.py); repeatable")
    p.add_argument("--phones-state", help="JSON {mac: key} in the state dir: phones learned from taps (tap.py)")
    p.add_argument("--irks-state", help="JSON {key: [irk]} in the state dir: phones' Bluetooth keys, for the "
                   "room sensors (irks.py)")
    p.add_argument("--sightings-db", help="SQLite in the state dir: who was seen where (sightings.py); unset: none")
    p.add_argument("--sightings-pictures", help="where an indoor sighting's frame is kept: a directory on the "
                   "encrypted workload layer; while it is absent, none are kept")
    p.add_argument("--unknown-people", action="store_true",
                   help="give each face matched to no one a person of its own, Unknown <Word> <Word> (unknowns.py)")
    p.add_argument("--unknown-words-file", help="JSON {\"adjectives\": [...], \"nouns\": [...]} replacing the built-in words")
    p.add_argument("--unknown-max", type=int, default=500, help="no new Unknowns past this many (none are dropped)")
    p.add_argument("--evidence-db", help="SQLite in the state dir: which devices arrive with whom (evidence.py); unset: none")
    p.add_argument("--evidence-key", help="the key that hashes the addresses of unclaimed devices (made on first start)")
    p.add_argument("--evidence-days", type=float, default=30.0, help="how long an unclaimed device is kept")
    p.add_argument("--sightings-days", type=float, default=30.0, help="how long sightings are kept")
    p.add_argument("--sightings-picture-days", type=float, default=7.0, help="how long their pictures are kept")
    p.add_argument("--mqtt-host", help="the MQTT broker: publish the robots and who is around to Home Assistant (mqtt.py)")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-user")
    p.add_argument("--mqtt-password-file", help="the broker password, one line (a systemd credential)")
    p.add_argument("--screen-rooms-file", help="JSON list of rooms with techo5 screens (mqtt.py item sensors)")
    p.add_argument("--media-rooms-file", help="JSON {room: [media_player entity]}: quiet a robot while its "
                   "room's media plays (media.py)")
    p.add_argument("--ha-url", help="Home Assistant, on the loopback: people's own settings, lists and "
                   "reminders (hass.py); unset: none")
    p.add_argument("--ha-token-file", help="the voice satellites' Home Assistant token, one line (a systemd credential)")
    p.add_argument("--lists-state", help="JSON {key: [list]} in the state dir: the to-do lists people asked for "
                   "(lists.py), which home-assistant-post-setup makes in Home Assistant")
    p.add_argument("--reminder-lists-file", help="JSON {key: todo.entity}: a person's reminders list when their "
                   "profile names one other than todo.<key>_reminders (lists.py)")
    p.add_argument("--rung-state", help="JSON in the state dir: the reminders already shown on the robots, "
                   "and how often (ringer.py)")
    p.add_argument("--prefs-state", help="JSON {key: settings} in the state dir: the screen settings people "
                   "chose on the who page (prefs.py)")
    p.add_argument("--frigate-url", help="Frigate's base URL: the face library for enrolment (frigate.py); unset when Frigate is not on this host")
    p.add_argument("--yt-show-url", help="yt-show's API on the loopback (services/yt-show.nix): YouTube on a "
                   "room's screen (youtube.py); unset: no video")
    p.add_argument("--yt-play-service", help="esphome.<node>_play_video: the screen's video action, when more "
                   "than one screen and its name does not hold the room")
    p.add_argument("--media-players", help='JSON {room: speaker name}: the Music Assistant players audio '
                   "plays on, e.g. '{\"Kitchen\": \"Echo Show 8\"}'")
    a = p.parse_args(argv)
    media_players: dict[str, str] = {}
    if a.media_players:
        try:
            parsed = json.loads(a.media_players)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            media_players = {str(key).casefold(): str(value) for key, value in parsed.items()}
    cfg = Config(a.local_url, a.local_model, a.cloud_url, a.cloud_model, a.mode, local_timeout=a.local_timeout,
                 log_dir=a.log_dir, log_days=a.log_days, log_text=not a.no_log_text,
                 offer_to_meet=not a.no_offer_to_meet, yt_play_service=a.yt_play_service,
                 media_players=media_players)
    return cfg, a


def main() -> None:
    cfg, a = config()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(serve(cfg, a))


if __name__ == "__main__":
    main()
