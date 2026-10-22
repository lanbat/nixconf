from datetime import datetime, timedelta, timezone

import pytest

from assistant_router.reminders import Reminder, describe, item_due, parse, parse_when

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 22, 14, 0, tzinfo=TZ)  # a Thursday, two in the afternoon


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize("said,want", [
    ("in 10 minutes", NOW + timedelta(minutes=10)),
    ("in an hour", NOW + timedelta(hours=1)),
    ("in half an hour", NOW + timedelta(minutes=30)),
    ("in two hours", NOW + timedelta(hours=2)),
    ("in 30 seconds", NOW + timedelta(seconds=30)),
    ("at 5pm", at(22, 17)),
    ("at 5:30 PM", at(22, 17, 30)),
    ("at 5 p.m.", at(22, 17)),
    ("at 17:15", at(22, 17, 15)),
    ("at 5", at(22, 17)),         # the next 5 o'clock
    ("at 3", at(22, 15)),         # 3 am has gone; the next 3 o'clock is this afternoon
    ("at 2pm", at(23, 14)),       # a time that has passed today is tomorrow's
    ("at noon", at(23, 12)),
    ("at five thirty", at(22, 17, 30)),
    ("at five fifty", None),      # minutes it can't read are not guessed: it asks
    ("tomorrow at 9", at(23, 9)),
    ("tomorrow at 5", at(23, 17)),
    ("tomorrow at 12", at(23, 12)),
    ("tonight at 8", at(22, 20)),
    ("on Monday at 3pm", at(26, 15)),
    ("friday at 9am", at(23, 9)),
    ("next thursday at 10", at(29, 10)),
    ("tomorrow", None),
    ("whenever", None),
    ("at 25", None),
])
def test_when(said, want):
    assert parse_when(said, NOW) == want


@pytest.mark.parametrize("text,what,due", [
    ("remind me to call mum at 5pm", "call mum", at(22, 17)),
    ("Okay Nabu, remind me to take the bins out tomorrow at 7", "take the bins out", at(23, 7)),
    ("remind me at 5pm to call mum", "call mum", at(22, 17)),
    ("remind me in 10 minutes to check the oven", "check the oven", NOW + timedelta(minutes=10)),
    ("please remind me about the dentist on Monday at 9am", "the dentist", at(26, 9)),
    ("could you remind me to buy 2 apples at 6", "buy 2 apples", at(22, 18)),
    ("remind me to take a 5 minute break in 10 minutes", "take a 5 minute break", NOW + timedelta(minutes=10)),
    ("remind me to call mum", "call mum", None),
    ("remind me to call mum tomorrow", "call mum", None),
])
def test_a_reminder(text, what, due):
    assert parse(text, NOW) == Reminder(what, due)


@pytest.mark.parametrize("text", [
    "what's on my list", "set a timer for 5 minutes", "remind me", "remind me to", "turn on the light at 5pm",
])
def test_not_a_reminder(text):
    assert parse(text, NOW) is None


def test_said_aloud_and_given_to_home_assistant():
    assert describe(at(22, 17), NOW) == "at 5 PM"
    assert describe(at(22, 17, 30), NOW) == "at 5:30 PM"
    assert describe(at(23, 9), NOW) == "tomorrow at 9 AM"
    assert describe(at(26, 15), NOW) == "on Monday at 3 PM"
    assert describe(at(30, 15), NOW) == "on Friday 30 October at 3 PM"
    assert item_due(at(22, 17, 5)) == "2026-10-22 17:05:00"
