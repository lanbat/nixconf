"""Quiet a robot while media plays in its room (docs/stackchan.md).

Every MEDIA_POLL_S, Home Assistant's media_player states are read. A room
whose players are playing -- music on the room's speakers, a film on its TV --
has its robot told to go quiet: still, head down, the screen dark, and still
reporting faces for person ID (the bridge's "quiet" act, firmware/stackchan).
When nothing in the room plays, the robot is told to come back.

The rooms and their players come from the deployment
(services/assistant-router.nix: the robot's host's Music Assistant player
media_player.<hostname>, and Kodi's media_player.kodi_<hostname>). Nothing is
logged unless a room's state changes.
"""
from __future__ import annotations

import asyncio
import json
import logging

from .body import Bodies
from .hass import Hass, HassError

LOG = logging.getLogger("assistant_router")

MEDIA_POLL_S = 5.0
PLAYING = frozenset({"playing", "buffering"})


class MediaWatch:
    def __init__(self, bodies: Bodies, hass: Hass, rooms: dict[str, list[str]],
                 poll_s: float = MEDIA_POLL_S) -> None:
        self.bodies = bodies
        self.hass = hass
        self.rooms = {str(k): [str(p) for p in v] for k, v in rooms.items() if k and v}
        self.poll_s = poll_s
        self._quiet: dict[str, bool] = {}  # room -> last told; logged on a change

    async def tick(self) -> None:
        try:
            states = {e["entity_id"]: e["state"] for e in await self.hass.states("media_player")}
        except HassError as err:
            LOG.warning(json.dumps({"media": "states", "error": str(err)}))
            return
        for room, players in self.rooms.items():
            playing = any(states.get(p) in PLAYING for p in players)
            if self._quiet.get(room) != playing:
                self._quiet[room] = playing
                LOG.info(json.dumps({"media": "quiet" if playing else "back", "room": room}))
            # Every tick, so a bridge that restarted gets it again.
            await self.bodies.act(room, {"quiet": playing})

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                await self.tick()
            except Exception as err:  # pylint: disable=broad-except
                LOG.warning(json.dumps({"media": "error", "error": str(err)}))
