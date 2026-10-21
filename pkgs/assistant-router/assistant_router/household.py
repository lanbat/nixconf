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
- "Not recorded": met before kinds were kept; treated like a guest here.

For everyone but accounts, an admin may fix the name, forget them, or link
them to an account: everything Nabu learned about them (the voice, the face
images, phones by Wi-Fi and Bluetooth, cards) moves to that person, and they
are removed. A link can't be split apart again, so the page asks first.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from .frigate import FrigateError
from .tap import display_name

LOG = logging.getLogger("assistant_router")

LABELS = {"account": "Account", "guest": "Guest", "heard": "Heard"}
ORDER = {"Account": 0, "Guest": 1, "Not recorded": 2, "Heard": 3}


class Household:
    def __init__(self, people, store, taps=None, voice=None, frigate=None, cards=None, irks=None,
                 forget: Callable[[str], Awaitable[bool]] | None = None, sightings=None) -> None:
        self.people = people
        self.store = store  # people_store.PeopleStore
        self.taps = taps
        self.voice = voice
        self.frigate = frigate
        self.cards = cards
        self.irks = irks
        self.sightings = sightings  # sightings.Sightings: searched on /admin/sightings
        self._forget = forget  # server.forget_person, bound to the stores (sightings too)

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
                        "profile": self.store is None or not self.store.has(key), "learned": learned})
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
        return await self._forget(key)

    async def link(self, key: str, into: str) -> list[str] | None:
        """`key` is `into` (an account): what moved, or None when it can't be."""
        if key == into or not self.editable(key) or into not in self.people.names \
                or self.label(into) != "Account" or self.store is None:
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
        self.store.relink(key, into)
        self.store.remove(key)
        LOG.info(json.dumps({"linked": key, "into": into, "moved": moved, "by": "admin"}))
        return moved
