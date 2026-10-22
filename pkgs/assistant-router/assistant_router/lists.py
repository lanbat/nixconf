"""People's own to-do lists, by voice and on the who page.

Each person has a list of their own, todo.<key>_todo, and any more they ask
for: "make a list called hardware" is todo.<key>_hardware. They are Home
Assistant's local_todo lists, which anyone with a login can open (docs/people.md);
the router only ever touches a list that is the speaker's own, never one named
by what was said, and never another person's.

A list Home Assistant doesn't have yet is made by home-assistant-post-setup
from this file, lists.json ({key: [slug]}, in the router's state directory): a
person's first list, or one they ask for, is written here and appears in about
a minute, after Home Assistant's brief restart. Nothing is deleted, here or in
Home Assistant: removing a list is an administrator's.

Home Assistant answers "add milk to my shopping list" itself when a list has
that name (prefer_local_intents); these commands are for "my list" and for
the lists that have a person's name in front. Parsed here, handled before the
gate, as identity.py's.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .hass import Hass, HassError
from .reminders import describe, item_due

DEFAULT = "todo"          # the slug of a person's own list: todo.<key>_todo
RESERVED = ("reminders",)  # the reminders list: items with a due time, never "a list"
MAX_LISTS = 12            # per person
SLUG = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_ITEM = 100            # characters of an item
READ_BACK = 6             # items read aloud before "and N more"

DONE = "\0"  # starts a reply that reports a change Home Assistant confirmed (stripped by handle)
NOT_SURE = "I'm not sure who you are, so I don't know whose list that is."
NO_HA = "I can't reach your lists right now."


def slug(name: str) -> str | None:
    """A list's slug from what was said: "To-do", "hardware store" -> "todo",
    "hardware_store"; None when nothing usable is left or it is reserved."""
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    if words and words[0] in ("the", "a", "my"):
        words = words[1:]
    if words in (["to", "do"], ["todo"]):
        return DEFAULT
    out = "_".join(words)[:24].strip("_")
    return out if SLUG.match(out) and out not in RESERVED else None


def entity(key: str, which: str) -> str:
    return f"todo.{key}_{which}"


class Lists:
    """The lists people asked for, beyond their own: {key: [slug]}."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.lists: dict[str, list[str]] = {}
        self.listeners: list[Callable[[str], Any]] = []
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.lists = {str(k): [s for s in v if isinstance(s, str) and SLUG.match(s)][:MAX_LISTS]
                                  for k, v in data.items() if isinstance(v, list)}
            except (OSError, ValueError):
                pass

    def _save(self, key: str) -> None:
        if self.path:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.lists, f, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)
        for listener in self.listeners:
            listener(key)

    def slugs(self, key: str) -> list[str]:
        """`key`'s lists: their own first, then the ones they asked for (never the reminders
        list, which is made the same way but is not a list to add to by name)."""
        return [DEFAULT] + [s for s in self.lists.get(key, []) if s != DEFAULT and s not in RESERVED]

    def add(self, key: str, which: str) -> bool:
        """`key` has a list `which`: True when it is new here (and so to be made).
        False when they had it, or are at the limit."""
        have = self.lists.get(key, [])
        if which in have or len(have) >= MAX_LISTS:
            return False
        self.lists[key] = have + [which]
        self._save(key)
        return True

    def forget(self, key: str) -> bool:
        if self.lists.pop(key, None) is None:
            return False
        self._save(key)
        return True

    def rekey(self, key: str, into: str) -> bool:
        """`key`'s lists are `into`'s too (an admin linked the two): ones `into` hasn't yet
        are made for them; the Home Assistant lists of `key` stay where they are."""
        mine = self.lists.pop(key, None)
        if mine is None:
            return False
        have = self.lists.setdefault(into, [])
        for s in mine:
            if s not in have and len(have) < MAX_LISTS:
                have.append(s)
        self._save(into)
        return True


@dataclass
class Command:
    kind: str            # add | remove | read | make
    item: str = ""       # what to put on or take off
    which: str | None = None  # the list's name as said; None: their own


