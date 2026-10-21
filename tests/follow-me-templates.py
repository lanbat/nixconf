"""Render follow-me's generated Home Assistant templates against fake states.

Usage: follow-me-templates.py GENERATED.json

GENERATED.json is {"template": [...], "unlocatedMinutes": 20, "darkLux": 30, ...}
as tests/follow-me.nix writes it: the `template:` block of the Home Assistant
configuration, for rooms Office (a light-level sensor) and Kitchen (none), people
alex and sam. The templates are rendered with Home Assistant's own template engine,
so a syntax or semantics mistake fails here and not on the server.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
import tempfile
import traceback
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

GEN = json.load(open(sys.argv[1]))
ENTITIES = {e["name"]: e for block in GEN["template"] for kind in ("binary_sensor", "sensor") for e in block.get(kind, [])}
UNKNOWN = ("unknown", "unavailable")
WHERE = {"alex": "sensor.lanbat_people_where_alex", "sam": "sensor.lanbat_people_where_sam"}
LAST = {"alex": "input_text.followme_lastroom_alex", "sam": "input_text.followme_lastroom_sam"}


def at(hhmm: str, plus_minutes: int = 0) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(2026, 10, 21, h, m, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=plus_minutes)


async def render(name: str, states: dict, now: dt.datetime | None = None) -> tuple[str, bool]:
    """(state text, available) of the template entity called `name`, with `states` set."""
    entity = ENTITIES[name]
    with tempfile.TemporaryDirectory() as cfg:
        hass = HomeAssistant(cfg)
        hass.config.time_zone = "UTC"
        for entity_id, state in states.items():
            if isinstance(state, tuple):
                hass.states.async_set(entity_id, state[0], state[1])
            else:
                hass.states.async_set(entity_id, state)
        real_now = dt_util.now
        if now is not None:
            dt_util.now = lambda time_zone=None: now
        try:
            out = Template(entity["state"], hass).async_render(parse_result=False).strip()
            available = Template(entity["availability"], hass).async_render(parse_result=False).strip() == "True" \
                if "availability" in entity else True
        finally:
            dt_util.now = real_now
            await hass.async_stop(force=True)
        return out, available


def sun(elevation: float) -> dict:
    return {"sun.sun": ("above_horizon", {"elevation": elevation})}


async def t_daypart_evening_night_day() -> None:
    cases = {"03:00": "night", "06:29": "night", "06:30": "day", "12:00": "day", "16:59": "day",
             "17:00": "evening", "20:00": "evening", "22:29": "evening", "22:30": "night", "23:59": "night"}
    for hhmm, want in cases.items():
        got, _ = await render("followme daypart", {}, at(hhmm))
        assert got == want, f"{hhmm}: {got!r}, wanted {want!r}"


async def t_dark_by_illuminance() -> None:
    for lux, want in (("5", "True"), ("29", "True"), ("30", "False"), ("400", "False")):
        got, ok = await render("followme Office dark", {"sensor.office_lux": lux, **sun(60)})
        assert ok and got == want, f"{lux} lx: {got!r}, wanted {want!r}"


async def t_dark_by_sun_when_no_sensor() -> None:
    for elevation, want in ((-10, "True"), (-1.5, "True"), (-1, "False"), (30, "False")):
        got, ok = await render("followme Kitchen dark", sun(elevation))
        assert ok and got == want, f"{elevation} deg: {got!r}, wanted {want!r}"
    _, ok = await render("followme Kitchen dark", {})
    assert not ok, "no sun entity must make the sensor unavailable, not dark or light"


async def t_dark_falls_back_to_sun_when_sensor_unavailable() -> None:
    got, ok = await render("followme Office dark", {"sensor.office_lux": "unavailable", **sun(-20)})
    assert ok and got == "True", (got, ok)
    got, ok = await render("followme Office dark", {"sensor.office_lux": "unavailable", **sun(40)})
    assert ok and got == "False", (got, ok)


async def t_occupied_any_tracked_person() -> None:
    base = {LAST["alex"]: "", LAST["sam"]: ""}
    got, ok = await render("followme Office occupied", {**base, WHERE["alex"]: "Office", WHERE["sam"]: "Kitchen"})
    assert ok and got == "True", (got, ok)
    got, ok = await render("followme Kitchen occupied", {**base, WHERE["alex"]: "Office", WHERE["sam"]: "Office"})
    assert ok and got == "False", (got, ok)
    got, ok = await render("followme Office occupied", {**base, WHERE["alex"]: "Kitchen", WHERE["sam"]: "Kitchen"})
    assert ok and got == "False", (got, ok)


async def t_occupied_stays_while_one_of_two_leaves() -> None:
    base = {LAST["alex"]: "Office", LAST["sam"]: "Office"}
    both = {**base, WHERE["alex"]: "Office", WHERE["sam"]: "Office"}
    alex_left = {**base, WHERE["alex"]: "Kitchen", WHERE["sam"]: "Office"}
    alex_gap = {**base, WHERE["alex"]: "home", WHERE["sam"]: "Office"}
    for name, states in (("both there", both), ("one left", alex_left), ("one in a gap", alex_gap)):
        got, ok = await render("followme Office occupied", states)
        assert ok and got == "True", f"{name}: the room is still in use, got {got!r}"
    got, _ = await render("followme Office occupied", {**base, WHERE["alex"]: "Kitchen", WHERE["sam"]: "Kitchen"})
    assert got == "False", "both gone: empty"


async def t_occupied_home_gap_keeps_last_room() -> None:
    states = {WHERE["alex"]: "home", LAST["alex"]: "Office", WHERE["sam"]: "away", LAST["sam"]: ""}
    got, ok = await render("followme Office occupied", states)
    assert ok and got == "True", f"a person at 'home' whose last room was Office keeps it occupied: {got!r}"
    got, _ = await render("followme Kitchen occupied", states)
    assert got == "False", got


async def t_occupied_home_gap_expires() -> None:
    states = {WHERE["alex"]: "home", LAST["alex"]: "Office", WHERE["sam"]: "away", LAST["sam"]: ""}
    soon = dt_util.now() + dt.timedelta(minutes=GEN["unlocatedMinutes"] - 2)
    late = dt_util.now() + dt.timedelta(minutes=GEN["unlocatedMinutes"] + 2)
    got, _ = await render("followme Office occupied", states, soon)
    assert got == "True", f"inside unlocatedMinutes: {got!r}"
    got, _ = await render("followme Office occupied", states, late)
    assert got == "False", f"after unlocatedMinutes: {got!r}"


async def t_occupied_away_frees_room() -> None:
    states = {WHERE["alex"]: "away", LAST["alex"]: "Office", WHERE["sam"]: "away", LAST["sam"]: "Office"}
    got, ok = await render("followme Office occupied", states)
    assert ok and got == "False", (got, ok)


async def t_occupied_unavailable_not_off() -> None:
    for state in UNKNOWN:
        _, ok = await render("followme Office occupied",
                             {WHERE["alex"]: state, LAST["alex"]: "Office", WHERE["sam"]: state, LAST["sam"]: ""})
        assert not ok, f"every sensor {state}: the occupied sensor must be unavailable, never 'off'"
    got, ok = await render("followme Office occupied",
                           {WHERE["alex"]: "unavailable", LAST["alex"]: "", WHERE["sam"]: "Kitchen", LAST["sam"]: ""})
    assert ok and got == "False", "one sensor still reporting is enough to know"


def automation(auto_id: str) -> dict:
    return next(a for a in GEN["automation"] if a.get("id") == auto_id)


async def eval_template(text: str, variables: dict, now: dt.datetime | None = None) -> str:
    with tempfile.TemporaryDirectory() as cfg:
        hass = HomeAssistant(cfg)
        hass.config.time_zone = "UTC"
        real_now = dt_util.now
        if now is not None:
            dt_util.now = lambda time_zone=None: now
        try:
            return Template(text, hass).async_render(variables, parse_result=False).strip()
        finally:
            dt_util.now = real_now
            await hass.async_stop(force=True)


async def t_music_room_slug_matches_the_nix_slug() -> None:
    expr = automation("followme_music_follow_alex")["variables"]["src_slug"]
    for room, want in (("Bedroom 1", "bedroom_1"), ("Office", "office"), ("Living  Room!", "living_room")):
        got = await eval_template(expr, {"src": room})
        assert got == want, f"{room!r}: {got!r}, wanted {want!r}"


async def t_music_cooldown() -> None:
    cond = next(c for c in automation("followme_music_follow_alex")["conditions"]
                if "last_triggered" in json.dumps(c))
    text = cond["value_template"]
    cooldown = GEN["followCooldownSeconds"]
    now = at("20:00")
    never = SimpleNamespace(attributes={"last_triggered": None})
    recent = SimpleNamespace(attributes={"last_triggered": now - dt.timedelta(seconds=cooldown - 5)})
    old = SimpleNamespace(attributes={"last_triggered": now - dt.timedelta(seconds=cooldown + 5)})
    for name, this, want in (("never", never, "True"), ("recent", recent, "False"), ("old", old, "True")):
        got = await eval_template(text, {"this": this}, now)
        assert got == want, f"{name}: {got!r}, wanted {want!r}"


async def t_ignores_untracked_people() -> None:
    states = {WHERE["alex"]: "Kitchen", LAST["alex"]: "", WHERE["sam"]: "Kitchen", LAST["sam"]: "",
              "sensor.lanbat_people_where_guest": "Office"}
    got, _ = await render("followme Office occupied", states)
    assert got == "False", f"a person not in the tracked list must not occupy a room: {got!r}"


async def main() -> int:
    failed = 0
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("t_")):
        try:
            await fn()
            print(f"ok   {name[2:]}")
        except Exception:
            failed += 1
            print(f"FAIL {name[2:]}\n{traceback.format_exc()}")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
