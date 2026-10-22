"""Whether Home Assistant lets the router do what the lists and reminders need.

Once after the router starts (and again, every RETRY_S, while Home Assistant
isn't answering yet), it asks Home Assistant for its entities, reads one to-do
list and, if there is one, reads an hour of one calendar, and logs one line:

    {"home_assistant": "ok", "todo": "ok", "calendar": "ok" | "none"}

or what was wrong: "refused" with the HTTP status (401: the token isn't accepted;
403: Home Assistant banned this address), "down", "error" with the status. It
reads only, and never writes: a first use of the lists proves the writes. The
client stops calling Home Assistant after a refusal (hass.py), so a bad token
costs one request, not a ban.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from .hass import ENTITY, Hass, HassError

LOG = logging.getLogger("assistant_router")

RETRY_S = 30.0
TRIES = 10


def _failed(what: str, err: HassError) -> dict:
    if err.status in (401, 403):
        return {what: "refused", "status": err.status}
    if err.status is None:
        return {what: "down"}
    return {what: "error", "status": err.status}


async def check(hass: Hass) -> dict:
    """The result line, without logging it."""
    try:
        states = await hass.states()
    except HassError as err:
        return _failed("home_assistant", err)
    out: dict = {"home_assistant": "ok", "entities": len(states)}
    todos = [s["entity_id"] for s in states if s["entity_id"].startswith("todo.") and ENTITY.match(s["entity_id"])]
    calendars = [s["entity_id"] for s in states if s["entity_id"].startswith("calendar.")]
    if todos:
        try:
            await hass.call("todo", "get_items", {"entity_id": todos[0], "status": "needs_action"}, response=True)
            out["todo"] = "ok"
        except HassError as err:
            out.update({"todo": "refused" if err.status in (401, 403) else "error", "status": err.status})
    else:
        out["todo"] = "none"  # no to-do list yet: they are made after the first deploy
    if calendars and not hass.refused:
        now = datetime.now().astimezone()
        try:
            await hass.call("calendar", "get_events", {"entity_id": calendars[0],
                                                       "start_date_time": now.isoformat(),
                                                       "end_date_time": (now + timedelta(hours=1)).isoformat()},
                            response=True)
            out["calendar"] = "ok"
        except HassError as err:
            out.update({"calendar": "refused" if err.status in (401, 403) else "error", "status": err.status})
    else:
        out["calendar"] = "none"
    return out


async def run(hass: Hass, retry_s: float = RETRY_S, tries: int = TRIES) -> dict:
    """Check, and again while Home Assistant is down, then log the last result."""
    out: dict = {}
    for attempt in range(tries):
        out = await check(hass)
        if out.get("home_assistant") != "down" or attempt == tries - 1:
            break
        await asyncio.sleep(retry_s)
    LOG.info(json.dumps(out))
    return out