_NAME = r"(?:(?P<which>[a-z0-9' -]{1,30}?) )?"
_TAIL = r"(?: (?:for me|please|nabu))*$"
_POLITE = r"^(?:(?:hey |okay |ok )?(?:nabu )?)?(?:can you |could you |would you |please )*"
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("add", re.compile(_POLITE + r"(?:add|put) (?P<item>.+?) (?:to|on|onto|in|into) my " + _NAME + r"list" + _TAIL)),
    ("remove", re.compile(_POLITE + r"(?:take|remove|delete|cross|strike) (?P<item>.+?) "
                          r"(?:off|from|out of|off of) my " + _NAME + r"list" + _TAIL)),
    ("read", re.compile(_POLITE + r"(?:what(?:'s| is| are)? (?:on|in)|read(?: me)?|tell me what(?:'s| is) on) "
                        r"(?:my )?" + _NAME + r"list" + _TAIL)),
    ("make", re.compile(_POLITE + r"(?:make|create|start|set up) (?:me )?(?:a |another )?(?:new )?list "
                        r"(?:called|named) (?P<which>.{1,30}?)" + _TAIL)),
    ("make", re.compile(_POLITE + r"(?:make|create|start|set up) (?:me )?(?:a |another )new (?P<which>[a-z0-9' -]{1,30}?) list"
                        + _TAIL)),
]


def command(text: str) -> Command | None:
    """A list command in `text`, or None: it is not for the lists."""
    said = re.sub(r"[^a-z0-9' -]+", " ", (text or "").lower())
    said = " ".join(said.split())
    for kind, pattern in _PATTERNS:
        m = pattern.match(said)
        if m:
            g = m.groupdict()
            which = (g.get("which") or "").strip() or None
            if kind == "make" and which is None:
                continue
            # "my shopping list" names a list; "my list" is their own.
            return Command(kind, (g.get("item") or "").strip()[:MAX_ITEM], which)
    return None


def _spoken(items: list[str]) -> str:
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else items[0]


