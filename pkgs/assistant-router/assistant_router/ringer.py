"""A due reminder, on the Stack-chan in the room where its person is alone.

The Echo Shows' bridge does this for a screen (pkgs/techo5-bridge); a robot has
no bridge reading lists, so the router does. Every RING_POLL_S, for each room
with a robot: when exactly one person is there, sure enough (the Greeter's
GREET_MIN), and the robot is awake and not in a conversation, their reminders
list (lists.Personal) is read, and the earliest open reminder that has come due
is shown: its words as the robot's caption, a happy mood and a perk, and the
notification light.

A robot has no chime, and its caption holds for 15 s, so the reminder is shown
again every REPEAT_S, RINGS times in all, while they stay alone. It stops when
they mark it done or remove it (it is no longer on the list), and a reminder
that comes due while they are elsewhere waits for them. Never for a bystander:
with anyone else in the room, nothing. What was shown is kept in a small JSON
file ({"<person> <uid> <due>": times shown}), so a restart doesn't start it over.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Callable

from .body import Bodies
from .greet import GREET_MIN
from .lists import Personal
from .people import People
from .reminders import parse_due

LOG = logging.getLogger("assistant-router")

RING_POLL_S = 30.0
REPEAT_S = 120.0
RINGS = 3
LIGHT = [255, 170, 0]  # the notification light, amber
CAPTION_MAX = 200      # the robot's own limit


def _key(room: str) -> str:
    return (room or "").strip().casefold()


class Ringer:
    def __init__(self, bodies: Bodies, people: People, personal: Personal, clock: Callable[[], float],
                 wall: Callable[[], datetime] | None = None, state_path: str | None = None) -> None:
        self.bodies = bodies
        self.people = people
        self.personal = personal
        self.clock = clock
        self.wall = wall or (lambda: datetime.now().astimezone())
        self.state_path = state_path
        self.shown: dict[str, int] = {}                   # "<uid> <due>" -> times shown
        self._last: dict[str, float] = {}                 # the same -> when, this run
        if state_path:
            try:
                with open(state_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.shown = {str(k): int(v) for k, v in data.items() if isinstance(v, int)}
            except (OSError, ValueError):
                pass

    def _save(self) -> None:
        if self.state_path:
            tmp = f"{self.state_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.shown, f, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.state_path)

    def alone(self, room: str, now: float) -> str | None:
        """The one person in `room`, when no one else is, and they are surely there."""
        here = self.people.here(room, now)
        if len(here) != 1:
            return None
        person, (sure, _) = next(iter(here.items()))
        return person if sure >= GREET_MIN and person in self.people.names else None

    def free(self, room: str) -> bool:
        state = self.bodies.state(room)
        return state is not None and not state.asleep and not state.night and (state.phase or "idle") == "idle"

    async def due(self, room: str, person: str, now: float) -> tuple[str, str] | None:
        """(the key, the words) of the reminder of `person` to show in `room` now, or None."""
        rows = await self.personal.items(person, "reminders")
        if rows is None:
            return None
        wall = self.wall()
        live: dict[str, str] = {}
        for r in rows:
            due = parse_due(r["due"], wall.tzinfo)
            if due is not None and due <= wall:
                live[f"{person} {r['uid']} {due.isoformat()}"] = r["summary"]
        # What of theirs is no longer an open, due reminder (done, removed, or moved) is forgotten.
        gone = [k for k in self.shown if k.startswith(f"{person} ") and k not in live]
        for k in gone:
            self.shown.pop(k, None)
            self._last.pop(k, None)
        if gone:
            self._save()
        for k, words in live.items():  # the earliest first: the list is in the order Home Assistant keeps
            if self.shown.get(k, 0) >= RINGS:
                continue
            if now - self._last.get(k, -REPEAT_S) < REPEAT_S:
                continue
            return k, words
        return None

    async def tick(self) -> None:
        now = self.clock()
        for room in self.bodies.rooms():
            if not self.bodies.is_robot(room) or not self.free(room):
                continue
            person = self.alone(room, now)
            if person is None:
                continue
            hit = await self.due(room, person, now)
            if hit is None:
                continue
            key, words = hit
            state = self.bodies.state(room)
            if state is not None and not state.present:
                await self.bodies.act(room, {"scan": True})  # known to be here: it looks round first
            shown = await self.bodies.act(room, {"mood": "happy", "gesture": "perk",
                                                 "caption": words[:CAPTION_MAX], "led": {"rgb": LIGHT}})
            if shown:
                self.shown[key] = self.shown.get(key, 0) + 1
                self._last[key] = now
                self._save()
                LOG.info(json.dumps({"reminder": "shown", "room": room, "person": person,
                                     "times": self.shown[key]}))

    async def run(self) -> None:
        while True:
            await asyncio.sleep(RING_POLL_S)
            try:
                await self.tick()
            except Exception as err:  # pylint: disable=broad-except
                LOG.warning("reminders: %s", err)
