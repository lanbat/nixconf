"""People's own screen settings, chosen on the who page ("My settings").

What a person picks for themselves, over what the profile says
(deployment.techo5Devices.<room>.profiles):

- calendars: the Home Assistant calendars of their "Next up" on a screen;
- reminder_calendar: the calendar their spoken reminders go in too;
- todo: the to-do list their screen shows;
- immich_person: whose photos the screen's slideshow shows, as Immich names them.

A screen is told them with who is in its room ({"here": [{..., "prefs"}]},
server.py), and uses them while that person is alone in front of it
(pkgs/techo5-bridge). Glance chips are not here: changing them makes TECHO5
drop every connection to it, Home Assistant's too, each time someone walks in.

Kept in a small JSON file in the router's state directory: {key: {...}}.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from .hass import ENTITY

MAX_CALENDARS = 8
# Told to a screen; reminder_calendar is the router's own.
SCREEN = ("calendars", "todo", "immich_person")


def _entity(value: Any, domain: str) -> str | None:
    return value if isinstance(value, str) and ENTITY.match(value) and value.startswith(domain + ".") else None


def clean(data: Any) -> dict:
    """The settings in `data` that are well formed; nothing else."""
    data = data if isinstance(data, dict) else {}
    out: dict[str, Any] = {}
    cals = [c for c in (data.get("calendars") or []) if _entity(c, "calendar")] \
        if isinstance(data.get("calendars"), list) else []
    if cals:
        out["calendars"] = list(dict.fromkeys(cals))[:MAX_CALENDARS]
    for key, domain in (("reminder_calendar", "calendar"), ("todo", "todo")):
        if _entity(data.get(key), domain):
            out[key] = data[key]
    immich = data.get("immich_person")
    if isinstance(immich, str) and immich.strip():
        out["immich_person"] = " ".join(immich.split())[:60]
    return out


class Prefs:
    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.prefs: dict[str, dict] = {}
        self.listeners: list[Callable[[str], Any]] = []
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.prefs = {str(k): c for k, v in data.items() if (c := clean(v))}
            except (OSError, ValueError):
                pass

    def _save(self, key: str) -> None:
        if self.path:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.prefs, f, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)
        for listener in self.listeners:
            listener(key)

    def get(self, key: str) -> dict:
        return dict(self.prefs.get(key, {}))

    def for_screen(self, key: str) -> dict:
        """What a screen uses of `key`'s settings."""
        return {k: v for k, v in self.prefs.get(key, {}).items() if k in SCREEN}

    def set(self, key: str, data: dict) -> dict:
        """`key`'s settings are `data` (what is well formed of it), all of them."""
        new = clean(data)
        if new == self.prefs.get(key, {}):
            return new
        if new:
            self.prefs[key] = new
        else:
            self.prefs.pop(key, None)
        self._save(key)
        return new

    def forget(self, key: str) -> bool:
        if self.prefs.pop(key, None) is None:
            return False
        self._save(key)
        return True

    def rekey(self, key: str, into: str) -> bool:
        """`key`'s settings are `into`'s, unless `into` has their own (an admin linked the two)."""
        mine = self.prefs.pop(key, None)
        if mine is None:
            return False
        if into not in self.prefs:
            self.prefs[into] = mine
        self._save(into)
        return True
