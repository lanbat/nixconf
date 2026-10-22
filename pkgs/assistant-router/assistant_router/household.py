"""The people Nabu knows, for the home's admins (the who page's /admin).

One list of everyone, each labelled with how Nabu knows their name
(people_store kinds):

- "Account": they signed in on the who page (kind "account"), or the
  profile names them. Read-only here: the name follows the login or the
  profile. Accounts are what the others can be linked to.
- "Guest": they typed their name on the page, with no account, or an admin
  fixed a name Nabu heard.
- "Heard": met by voice ("remember me", or Nabu asking), the name as heard:
  speech-to-text spells names its own way, so it waits for an admin.
- "Unknown": a face that matched no one, with a name Nabu made up
  (unknowns.py). An admin may give them their real name, or link them to an
  account; they also fix themselves by saying who they are or signing in.
- "Not recorded": met before kinds were kept; treated like a guest here.

For everyone but accounts, an admin may fix the name, forget them, or link
them to an account: everything Nabu learned about them (the voice, the face
images, phones by Wi-Fi and Bluetooth, cards) moves to that person, and they
are removed. A link can't be split apart again, so the page asks first.

The same person can turn up twice (an Unknown met under two names, or an
Unknown who is someone already known): an admin may merge anyone into anyone
who is at least as well identified (an Unknown is never the one that stays,
unless the other is one too). "Find duplicates" suggests such pairs, on
request: it asks the face library who each Unknown's later pictures look like.
An admin may also see a person's face pictures and delete single ones, and
say that one sighting was someone else.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable

from .frigate import FrigateError
from .tap import display_name

LOG = logging.getLogger("assistant_router")

LABELS = {"account": "Account", "guest": "Guest", "heard": "Heard", "unknown": "Unknown"}
ORDER = {"Account": 0, "Guest": 1, "Not recorded": 2, "Heard": 3, "Unknown": 4}

DUP_SCORE = 0.6      # how sure the library must be that an Unknown's picture is someone else
DUP_UNKNOWNS = 12    # Unknowns looked at in one search (each picture is a call to Frigate)
DUP_PICTURES = 3     # pictures tried per Unknown
DUP_CACHE_S = 600.0  # a search's answer is kept this long, so reloading the page costs nothing


class Household:
    def __init__(self, people, store, taps=None, voice=None, frigate=None, cards=None, irks=None,
                 forget: Callable[[str], Awaitable[bool]] | None = None, sightings=None,
                 prefs=None, lists=None, strangers=None, evidence=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.people = people
        self.store = store  # people_store.PeopleStore
        self.taps = taps
        self.voice = voice
        self.frigate = frigate
        self.cards = cards
        self.irks = irks
        self.sightings = sightings  # sightings.Sightings: searched on /admin/sightings
        self._forget = forget  # server.forget_person, bound to the stores (sightings too)
        self.prefs = prefs  # prefs.Prefs: their own screen settings, which follow a link
        self.lists = lists  # lists.Lists: the lists they asked for, likewise
        self.strangers = strangers  # unknowns.Strangers: told when an Unknown goes
        self.evidence = evidence  # evidence.Evidence: devices nobody has claimed, and whose they probably are
        self._clock = clock
        self._duplicates: tuple[float, list[dict]] | None = None  # (when, the last search's pairs)

    def label(self, key: str) -> str:
        if self.store is None or not self.store.has(key):
            return "Account"  # the profile's person
        return LABELS.get(self.store.kind(key) or "", "Not recorded")

    def editable(self, key: str) -> bool:
        return key in self.people.names and self.label(key) != "Account"

    async def everyone(self) -> list[dict]:
        """Everyone, accounts first, with what Nabu has learned of each."""
        prints = set(await self.voice.people() or []) if self.voice is not None else set()
        faces: dict[str, list[str]] = {}
        if self.frigate is not None:
            try:
                faces = await self.frigate.list()
            except FrigateError:
                pass
        phones = list((self.taps.phones if self.taps is not None else {}).values())
        bluetooth = self.irks.all() if self.irks is not None else {}
        cards = list((self.cards.cards if self.cards is not None else {}).values())
        out = []
        for key, name in self.people.names.items():
            learned = []
            if key in prints:
                learned.append("voice")
            if faces.get(key):
                n = len(faces[key])
                learned.append(f"{n} face picture{'s' if n != 1 else ''}")
            if key in phones:
                learned.append("Wi-Fi")
            if bluetooth.get(key):
                learned.append("Bluetooth")
            if key in cards:
                learned.append("card")
            label = self.label(key)
            out.append({"key": key, "name": name, "label": label, "editable": label != "Account",
                        "profile": self.store is None or not self.store.has(key), "learned": learned,
                        "pictures": len(faces.get(key) or [])})
        return sorted(out, key=lambda p: (ORDER[p["label"]], p["name"].casefold(), p["key"]))

    def rename(self, key: str, name: str) -> bool:
        """An admin typed the name: it is theirs now, confirmed (a guest)."""
        clean = display_name(name, "")
        if not clean or not self.editable(key) or self.store is None:
            return False
        self.store.add(key, clean)
        self.store.set_kind(key, "guest")
        LOG.info(json.dumps({"renamed": key, "by": "admin"}))
        return True

    async def forget(self, key: str) -> bool:
        if not self.editable(key) or self._forget is None:
            return False
        if self.taps is not None:
            self.taps.forget_phones(key)
        LOG.info(json.dumps({"forgotten": key, "by": "admin"}))
        if self.strangers is not None:
            self.strangers.rekey(key)
        if self.evidence is not None:
            self.evidence.forget_person(key)
        self._duplicates = None
        return await self._forget(key)

    def can_merge(self, key: str, into: str) -> bool:
        """Whether `key` may be merged into `into`: `key` is someone an admin may change, `into`
        is another person, and an Unknown is never the one that stays unless `key` is one too."""
        return key != into and self.editable(key) and into in self.people.names and self.store is not None \
            and (self.label(into) != "Unknown" or self.label(key) == "Unknown")

    async def link(self, key: str, into: str) -> list[str] | None:
        """`key` is `into` (anyone better identified): what moved, or None when it can't be."""
        if not self.can_merge(key, into):
            return None
        moved = []
        if self.voice is not None and await self.voice.merge(key, into):
            moved.append("voice")
        if self.frigate is not None:
            try:
                n = await self.frigate.move(key, into)
            except FrigateError:
                n = 0
            if n:
                moved.append(f"{n} face picture{'s' if n != 1 else ''}")
        if self.taps is not None and self.taps.rekey_phones(key, into):
            moved.append("Wi-Fi")
        if self.irks is not None and self.irks.rekey(key, into):
            moved.append("Bluetooth")
        if self.cards is not None and self.cards.rekey(key, into):
            moved.append("card")
        if self.sightings is not None and self.sightings.rekey(key, into):
            moved.append("sightings")
        if self.prefs is not None and self.prefs.rekey(key, into):
            moved.append("settings")
        if self.lists is not None and self.lists.rekey(key, into):
            moved.append("lists")
        self.store.relink(key, into)
        self.store.remove(key)
        if self.strangers is not None:
            self.strangers.rekey(key, into)
        if self.evidence is not None:
            self.evidence.rekey(key, into)
        self._duplicates = None
        LOG.info(json.dumps({"linked": key, "into": into, "moved": moved, "by": "admin"}))
        return moved

    # -- face pictures ---------------------------------------------------------

    async def pictures(self, key: str) -> list[str]:
        """The face pictures the library holds for `key` (their file names)."""
        if self.frigate is None or key not in self.people.names:
            return []
        try:
            return list((await self.frigate.list()).get(key) or [])
        except FrigateError:
            return []

    async def remove_picture(self, key: str, file: str) -> bool:
        """Delete one of `key`'s face pictures (it must be one the library lists for them)."""
        if self.frigate is None or not self.editable(key) or file not in await self.pictures(key):
            return False
        try:
            await self.frigate.delete(key, [file])
        except FrigateError:
            return False
        self._duplicates = None
        LOG.info(json.dumps({"picture_deleted": key, "by": "admin"}))
        return True

    # -- a sighting that was someone else --------------------------------------

    async def move_sighting(self, sighting: int, person: str | None, add_picture: bool = False) -> bool:
        """This sighting was `person` (None: nobody known). With `add_picture`, its picture also
        goes into their face pictures, which teaches the library what they look like."""
        if self.sightings is None or (person is not None and person not in self.people.names):
            return False
        found = self.sightings.get(sighting)
        if found is None:
            return False
        if add_picture and person is not None and self.frigate is not None:
            image = self.sightings.picture(sighting)
            if image is None and found["event"]:
                try:
                    image = await self.frigate.thumbnail(found["event"])
                except FrigateError:
                    image = None
            if image is None:
                return False
            try:
                await self.frigate.register(person, image)
            except FrigateError:
                return False
        ok = self.sightings.reassign(sighting, person)
        self._duplicates = None
        LOG.info(json.dumps({"sighting_moved": int(sighting), "to": person, "by": "admin"}))
        return ok

    # -- devices nobody has claimed ---------------------------------------------

    def claim_device(self, device: str, person: str) -> bool:
        """An admin said the device is `person`'s: it is taught like a tapped phone, so the Wi-Fi
        sensor counts it as theirs from now on."""
        if self.evidence is None or self.taps is None or person not in self.people.names:
            return False
        mac = self.evidence.claimable(device)
        if mac is None or not self.taps.learn(mac, person) or self.evidence.claim(device, person) is None:
            return False
        self._duplicates = None
        LOG.info(json.dumps({"device": "claimed", "for": person, "by": "admin"}))
        return True

    # -- duplicates ------------------------------------------------------------

    async def duplicates(self, fresh: bool = False) -> list[dict]:
        """Pairs that look like one person: an Unknown, and whoever the library says its later
        pictures are (best score first). Asked on request, not on a timer: each call is a
        round trip to Frigate, and the answer is kept a few minutes. The first sighting of an
        Unknown is the picture they were enrolled with, so it is not used."""
        now = self._clock()
        if not fresh and self._duplicates is not None and now - self._duplicates[0] < DUP_CACHE_S:
            return self._duplicates[1]
        found: dict[tuple[str, str], dict] = {}
        if self.frigate is not None and self.sightings is not None:
            unknowns = [k for k in self.people.strangers if k in self.people.names][:DUP_UNKNOWNS]
            for key in unknowns:
                later = self.sightings.search(person=key, limit=50)[:-1]  # newest first, the first dropped
                for s in [s for s in later if s["picture"] or s["event"]][:DUP_PICTURES]:
                    image = self.sightings.picture(s["id"])
                    try:
                        if image is None and s["event"]:
                            image = await self.frigate.thumbnail(s["event"])
                        hit = await self.frigate.recognize(image) if image else None
                    except FrigateError:
                        continue
                    other, score = (hit or {}).get("name"), (hit or {}).get("confidence")
                    if not other or other == key or other not in self.people.names \
                            or type(score) not in (int, float) or score < DUP_SCORE:
                        continue
                    best = found.get((key, other))
                    if best is None or score > best["score"]:
                        found[(key, other)] = {"key": key, "into": other, "score": round(float(score), 2),
                                               "sighting": s["id"], "place": s["place"]}
        if self.evidence is not None:
            # Two people that the same device arrives with: probably one person met twice.
            for shared in self.evidence.shared():
                a, b = shared["a"], shared["b"]
                for key, other in ((a, b), (b, a)):
                    if (key, other) not in found and self.can_merge(key, other):
                        found[(key, other)] = {"key": key, "into": other, "score": shared["score"],
                                               "sighting": None, "place": "", "why": "shares a device"}
                        break
        pairs = sorted((p for p in found.values() if self.can_merge(p["key"], p["into"])),
                       key=lambda p: -p["score"])
        self._duplicates = (now, pairs)
        return pairs
