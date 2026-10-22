"""Whose device is this? Learned from what arrives together (docs/people.md, "Devices nobody has claimed").

Every person is already a profile that their voice, face, phones, cards and
login point at. What was missing is the devices nobody has claimed yet: a
phone on the home network that is on no one's list. This keeps a small record
of each, and learns whose it probably is from what arrives with it.

A device **arrives** when it turns up after being away (ten minutes). A person
arrives when something identifies them after the same absence: a face, a
voice, a tap, a card, a Bluetooth phone. If a device arrives within two
minutes of the same person's arrival, again and again, and almost never with
anyone else's, it is probably theirs. That is only ever a **suggestion**: an
admin confirms it (the device is then taught like a tapped phone), or ignores
it. A device that arrives with different people (a tablet, a visitor's phone)
is never suggested.

An unclaimed device is kept only as a keyed hash of its address, so the file
holds no addresses of strangers; the address itself lives in memory while the
device is on the network, and is kept only once an admin claims it. Unclaimed
devices are dropped after `days`.

Passive: it reads what the Wi-Fi sensor already hears. Nothing here probes a
device, and nothing links a device's changing random addresses to each other:
a phone with a private address per connection is a new device each time.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from typing import Callable

LOG = logging.getLogger("assistant_router")

GAP_S = 600.0       # away this long, and turning up again is an arrival
WINDOW_S = 120.0    # a person arriving this close to a device's arrival goes with it
MIN_ARRIVALS = 5    # a device must have arrived this often before anyone is suggested
MIN_TOGETHER = 4    # ... and arrived with the person this often
MIN_SCORE = 0.8     # of its arrivals, this share were with them
RUNNER_UP = 0.3     # and no one else was with it more often than this share
MAX_PENDING = 256   # arrivals waiting for the window to close
MAX_PEOPLE = 256

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def normalise_mac(text) -> str | None:
    hexes = re.sub(r"[^0-9a-f]", "", str(text).lower())
    if len(hexes) != 12:
        return None
    mac = ":".join(hexes[i:i + 2] for i in range(0, 12, 2))
    return mac if _MAC.match(mac) else None


def private_address(mac: str) -> bool:
    """Whether the address is a locally administered one: what a phone's private (random) Wi-Fi
    address is. Not an identity, only a hint of how to read the device."""
    return bool(int(mac[:2], 16) & 0x02)


class Evidence:
    def __init__(self, path: str | None = None, key_path: str | None = None, days: float = 30.0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.days = float(days)
        self._clock, self._wall = clock, wall
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        if path:
            os.chmod(path, 0o600)
        self._db.execute("CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, first REAL NOT NULL, "
                         "last REAL NOT NULL, arrivals INTEGER NOT NULL DEFAULT 0, private INTEGER NOT NULL DEFAULT 0, "
                         "person TEXT, claimed_by TEXT, ignored INTEGER NOT NULL DEFAULT 0)")
        self._db.execute("CREATE TABLE IF NOT EXISTS together (device TEXT NOT NULL, person TEXT NOT NULL, "
                         "n INTEGER NOT NULL, last REAL NOT NULL, PRIMARY KEY (device, person))")
        self._db.commit()
        self._key = self._load_key(key_path)
        self._raw: dict[str, str] = {}           # id -> the address, while it is on the network
        self._present: dict[str, float] = {}     # id -> when last heard (the router's clock)
        self._arrived: dict[str, float] = {}     # person -> when they last arrived (the router's clock)
        self._seen: dict[str, float] = {}        # person -> when last identified
        self._pending: list[tuple[str, float]] = []  # (device id, its arrival) waiting for the window
        self._pruned = 0.0

    @staticmethod
    def _load_key(path: str | None) -> bytes:
        if not path:
            return secrets.token_bytes(32)
        try:
            with open(path, "rb") as f:
                key = f.read()
            if len(key) >= 32:
                return key
        except OSError:
            pass
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    def device_id(self, mac: str) -> str:
        """The keyed hash a device is known by: the same address, the same id, and no way back."""
        return "wifi:" + hmac.new(self._key, mac.encode(), hashlib.sha256).hexdigest()[:20]

    # -- people ----------------------------------------------------------------

    def person_seen(self, person: str, now: float | None = None) -> None:
        """Something identified `person` (a face, a voice, a tap, a card, a phone): an arrival when
        they had been away. Not their Wi-Fi phone: that is the thing being learned."""
        now = self._clock() if now is None else now
        last = self._seen.get(person)
        if last is None or now - last >= GAP_S:
            self._arrived[person] = now
            if len(self._arrived) > MAX_PEOPLE:
                self._arrived.pop(next(iter(self._arrived)))
        self._seen[person] = now

    # -- devices ---------------------------------------------------------------

    def observe_devices(self, macs, now: float | None = None) -> None:
        """The addresses of the devices on the network that are no one's (the Wi-Fi sensor's
        report, once a minute)."""
        now, wall = self._clock() if now is None else now, self._wall()
        for mac in macs:
            m = normalise_mac(mac)
            if m is None:
                continue
            ident = self.device_id(m)
            row = self._db.execute("SELECT last, person FROM devices WHERE id = ?", (ident,)).fetchone()
            if row is None:
                self._db.execute("INSERT INTO devices (id, first, last, arrivals, private) VALUES (?,?,?,?,?)",
                                 (ident, wall, wall, 1, int(private_address(m))))
                self._arrival(ident, now)
            else:
                if row[1] is None and wall - row[0] >= GAP_S:
                    self._db.execute("UPDATE devices SET arrivals = arrivals + 1 WHERE id = ?", (ident,))
                    self._arrival(ident, now)
                self._db.execute("UPDATE devices SET last = ? WHERE id = ?", (wall, ident))
            self._raw[ident] = m
            self._present[ident] = now
        self._db.commit()
        self.resolve(now)
        if wall - self._pruned > 3600:
            self.prune(wall)

    def _arrival(self, ident: str, now: float) -> None:
        self._pending.append((ident, now))
        del self._pending[:-MAX_PENDING]

    def resolve(self, now: float | None = None) -> None:
        """Settle the arrivals whose window has closed: who arrived near each."""
        now = self._clock() if now is None else now
        waiting = []
        for ident, at in self._pending:
            if now - at < WINDOW_S:
                waiting.append((ident, at))
                continue
            with_them = [p for p, t in self._arrived.items() if abs(t - at) <= WINDOW_S]
            for person in with_them:
                self._db.execute("INSERT INTO together (device, person, n, last) VALUES (?,?,1,?) "
                                 "ON CONFLICT(device, person) DO UPDATE SET n = n + 1, last = excluded.last",
                                 (ident, person, self._wall()))
        self._pending = waiting
        self._db.commit()

    def prune(self, wall: float | None = None) -> int:
        """Drop unclaimed devices not heard for `days`."""
        wall = self._wall() if wall is None else wall
        self._pruned = wall
        old = [r[0] for r in self._db.execute("SELECT id FROM devices WHERE person IS NULL AND last < ?",
                                              (wall - self.days * 86400,))]
        for ident in old:
            self._db.execute("DELETE FROM together WHERE device = ?", (ident,))
            self._db.execute("DELETE FROM devices WHERE id = ?", (ident,))
            self._raw.pop(ident, None)
            self._present.pop(ident, None)
        self._db.commit()
        return len(old)

    # -- what an admin sees ----------------------------------------------------

    def devices(self) -> list[dict]:
        """The unclaimed devices, newest first, each with who it probably belongs to (or None)."""
        self.resolve()
        out = []
        rows = self._db.execute("SELECT id, first, last, arrivals, private, ignored FROM devices "
                                "WHERE person IS NULL ORDER BY last DESC").fetchall()
        for ident, first, last, arrivals, private, ignored in rows:
            who = self._likely(ident, arrivals)
            out.append({"id": ident, "short": ident[5:11], "first": first, "last": last, "arrivals": arrivals,
                        "private": bool(private), "ignored": bool(ignored), "likely": who,
                        "present": ident in self._present and self._clock() - self._present[ident] < GAP_S,
                        "known_address": ident in self._raw})
        return out

    def _likely(self, ident: str, arrivals: int) -> dict | None:
        """{"person", "score", "together"}: the one person this device arrives with, or None."""
        if arrivals < MIN_ARRIVALS:
            return None
        ranked = self._db.execute("SELECT person, n FROM together WHERE device = ? ORDER BY n DESC, person",
                                  (ident,)).fetchall()
        if not ranked:
            return None
        person, n = ranked[0]
        score = n / arrivals
        if n < MIN_TOGETHER or score < MIN_SCORE:
            return None
        if len(ranked) > 1 and ranked[1][1] / arrivals > RUNNER_UP:
            return None  # it arrives with someone else too: a shared device
        return {"person": person, "score": round(score, 2), "together": n}

    def suggestions(self) -> list[dict]:
        return [d for d in self.devices() if d["likely"] and not d["ignored"]]

    def claimable(self, ident: str) -> str | None:
        """The device's address, when it can be claimed now (it has been on the network since the
        router started), else None."""
        row = self._db.execute("SELECT person FROM devices WHERE id = ?", (ident,)).fetchone()
        return self._raw.get(ident) if row is not None and row[0] is None else None

    def claim(self, ident: str, person: str, by: str = "admin") -> str | None:
        """`ident` is `person`'s: the address to teach (a tapped phone is taught the same way), or
        None when it can't be claimed (unknown, already claimed, or not on the network lately)."""
        mac = self.claimable(ident)
        if mac is None:
            return None
        self._db.execute("UPDATE devices SET person = ?, claimed_by = ? WHERE id = ?", (person, by, ident))
        self._db.commit()
        LOG.info(json.dumps({"device_claimed": ident[5:11], "by": by}))
        return mac

    def ignore(self, ident: str) -> bool:
        cur = self._db.execute("UPDATE devices SET ignored = 1 WHERE id = ? AND person IS NULL", (ident,))
        self._db.commit()
        return cur.rowcount > 0

    def forget_person(self, person: str) -> None:
        """A person went: their arrivals and claims go with them."""
        self._db.execute("DELETE FROM together WHERE person = ?", (person,))
        self._db.execute("UPDATE devices SET person = NULL, claimed_by = NULL WHERE person = ?", (person,))
        self._db.commit()
        self._arrived.pop(person, None)
        self._seen.pop(person, None)

    def rekey(self, person: str, into: str) -> None:
        """`person` was merged into `into`: what arrived with them arrived with `into`."""
        for device, n, last in self._db.execute("SELECT device, n, last FROM together WHERE person = ?",
                                                (person,)).fetchall():
            self._db.execute("INSERT INTO together (device, person, n, last) VALUES (?,?,?,?) "
                             "ON CONFLICT(device, person) DO UPDATE SET n = n + excluded.n, last = excluded.last",
                             (device, into, n, last))
        self._db.execute("DELETE FROM together WHERE person = ?", (person,))
        self._db.execute("UPDATE devices SET person = ? WHERE person = ?", (into, person))
        self._db.commit()
        if person in self._arrived:
            self._arrived[into] = max(self._arrived.get(into, float("-inf")), self._arrived.pop(person))
        self._seen.pop(person, None)

    def shared(self, min_share: float = 0.5) -> list[dict]:
        """Pairs of people that the same unclaimed device arrives with (each at least `min_share` of
        its arrivals): likely one person met twice, an Unknown and who they are. Best first."""
        self.resolve()
        pairs: dict[tuple[str, str], float] = {}
        for ident, arrivals in self._db.execute("SELECT id, arrivals FROM devices WHERE person IS NULL "
                                                "AND ignored = 0 AND arrivals >= ?", (MIN_ARRIVALS,)):
            near = [(p, n / arrivals) for p, n in self._db.execute(
                "SELECT person, n FROM together WHERE device = ? AND n >= ?", (ident, MIN_TOGETHER))]
            near = [(p, s) for p, s in near if s >= min_share]
            for i, (a, sa) in enumerate(near):
                for b, sb in near[i + 1:]:
                    key = tuple(sorted((a, b)))
                    pairs[key] = max(pairs.get(key, 0.0), min(sa, sb))
        return [{"a": a, "b": b, "score": round(s, 2)} for (a, b), s in sorted(pairs.items(), key=lambda kv: -kv[1])]
