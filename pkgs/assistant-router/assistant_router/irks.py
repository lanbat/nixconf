"""People's phones by their Bluetooth key, for the room sensors.

A phone changes its Bluetooth address every few minutes; its Identity
Resolving Key (IRK, 16 bytes) tells its addresses from everyone else's. A
phone paired once hands it over (docs/people.md). The router keeps the keys,
per person, in its state directory (readable by it alone), and gives the full
set to every room sensor when it connects and whenever it changes. The sensors
keep them in memory only.

An IRK lets whoever has it follow the phone within Bluetooth range: it is
kept like a secret, and goes only to the room sensors' socket.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable

_IRK = re.compile(r"[0-9a-f]{32}")
MAX_PER_PERSON = 4


def _load(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [i for i in v if isinstance(i, str) and _IRK.fullmatch(i)][:MAX_PER_PERSON]
            for k, v in data.items() if isinstance(v, list)}


class Irks:
    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._keys = _load(path)
        self.listeners: list[Callable[[dict[str, list[str]]], None]] = []

    def all(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._keys.items() if v}

    def add(self, key: str, irk: str) -> bool:
        """`irk` (32 hex digits) for `key`'s phone; False when it isn't one,
        or is known already. The newest MAX_PER_PERSON per person are kept."""
        irk = (irk or "").strip().lower()
        if not key or not _IRK.fullmatch(irk) or irk in self._keys.get(key, []):
            return False
        self._keys[key] = ([i for i in self._keys.get(key, []) if i != irk] + [irk])[-MAX_PER_PERSON:]
        self._save()
        return True

    def forget(self, key: str) -> bool:
        if not self._keys.pop(key, None):
            return False
        self._save()
        return True

    def _save(self) -> None:
        if self.path:
            tmp = self.path + ".new"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._keys, f)
            os.replace(tmp, self.path)
        for listener in self.listeners:
            listener(self.all())
