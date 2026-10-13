"""People's NFC cards: "remember this card", then a tap on the robot.

Nothing is listed in the profile. Someone the router already recognises in a
room with a robot (by a phone tap, voice or face: a named speaker, people.py)
says "remember this card"; the robot reads cards for READ_S (it is a tag for
phones otherwise, firmware nfc.h), and the first card tapped on it is theirs.
From then on that card, tapped on any robot, names them there for two minutes
("card" in people.py). "Forget my card" drops theirs.

Cards are kept in a small JSON file in the router's state directory:
{uid: key}. A card's UID is easy to copy: a hint, never authority.
"""
from __future__ import annotations

import json
import os
import re

READ_S = 30              # the robot reads cards this long after "remember this card"
CARDS_PER_PERSON = 4
_UID = re.compile(r"^[0-9A-F]{8,20}$")

# "Remember this card", "learn my card", not "add a birthday card to the list".
_REMEMBER = re.compile(r"\b(remember|learn|save|register)\b.*\b(this|my) (card|tag)\b")
_FORGET = re.compile(r"\b(forget|remove|delete)\b.*\b(this|my|the) (card|cards|tag|tags)\b")


def command(text: str) -> str | None:
    """"remember", "forget", or None: what was said about a card."""
    said = re.sub(r"[^a-z' ]+", " ", (text or "").lower())
    if _FORGET.search(said):
        return "forget"
    if _REMEMBER.search(said):
        return "remember"
    return None


def _key(room: str) -> str:
    return (room or "").strip().casefold()


class Cards:
    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.cards: dict[str, str] = {}
        self._waiting: dict[str, tuple[str, float]] = {}  # room -> (key, until)
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.cards = {str(u): str(k) for u, k in data.items() if _UID.match(str(u))}
            except (OSError, ValueError):
                pass

    def _save(self) -> None:
        if self.path:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cards, f, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)

    def expect(self, room: str, key: str, now: float) -> None:
        """The next card tapped in `room`, within READ_S, is `key`'s."""
        self._waiting[_key(room)] = (key, now + READ_S)

    def tapped(self, room: str, uid: str, now: float) -> tuple[str | None, bool]:
        """(whose card it is, whether it was just learned)."""
        uid = (uid or "").upper()
        if not _UID.match(uid):
            return None, False
        waiting = self._waiting.pop(_key(room), None)
        if waiting is not None and now <= waiting[1]:
            key = waiting[0]
            theirs = [u for u, k in self.cards.items() if k == key and u != uid]
            if len(theirs) >= CARDS_PER_PERSON:
                self.cards.pop(theirs[0])
            self.cards[uid] = key
            self._save()
            return key, True
        return self.cards.get(uid), False

    def forget(self, key: str) -> int:
        gone = [u for u, k in self.cards.items() if k == key]
        for u in gone:
            del self.cards[u]
        if gone:
            self._save()
        return len(gone)
