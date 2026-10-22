"""Messages the mapper sends to the assistant router's body socket.

The mapper is a `face-source` client (not a body): one connection per room. It
sends a `hello` naming the room, then the room's full current set of recognised
people whenever it changes. The router replaces the room's faces on every
message, so a person who left simply isn't in the next set. Each face carries
the Frigate event it was seen in, when known: the router's sightings link to
its thumbnail. A face matched to no one is sent as an event id under "unknown":
the router gives that person a name of their own (assistant_router/unknowns.py).
"""
from __future__ import annotations

PROTO = 1


def hello(room: str) -> dict:
    return {"hello": {"room": room, "kind": "face-source", "proto": PROTO}}


def faces(faces: tuple, events: dict | None = None, unknown: tuple = ()) -> dict:
    """A (person, confidence) snapshot -> the router's face-source payload."""
    events = events or {}
    return {"faces": [{"person": p, "confidence": c, **({"event": events[p]} if p in events else {})}
                      for p, c in faces],
            **({"unknown": [{"event": e} for e in unknown]} if unknown else {})}
