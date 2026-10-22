"""People nobody has met yet get a name of their own: "Unknown Wobbly Noodle".

A face the library matches to no one used to be "nobody known", and two
strangers were indistinguishable. Now each distinct one is enrolled into
Frigate's face library under a generated key (unknown_wobbly_noodle), so the
next time that face turns up the library names them, and they are a person
like any other (the who page, sightings, Home Assistant, the admin page).
Their kind in the people store is "unknown".

They stay in the library, with their sightings and whatever else is learned,
until they say who they are ("Meeting Nabu", identity.py) or sign in with an
account (tap.py): the key then stays and only the name and kind change, so
the face pictures, sightings and Home Assistant entities stay where they are.
Nothing deletes an Unknown; only an explicit "forget" (or an admin) does, and
past `max_people` no new ones are made, rather than any being dropped.

An Unknown is never greeted by name, never counted as someone present (the
assistant must not talk to a stranger as if it knew them: People.strangers),
and a robot is told nobody it knows.

The words are mild on purpose and a site may replace them (--unknown-words-file).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Callable

from .frigate import Frigate, FrigateError

LOG = logging.getLogger("assistant_router")

PREFIX = "unknown_"
KIND = "unknown"
# A word of a name: letters only, so that the key (frigate.safe_name, at most
# 40 characters: PREFIX, two words, an underscore and a number) is always a valid one.
WORD = re.compile(r"[A-Za-z]{2,14}")

ADJECTIVES = (
    "wobbly", "sneaky", "bouncy", "grumpy", "sleepy", "fluffy", "dapper", "cheeky", "jolly", "mighty",
    "soggy", "zesty", "plucky", "quirky", "snazzy", "giddy", "wacky", "peppy", "dizzy", "fuzzy",
    "crispy", "spicy", "bashful", "clumsy", "breezy", "chatty", "dramatic", "feisty", "frosty", "gleeful",
    "goofy", "jazzy", "lumpy", "moody", "nimble", "perky", "rowdy", "squeaky", "sparkly", "toasty",
    "twirly", "wiggly", "yodelling", "zippy", "bubbly", "cosmic", "dozy", "fancy", "glittery", "haughty",
)
NOUNS = (
    "noodle", "biscuit", "pancake", "walrus", "muffin", "pickle", "turnip", "badger", "waffle", "penguin",
    "dumpling", "wombat", "crumpet", "pretzel", "hedgehog", "potato", "llama", "gnome", "kumquat", "otter",
    "meatball", "narwhal", "pigeon", "sausage", "teapot", "walnut", "yak", "doughnut", "parsnip", "platypus",
    "tadpole", "trombone", "turnover", "whisk", "croissant", "banjo", "cabbage", "goblin", "kipper", "marmot",
    "nugget", "oyster", "pudding", "quokka", "radish", "sprout", "toad", "umbrella", "vole", "zucchini",
)


def words(given, defaults: tuple[str, ...]) -> tuple[str, ...]:
    """A site's word list when it has a usable one, else the built-in."""
    ok = tuple(dict.fromkeys(w.lower() for w in (given or ()) if isinstance(w, str) and WORD.fullmatch(w)))
    return ok or defaults


