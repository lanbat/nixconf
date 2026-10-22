"""The mapper's state: who is in each room, from Frigate's face events.

Frigate tracks a face per camera per event: it emits a `tracked_object_update`
while the face is present (name = the best face-library match, or none) and an
`events` `end` when it leaves. A room's people are the distinct named people
with a live face in that room's camera; a face with no match is counted as
unknown, and its event is passed on so the router can give it a name. When the last live event for a person ends, that
person falls out of the room on their own.

Who may be named: a registry of person keys, or, without one (None), anyone
the face library names (a person key's characters): the assistant router
knows its runtime people and drops names it doesn't.

Pure: the driver feeds it parsed events and reads snapshots; no I/O here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_KEY = re.compile(r"[A-Za-z0-9_-]{1,40}")


@dataclass
class _Active:
    person: str | None
    score: float


@dataclass(frozen=True)
class RoomSnapshot:
    faces: tuple  # (("person", confidence), ...) for named people, best score each
    unknown: int  # live faces with no match
    camera_online: bool
    events: dict = field(default_factory=dict, compare=False)  # person -> the event of their best face
    unknown_events: tuple = ()  # the Frigate events of those faces, for the router to name


class PersonState:
    def __init__(self, registry: set[str] | None, cameras: dict[str, str]) -> None:
        self.registry = set(registry) if registry is not None else None
        self.cameras = dict(cameras)  # camera -> room
        # camera -> event_id -> _Active
        self._active: dict[str, dict[str, _Active]] = {}
        self._online: dict[str, bool] = {}

    def _named(self, name) -> str | None:
        if not isinstance(name, str) or name == "unknown" or not _KEY.fullmatch(name):
            return None
        return name if self.registry is None or name in self.registry else None

    # -- events ---------------------------------------------------------------

    def on_face_update(self, camera: str, event_id: str, name, score: float) -> None:
        if camera not in self.cameras:
            return
        self._active.setdefault(camera, {})[event_id] = _Active(self._named(name), float(score))

    def on_event_end(self, camera: str, event_id: str) -> None:
        self._active.get(camera, {}).pop(event_id, None)

    def on_camera_status(self, camera: str, online: bool) -> None:
        if camera in self.cameras:
            self._online[camera] = online

    # -- read -----------------------------------------------------------------

    @property
    def rooms(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.cameras.values())))

    def _room_cameras(self, room: str) -> list[str]:
        return [c for c, r in self.cameras.items() if r == room]

    def snapshot(self, room: str) -> RoomSnapshot:
        best: dict[str, float] = {}
        events: dict[str, str] = {}
        unknown = 0
        unknown_events: list[str] = []
        for cam in self._room_cameras(room):
            for event_id, act in self._active.get(cam, {}).items():
                if act.person is None:
                    unknown += 1
                    unknown_events.append(event_id)
                elif act.score >= best.get(act.person, -1.0):
                    best[act.person] = act.score
                    events[act.person] = event_id
        faces = tuple((p, round(s, 3)) for p, s in sorted(best.items()))
        online = any(self._online.get(c, False) for c in self._room_cameras(room))
        return RoomSnapshot(faces=faces, unknown=unknown, unknown_events=tuple(sorted(unknown_events)[:8]),
                            camera_online=online, events=events)

    def all_snapshots(self) -> dict[str, RoomSnapshot]:
        return {room: self.snapshot(room) for room in self.rooms}
