"""Runtime people: who was enrolled at runtime, kept across restarts.

The Nix people file (--people-file) is the base: who the profile says the
assistant may know, fixed at deploy time. A person met later is added here and
remembered in a small JSON file in the router's state directory. At startup the
two are merged, the overlay winning, so an enrolled person is recognised again.

Only the runtime-added people are written, never the whole set: dropping
someone from the Nix file still takes effect on the next start.

Aliases tie a login (an Authentik username, from a phone tap) to a person met
by voice before they had one, so the tap names the person Nabu already knows
instead of making a second one ("Meeting Nabu", docs/people.md). They are kept
next to the people, in `<name>-aliases.json`.
"""
from __future__ import annotations

import json
import os

from .people import People


def _load(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _persist(path: str, overlay: dict[str, str]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(overlay, f, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


class PeopleStore:
    def __init__(self, people: People, path: str) -> None:
        self.people = people
        self.path = path
        self._overlay = _load(path)
        for key, name in self._overlay.items():
            people.names[key] = name
        self.aliases_path = os.path.splitext(path)[0] + "-aliases.json"
        self._aliases = _load(self.aliases_path)  # login -> key

    def add(self, key: str, name: str) -> None:
        key, name = str(key), str(name)
        self.people.add(key, name)
        self._overlay[key] = name
        _persist(self.path, self._overlay)

    def has(self, key: str) -> bool:
        """Whether `key` was enrolled at runtime (not declared in the profile)."""
        return str(key) in self._overlay

    def remove(self, key: str) -> None:
        key = str(key)
        self.people.remove(key)
        self._overlay.pop(key, None)
        _persist(self.path, self._overlay)
        if any(k == key for k in self._aliases.values()):
            self._aliases = {login: k for login, k in self._aliases.items() if k != key}
            _persist(self.aliases_path, self._aliases)

    def link(self, login: str, key: str) -> None:
        """`login` is the person `key` from now on."""
        self._aliases[str(login)] = str(key)
        _persist(self.aliases_path, self._aliases)

    def resolve(self, login: str) -> str | None:
        """The person a login is linked to, or None."""
        return self._aliases.get(str(login))

    def linked(self, key: str) -> bool:
        """Whether a login is linked to `key` (its name then follows the login)."""
        return str(key) in self._aliases.values()
