"""Spoken reminders: "remind me to call mum at 5pm".

A reminder is an item with a due time in the speaker's own reminders list
(todo.<key>_reminders, lists.py), which is what their screen shows when they are
alone in front of it, and what chimes there when it comes due
(pkgs/techo5-bridge). The router only parses what was said here, in the
server's time zone, and adds the item.

What it hears:

- "remind me to <thing> at 5pm" / "at 17:30" / "at 5" (the next 5 o'clock);
- "... in 10 minutes" / "in an hour" / "in half an hour";
- "... tomorrow at 9", "... tonight at 8", "... on Monday at 3pm";
- "remind me at 5pm to <thing>", "remind me in 10 minutes to <thing>";
- "remind me to <thing>" alone: it asks "When should I remind you?" once.

A time without am or pm is read as the clock does for the day: today, the next
time that hour comes round; tomorrow or a named day, 7 to 11 as morning, 12 as
noon and 1 to 6 as afternoon.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

ASK_WHEN = "When should I remind you?"
NO_TIME = "I didn't catch a time, so I didn't set that."
MAX_THING = 100  # characters

_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
            "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
_NUM = r"(?:\d{1,3}|" + "|".join(sorted((re.escape(w) for w in _NUMBERS), key=len, reverse=True)) + ")"
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)"
_HOURS = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_QUARTERS = {"fifteen": 15, "thirty": 30, "forty five": 45}
_TIME = (rf"(?:\d{{1,2}}(?::\d{{2}})?(?: ?(?:am|pm))?|noon|midnight|"
         rf"{_HOURS}(?: (?:fifteen|thirty|forty five))?(?: ?(?:am|pm)| o'clock)?)")
_DAYNAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY = rf"(?:today|tonight|tomorrow|(?:on |next )?(?:{'|'.join(_DAYNAMES)}))"
_WHEN = (rf"(?:in (?:half an hour|{_NUM} {_UNIT})|{_DAY} (?:at |by )?{_TIME}|{_DAY}|(?:at |by ){_TIME})")
_LEAD = re.compile(r"^(?:(?:hey |okay |ok )?nabu )?(?:please )?(?:(?:can|could|would) you )?(?:please )?"
                   r"remind me (?:(?:to|about|that) )?(?P<rest>.+)$")
_FIRST = re.compile(rf"^(?P<when>{_WHEN})(?: (?:to|that|about))? (?P<what>.+)$")
_LAST = re.compile(rf"^(?P<what>.+?) (?P<when>{_WHEN})$")


@dataclass
class Reminder:
    what: str
    due: datetime | None  # None: it asks when


def _clean(text: str) -> str:
    said = (text or "").lower().replace("a.m.", "am").replace("p.m.", "pm")
    said = re.sub(r"(\d)\.(\d{2})\b", r"\1:\2", said)  # 5.30
    said = re.sub(r"[^a-z0-9:' ]+", " ", said)
    return " ".join(said.split())


def _number(word: str) -> int | None:
    return int(word) if word.isdigit() else _NUMBERS.get(word)


def _hour(said: str) -> tuple[int, int, str] | None:
    """(hour 0-23 or 1-12, minute, "am" | "pm" | "") of a clock time."""
    m = re.search(r"\b(noon|midnight)\b", said)
    if m:
        return (12, 0, "pm") if m.group(1) == "noon" else (12, 0, "am")
    # What is left of the sentence once the time is taken out holds no other number: "five
    # fifty" is not 5 o'clock, and a time it can't read is asked for, not guessed.
    def leftover(match: re.Match) -> bool:
        rest = said[:match.start()] + " " + said[match.end():]
        return bool(re.search(rf"\b(?:\d+|{_NUM})\b", rest))
    m = re.search(rf"\b(\d{{1,2}})(?::(\d{{2}}))? ?(am|pm)?\b|"
                  rf"\b({_HOURS})(?: (fifteen|thirty|forty five))?(?: ?(am|pm)| o'clock)?\b", said)
    if not m or leftover(m):
        return None
    if m.group(4):
        hour, minute, meridiem = _NUMBERS[m.group(4)], _QUARTERS.get(m.group(5) or "", 0), m.group(6) or ""
    else:
        hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3) or ""
    if minute > 59 or hour > 23 or (meridiem and not 1 <= hour <= 12):
        return None
    return hour, minute, meridiem


def _at(day: datetime, hour: int, minute: int, meridiem: str, named: bool, now: datetime) -> datetime | None:
    """`day` at that time. Without am or pm: on the day itself, the next time that hour
    comes round; on a day that was named, 7-11 am, 12 noon, 1-6 pm."""
    if hour >= 13 or meridiem == "" and hour == 0:
        options = [hour]
    elif meridiem == "am":
        options = [hour % 12]
    elif meridiem == "pm":
        options = [hour % 12 + 12]
    elif named:
        options = [hour if 7 <= hour <= 11 else 12 if hour == 12 else hour + 12]
    else:
        options = sorted({hour % 12, hour % 12 + 12})
    for h in options:
        due = day.replace(hour=h, minute=minute, second=0, microsecond=0)
        if due > now:
            return due
    return None


def parse_when(text: str, now: datetime) -> datetime | None:
    """When `text` says, after `now` (aware, in the server's time zone), or None."""
    said = _clean(text)
    m = re.search(rf"\bin (half an hour|{_NUM} {_UNIT})\b", said)
    if m:
        if m.group(1) == "half an hour":
            return now + timedelta(minutes=30)
        count, _, unit = m.group(1).rpartition(" ")
        n = _number(count)
        if n is None:
            return None
        return now + (timedelta(seconds=n) if unit.startswith("sec") else
                      timedelta(minutes=n) if unit.startswith("min") else timedelta(hours=n))
    day, named = now, False
    if "tomorrow" in said:
        day, named = now + timedelta(days=1), True
    elif "tonight" in said:
        named = True
    else:
        for i, name in enumerate(_DAYNAMES):
            if re.search(rf"\b{name}\b", said):
                ahead = (i - now.weekday()) % 7
                day, named = now + timedelta(days=ahead or 7 if "next" in said else ahead), True
                break
    clock = _hour(re.sub(rf"\b(?:{'|'.join(_DAYNAMES)}|tomorrow|tonight|today|next|on|at|by)\b", " ", said))
    if clock is None:
        return None
    hour, minute, meridiem = clock
    if "tonight" in said and not meridiem and hour < 12:
        meridiem = "pm"
    due = _at(day, hour, minute, meridiem, named, now)
    if due is None and not named:
        due = _at(day + timedelta(days=1), hour, minute, meridiem, True, now)
    if due is None and named:  # a named day that was today, already gone: a week on
        due = _at(day + timedelta(days=7), hour, minute, meridiem, True, now)
    return due


def parse(text: str, now: datetime) -> Reminder | None:
    """A reminder in `text`, or None: it isn't one."""
    m = _LEAD.match(_clean(text))
    if not m:
        return None
    rest = m.group("rest")
    hit = _FIRST.match(rest) or _LAST.match(rest)
    what, due = (hit.group("what"), parse_when(hit.group("when"), now)) if hit else (rest, None)
    what = what.strip()[:MAX_THING]
    return Reminder(what, due) if what and what not in ("to", "about", "that") else None


def describe(due: datetime, now: datetime) -> str:
    """When it will be, as said aloud."""
    clock = due.strftime("%I:%M %p").lstrip("0")
    if clock.endswith(":00 AM") or clock.endswith(":00 PM"):
        clock = clock.replace(":00", "")
    days = (due.date() - now.date()).days
    if days == 0:
        return f"at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if days < 7:
        return f"on {due.strftime('%A')} at {clock}"
    return f"on {due.strftime('%A %d %B')} at {clock}"


def item_due(due: datetime) -> str:
    """The time as Home Assistant's to-do services take it: local, no zone."""
    return due.strftime("%Y-%m-%d %H:%M:%S")
