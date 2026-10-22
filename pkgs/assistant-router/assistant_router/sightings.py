"""Sightings: who was seen where, kept so they can be searched.

A sighting is a face seen: when, where, who (or "nobody known"), by what (a
robot's or a screen's camera, or a Frigate camera through the person
mapper) and how sure. They come from every face check that found a face
(server.face_check: a robot's or a screen's frame) and from every face a
Frigate camera names as it comes into view (the person mapper's face-source).

They are kept `days` (settings.sightings.days) in a small SQLite file in the
router's state. Indoors, the frame that was checked is kept too, for
`picture_days`, in a directory on the encrypted workload layer (the
household's faces are personal data); while the layer is locked there is
nowhere to keep it, and the sighting is kept without one. A camera's sighting
keeps Frigate's event instead, whose thumbnail Frigate keeps under its own
retention.

Admins search them on the who page (/admin/sightings); "Forget me" and an
admin's "Forget" remove a person's, and a link moves them.
"""
from __future__ import annotations

import datetime
import os
import re
import secrets
import sqlite3
import time
from typing import Callable

SOURCES = ("robot", "screen", "camera")
_PICTURE = re.compile(r"[0-9]{10,12}-[0-9a-f]{8}\.jpg")
_EVENT = re.compile(r"[0-9]{6,14}\.[0-9]{1,8}-[a-z0-9]{1,16}")
MAX_PICTURE = 128 * 1024


class Sightings:
    def __init__(self, db_path: str | None = None, pictures: str | None = None, days: float = 30.0,
                 picture_days: float = 7.0, clock: Callable[[], float] = time.time) -> None:
        self.pictures = pictures
        self.days = days
        self.picture_days = picture_days
        self.clock = clock
        self._db = sqlite3.connect(db_path or ":memory:")
        if db_path:
            os.chmod(db_path, 0o600)
        self._db.execute("CREATE TABLE IF NOT EXISTS sightings (id INTEGER PRIMARY KEY, at REAL NOT NULL, "
                         "place TEXT NOT NULL, person TEXT, source TEXT NOT NULL, confidence REAL, "
                         "picture TEXT, event TEXT)")
        self._db.execute("CREATE INDEX IF NOT EXISTS sightings_at ON sightings(at)")
        self._db.commit()
        self._pruned = 0.0

    def _picture_dir(self) -> str | None:
        """Where pictures go: the workload layer's directory, while it is open."""
        d = self.pictures
        return d if d and os.path.isdir(d) and os.access(d, os.W_OK) else None

    def record(self, place: str, person: str | None, source: str, confidence: float | None = None,
               jpeg: bytes | None = None, event: str | None = None) -> int:
        """A face seen: the sighting's id."""
        if source not in SOURCES or not place:
            return 0
        now = self.clock()
        picture = None
        where = self._picture_dir()
        if jpeg and where and len(jpeg) <= MAX_PICTURE:
            picture = f"{int(now)}-{secrets.token_hex(4)}.jpg"
            fd = os.open(os.path.join(where, picture), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(fd, "wb") as f:
                f.write(jpeg)
        event = event if isinstance(event, str) and _EVENT.fullmatch(event) else None
        cur = self._db.execute(
            "INSERT INTO sightings (at, place, person, source, confidence, picture, event) VALUES (?,?,?,?,?,?,?)",
            (now, place[:64], person, source,
             round(float(confidence), 3) if type(confidence) in (int, float) else None, picture, event))
        self._db.commit()
        if now - self._pruned > 3600:
            self.prune()
        return int(cur.lastrowid)

    def _drop_picture(self, picture: str | None) -> None:
        where = self.pictures
        if picture and where and _PICTURE.fullmatch(picture):
            try:
                os.remove(os.path.join(where, picture))
            except OSError:
                pass

    def prune(self) -> None:
        """Pictures past picture_days, sightings past days."""
        now = self._pruned = self.clock()
        old = now - self.picture_days * 86400
        for (picture,) in self._db.execute("SELECT picture FROM sightings WHERE picture IS NOT NULL AND at < ?",
                                           (old,)).fetchall():
            self._drop_picture(picture)
        self._db.execute("UPDATE sightings SET picture = NULL WHERE picture IS NOT NULL AND at < ?", (old,))
        self._db.execute("DELETE FROM sightings WHERE at < ?", (now - self.days * 86400,))
        self._db.commit()

    def search(self, person: str | None = None, place: str | None = None, days: float | None = None,
               limit: int = 200) -> list[dict]:
        """The newest first: person "" for nobody known, None for anyone."""
        where, args = [], []
        if person == "":
            where.append("person IS NULL")
        elif person:
            where.append("person = ?")
            args.append(person)
        if place:
            where.append("place = ?")
            args.append(place)
        if days:
            where.append("at >= ?")
            args.append(self.clock() - days * 86400)
        sql = ("SELECT id, at, place, person, source, confidence, picture, event FROM sightings"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY at DESC, id DESC LIMIT ?")
        rows = self._db.execute(sql, (*args, max(1, min(int(limit), 1000)))).fetchall()
        return [{"id": i, "at": at, "when": datetime.datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M"),
                 "place": pl, "person": pe, "source": so, "confidence": co, "picture": pi is not None,
                 "event": ev} for i, at, pl, pe, so, co, pi, ev in rows]

    def places(self) -> list[str]:
        return [p for (p,) in self._db.execute("SELECT DISTINCT place FROM sightings ORDER BY place")]

    def picture(self, sighting: int) -> bytes | None:
        """A sighting's own picture, while it is kept and the layer is open."""
        row = self._db.execute("SELECT picture FROM sightings WHERE id = ?", (int(sighting),)).fetchone()
        where = self._picture_dir()
        if not row or not row[0] or not where or not _PICTURE.fullmatch(row[0]):
            return None
        try:
            with open(os.path.join(where, row[0]), "rb") as f:
                return f.read(MAX_PICTURE + 1)[:MAX_PICTURE]
        except OSError:
            return None

    def event(self, sighting: int) -> str | None:
        row = self._db.execute("SELECT event FROM sightings WHERE id = ?", (int(sighting),)).fetchone()
        return row[0] if row else None

    def get(self, sighting: int) -> dict | None:
        """One sighting, as search() gives it."""
        row = self._db.execute("SELECT id, at, place, person, source, confidence, picture, event FROM sightings "
                               "WHERE id = ?", (int(sighting),)).fetchone()
        if row is None:
            return None
        i, at, pl, pe, so, co, pi, ev = row
        return {"id": i, "at": at, "place": pl, "person": pe, "source": so, "confidence": co,
                "picture": pi is not None, "event": ev}

    def reassign(self, sighting: int, person: str | None) -> bool:
        """This one sighting is `person`'s (None: nobody known): an admin looked at the picture."""
        cur = self._db.execute("UPDATE sightings SET person = ? WHERE id = ?", (person, int(sighting)))
        self._db.commit()
        return cur.rowcount > 0

    def forget(self, person: str) -> int:
        rows = self._db.execute("SELECT picture FROM sightings WHERE person = ?", (person,)).fetchall()
        for (picture,) in rows:
            self._drop_picture(picture)
        self._db.execute("DELETE FROM sightings WHERE person = ?", (person,))
        self._db.commit()
        return len(rows)

    def rekey(self, person: str, into: str) -> int:
        cur = self._db.execute("UPDATE sightings SET person = ? WHERE person = ?", (into, person))
        self._db.commit()
        return cur.rowcount