def load_words(path: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(adjectives, nouns) from a JSON file {"adjectives": [...], "nouns": [...]}."""
    data: dict = {}
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            LOG.warning("unknown words file %s unreadable; using the built-in words", path)
    data = data if isinstance(data, dict) else {}
    return words(data.get("adjectives"), ADJECTIVES), words(data.get("nouns"), NOUNS)


def make_name(taken: set[str] | dict, rng: random.Random, adjectives: tuple[str, ...] = ADJECTIVES,
              nouns: tuple[str, ...] = NOUNS) -> tuple[str, str]:
    """(key, display name), a combination nobody has: unknown_wobbly_noodle,
    "Unknown Wobbly Noodle". When the combinations run low, a number is added."""
    for _ in range(64):
        adjective, noun = rng.choice(adjectives), rng.choice(nouns)
        key = f"{PREFIX}{adjective}_{noun}"
        if key not in taken:
            return key, f"Unknown {adjective.capitalize()} {noun.capitalize()}"
    base, n = f"{PREFIX}{rng.choice(adjectives)}_{rng.choice(nouns)}", 2
    while f"{base}_{n}" in taken:
        n += 1
    suffix = f"_{n}"
    adjective, noun = base[len(PREFIX):].split("_")
    return f"{base}{suffix}", f"Unknown {adjective.capitalize()} {noun.capitalize()} {n}"


def _room(room: str) -> str:
    return (room or "").strip().casefold()


class Strangers:
    """Gives each unrecognised face a person of its own, and remembers who was
    last met in each room, so that what they say or sign in with next is theirs."""

    RECENT_S = 300.0   # a stranger met in a room is who is there for this long
    IMAGE_GAP_S = 8.0  # between two pictures added to one stranger
    MINT_GAP_S = 5.0   # between two new strangers (each enrolment makes Frigate retrain)
    MAX_IMAGES = 8     # pictures kept per stranger from this path

    def __init__(self, people, store, frigate: Frigate | None, sightings=None, clock: Callable[[], float] | None = None,
                 rng: random.Random | None = None, adjectives: tuple[str, ...] = ADJECTIVES,
                 nouns: tuple[str, ...] = NOUNS, max_people: int = 500) -> None:
        self.people = people
        self.store = store  # people_store.PeopleStore
        self.frigate = frigate
        self.sightings = sightings
        self.rng = rng or random.Random()
        self.adjectives, self.nouns = adjectives, nouns
        self.max_people = max_people
        self._clock = clock
        self._last: dict[str, tuple[str, float]] = {}  # room key -> (stranger, when)
        self._images: dict[str, tuple[int, float]] = {}  # stranger -> (pictures added, when last)
        self._minted_at = float("-inf")
        self._events: dict[str, None] = {}  # camera events already looked at (oldest first)
        self._lock = asyncio.Lock()

    def now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def recent(self, room: str, now: float | None = None) -> str | None:
        """The stranger last met in `room`, if recently and still one."""
        now = self.now() if now is None else now
        hit = self._last.get(_room(room))
        if hit and now - hit[1] <= self.RECENT_S and hit[0] in self.people.strangers:
            return hit[0]
        return None

    def touch(self, room: str, key: str, now: float | None = None) -> None:
        """`key`, a stranger, was seen in `room` again."""
        if key in self.people.strangers:
            self._last[_room(room)] = (key, self.now() if now is None else now)

    async def meet(self, room: str, image: bytes) -> str | None:
        """The stranger this unrecognised face is: whoever was just met in the
        room (a picture is added), else a new Unknown. None when no one could
        be made (the library refused the picture, or the limit is reached)."""
        if self.store is None or self.frigate is None or not image:
            return None
        async with self._lock:
            now = self.now()
            key = self.recent(room, now)
            if key is not None:
                await self._add_picture(key, image, now)
                self.touch(room, key, now)
                return key
            if len(self.people.strangers) >= self.max_people or now - self._minted_at < self.MINT_GAP_S:
                return None
            self._minted_at = now
            key, name = make_name(self.people.names, self.rng, self.adjectives, self.nouns)
            self.store.add(key, name)
            self.store.set_kind(key, KIND)
            try:
                await self.frigate.enroll(key, image)
            except FrigateError as err:
                self.store.remove(key)
                LOG.info(json.dumps({"unknown": "not enrolled", "room": room, "frigate": err.detail[:120]}))
                return None
            self._images[key] = (1, now)
            self.touch(room, key, now)
            LOG.info(json.dumps({"unknown": key, "name": name, "room": room}))
            return key

    async def _add_picture(self, key: str, image: bytes, now: float) -> None:
        count, last = self._images.get(key, (0, float("-inf")))
        if count >= self.MAX_IMAGES or now - last < self.IMAGE_GAP_S:
            return
        try:
            await self.frigate.register(key, image)
        except FrigateError:
            return
        self._images[key] = (count + 1, now)

    async def meet_event(self, room: str, event: str) -> str | None:
        """A Frigate camera's face matched to no one, by its event: the
        stranger it is, and a sighting. Each event is looked at once."""
        if self.frigate is None or not isinstance(event, str) or event in self._events:
            return None
        self._events[event] = None
        while len(self._events) > 512:
            self._events.pop(next(iter(self._events)))
        try:
            image = await self.frigate.thumbnail(event)
        except FrigateError:
            return None
        key = await self.meet(room, image)
        if key is not None and self.sightings is not None:
            self.sightings.record(room, key, "camera", None, event=event)
        return key

    def identify(self, key: str, name: str, kind: str) -> None:
        """The stranger `key` said who they are (kind "heard") or signed in
        ("account"): their name and kind change, and nothing else moves."""
        self.store.add(key, name)
        self.store.set_kind(key, kind)
        LOG.info(json.dumps({"unknown": "identified", "person": key, "as": kind}))