class Personal:
    """What a person's list command does, for the speaker `key`."""

    def __init__(self, hass: Hass, lists: Lists, people_names: dict[str, str],
                 told: Callable[[str], Any] | None = None) -> None:
        self.hass = hass
        self.lists = lists
        self.names = people_names
        self.told = told  # called with the key after a change: their screen reads again

    async def exists(self, key: str, which: str) -> bool | None:
        """Whether Home Assistant has `key`'s list `which`; None when it can't be asked."""
        try:
            states = await self.hass.states("todo")
        except HassError:
            return None
        return any(s["entity_id"] == entity(key, which) for s in states)

    async def items(self, key: str, which: str) -> list[dict] | None:
        """The open items of `key`'s list, [{"uid", "summary"}]; None when it can't be read."""
        try:
            got = await self.hass.call("todo", "get_items", {"entity_id": entity(key, which), "status": "needs_action"},
                                       response=True)
        except HassError:
            return None
        rows = (got.get(entity(key, which)) or {}).get("items") or []
        return [{"uid": str(r.get("uid") or ""), "summary": str(r.get("summary") or ""),
                 "due": str(r.get("due") or "")}
                for r in rows if isinstance(r, dict) and r.get("summary")]

    def find(self, key: str, said: str | None) -> str | None:
        """The slug of `key`'s list named `said` (None: their own), if they have one."""
        if said is None:
            return DEFAULT
        wanted = slug(said)
        return wanted if wanted in self.lists.slugs(key) else None

    async def handle(self, key: str | None, cmd: Command) -> tuple[str, bool]:
        """(the spoken reply, whether a change in Home Assistant was confirmed): the
        reply may say it changed something only then (honesty.py)."""
        text = await self._handle(key, cmd)
        return text.removeprefix(DONE), text.startswith(DONE)

    async def _handle(self, key: str | None, cmd: Command) -> str:
        if key is None or key not in self.names:
            return NOT_SURE
        if cmd.kind == "make":
            return self.make(key, cmd.which or "")
        which = self.find(key, cmd.which)
        if which is None:
            return f"You don't have a {cmd.which} list. Say \"make a list called {cmd.which}\"."
        have = await self.exists(key, which)
        if have is None:
            return NO_HA
        if not have:
            # Their own list is made the first time they use it, and a list they asked for is on its way.
            self.lists.add(key, which)
            return "I'm setting up that list. It will be ready in a minute or two; ask me again then."
        label = "your list" if which == DEFAULT else f"your {which.replace('_', ' ')} list"
        if cmd.kind == "add":
            if not cmd.item:
                return "What should I add?"
            try:
                await self.hass.call("todo", "add_item", {"entity_id": entity(key, which), "item": cmd.item})
            except HassError:
                return NO_HA
            self._told(key)
            return f"{DONE}Added {cmd.item} to {label}."
        rows = await self.items(key, which)
        if rows is None:
            return NO_HA
        if cmd.kind == "read":
            if not rows:
                return f"{label[0].upper()}{label[1:]} is empty."
            shown = [r["summary"] for r in rows[:READ_BACK]]
            more = len(rows) - len(shown)
            return f"On {label}: {_spoken(shown)}" + (f", and {more} more." if more else ".")
        # remove: the open item whose words match, never one that merely contains a part of them
        want = " ".join(cmd.item.lower().split())
        hit = next((r for r in rows if r["summary"].lower() == want), None) \
            or next((r for r in rows if want and want in r["summary"].lower()), None)
        if hit is None:
            return f"I can't find {cmd.item} on {label}."
        try:
            await self.hass.call("todo", "remove_item", {"entity_id": entity(key, which), "item": hit["uid"] or hit["summary"]})
        except HassError:
            return NO_HA
        self._told(key)
        return f"{DONE}Took {hit['summary']} off {label}."

    # What the who page does, on a list that is the person's own (`which` a slug of theirs).
    async def put(self, key: str, which: str, item: str) -> bool:
        item = " ".join((item or "").split())[:MAX_ITEM]
        if not item or which not in self.lists.slugs(key):
            return False
        try:
            await self.hass.call("todo", "add_item", {"entity_id": entity(key, which), "item": item})
        except HassError:
            return False
        self._told(key)
        return True

    async def _on_list(self, key: str, which: str, uid: str, service: str, data: dict) -> bool:
        """`service` on the open item `uid` of the person's list, when it is one."""
        if which not in (*self.lists.slugs(key), "reminders") \
                or not any(r["uid"] == uid for r in await self.items(key, which) or []):
            return False
        try:
            await self.hass.call("todo", service, {"entity_id": entity(key, which), "item": uid, **data})
        except HassError:
            return False
        self._told(key)
        return True

    async def complete(self, key: str, which: str, uid: str) -> bool:
        return await self._on_list(key, which, uid, "update_item", {"status": "completed"})

    async def drop(self, key: str, which: str, uid: str) -> bool:
        return await self._on_list(key, which, uid, "remove_item", {})

    async def remind(self, key: str | None, what: str, due: datetime, now: datetime) -> tuple[str, bool]:
        """(the spoken reply, whether it was set): `what` at `due` in `key`'s reminders list."""
        if key is None or key not in self.names:
            return NOT_SURE, False
        have = await self.exists(key, "reminders")
        if have is None:
            return NO_HA, False
        if not have:
            self.lists.add(key, "reminders")
            return "I'm setting up your reminders. It will be ready in a minute or two; ask me again then.", False
        try:
            await self.hass.call("todo", "add_item", {"entity_id": entity(key, "reminders"), "item": what,
                                                       "due_datetime": item_due(due)})
        except HassError:
            return NO_HA, False
        self._told(key)
        return f"OK, I'll remind you to {what} {describe(due, now)}.", True

    def make(self, key: str, said: str) -> str:
        wanted = slug(said)
        if wanted is None:
            return "I couldn't make a list name from that."
        if wanted == DEFAULT or wanted in self.lists.slugs(key):
            return f"You already have a {said.strip()} list."
        if not self.lists.add(key, wanted):
            return "That's a lot of lists already."
        return f"I'll have your {wanted.replace('_', ' ')} list ready in a minute."

    def _told(self, key: str) -> None:
        if self.told is not None:
            self.told(key)
