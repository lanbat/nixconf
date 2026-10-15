"""The robots greet the people they recognise (docs/stackchan.md).

Every few seconds, for each room with a robot: whoever People.here() places
there at GREET_MIN or more is recognised, by any source on its own (a tap or
a card, the robot's look, a clear voice, their phone in the room). Each is
greeted once as they arrive: someone already there at the last look isn't
arriving, and nobody is greeted in the same room twice in GREET_AGAIN_S.
Someone who has just spoken to the room's satellite is counted as greeted:
Nabu already talked to them by name. Not while the robot sleeps, and not in
the middle of a conversation: the greeting waits for its end.

The robot does the rest (the bridge's "greet" act): awake, happy, a wag and
a nod, three green breaths and "Hi <name>!". Home Assistant gets a
"recognized" event on the robot, with who and by what.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from .body import Bodies
from .people import People

LOG = logging.getLogger("assistant-router")

GREET_MIN = 0.8         # this sure they are in the room (People.presence)
GREET_AGAIN_S = 1800.0  # not twice in a room within this
ARRIVAL_S = 120.0       # gone this long, coming back is arriving
GREET_POLL_S = 5.0


def _key(room: str) -> str:
    return (room or "").strip().casefold()


def first_name(name: str) -> str:
    return name.split()[0] if name.split() else name


class Greeter:
    def __init__(self, bodies: Bodies, people: People, clock: Callable[[], float]) -> None:
        self.bodies = bodies
        self.people = people
        self.clock = clock
        self._started = clock()
        self._seen_at: dict[tuple[str, str], float] = {}     # (room, person) -> last look they were there
        self._arrived: dict[tuple[str, str], float] = {}     # arrivals not greeted yet (the robot was busy)
        self._greeted_at: dict[tuple[str, str], float] = {}  # (room, person) -> when they were greeted

    def due(self, now: float) -> list[tuple[str, str, list[str]]]:
        """(room, person key, the sources) of each greeting due now."""
        out = []
        for room in self.bodies.rooms():
            state = self.bodies.state(room)
            busy = state is None or state.asleep or state.night or (state.phase or "idle") != "idle"
            spoke, _, _ = self.people.last_speaker(room, now, within=GREET_AGAIN_S)
            for person, (sure, by) in self.people.here(room, now).items():
                if sure < GREET_MIN:
                    continue
                k = (_key(room), person)
                last = self._seen_at.get(k)
                self._seen_at[k] = now
                # There at the router's start, or at the last look: not arriving.
                if now - (self._started if last is None else last) >= ARRIVAL_S:
                    self._arrived[k] = now
                if k not in self._arrived or busy:
                    continue  # asleep, or talking: when it's over
                del self._arrived[k]
                if now - self._greeted_at.get(k, -GREET_AGAIN_S) < GREET_AGAIN_S:
                    continue
                self._greeted_at[k] = now
                if person != spoke:
                    out.append((room, person, by))
        for k in [k for k, at in self._arrived.items() if now - at > 600]:
            del self._arrived[k]  # gone before the robot was free
        for k in [k for k, at in self._seen_at.items() if now - at > 86400]:
            del self._seen_at[k]
            self._greeted_at.pop(k, None)
        return out

    async def tick(self) -> None:
        for room, person, by in self.due(self.clock()):
            name = self.people.names.get(person, person)
            await self.bodies.act(room, {"greet": {"name": first_name(name)}})
            self.bodies.announce(room, {"recognized": name, "how": by})
            LOG.info(json.dumps({"greeted": person, "room": room, "how": by}))

    async def run(self) -> None:
        while True:
            await asyncio.sleep(GREET_POLL_S)
            try:
                await self.tick()
            except Exception as err:  # pylint: disable=broad-except
                LOG.warning("greeting: %s", err)
