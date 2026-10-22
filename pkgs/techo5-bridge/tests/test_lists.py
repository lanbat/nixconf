import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from techo5_bridge.lists import (
    Event,
    Ha,
    Item,
    clean,
    parse_events,
    parse_items,
    pick_reminders,
    pick_todos,
)


def test_open_items_only():
    response = {
        "todo.shopping": {
            "items": [
                {"uid": "1", "summary": "Milk", "status": "needs_action"},
                {"uid": "2", "summary": "Bread", "status": "completed"},
            ]
        }
    }
    items = parse_items("todo.shopping", response)
    assert len(items) == 1
    assert items[0].title == "Milk"


def test_date_only_due_is_due_that_day():
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 10, 21, 9, 0, tzinfo=tz)
    response = {
        "todo.tasks": {
            "items": [
                {"uid": "a", "summary": "Pay bill", "status": "needs_action", "due": "2026-10-21"},
            ]
        }
    }
    items = parse_items("todo.tasks", response)
    reminders = pick_reminders(items, now)
    assert len(reminders) == 1
    assert reminders[0].due == datetime(2026, 10, 21, 0, 0, tzinfo=tz)


def test_reminders_overdue_first_then_later_today():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 21, 14, 0, tzinfo=tz)
    items = [
        Item("1", "Later today", datetime(2026, 10, 21, 18, 0, tzinfo=tz), "todo.x"),
        Item("2", "Overdue morning", datetime(2026, 10, 21, 8, 0, tzinfo=tz), "todo.x"),
        Item("3", "Tomorrow", datetime(2026, 10, 22, 8, 0, tzinfo=tz), "todo.x"),
        Item("4", "No due", None, "todo.x"),
    ]
    reminders = pick_reminders(items, now)
    assert [r.uid for r in reminders] == ["2", "1"]
    assert "3" not in [r.uid for r in reminders]
    assert "4" not in [r.uid for r in reminders]


def test_todos_are_the_undated_and_count_the_rest():
    items = [
        Item(str(i), f"Task {i}", None, "todo.x") for i in range(7)
    ]
    picked, more = pick_todos(items)
    assert len(picked) == 5
    assert more == 2
    assert [p.uid for p in picked] == ["0", "1", "2", "3", "4"]


def test_events_sorted_and_ended_dropped():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 21, 12, 0, tzinfo=tz)
    response = {
        "calendar.a": {
            "events": [
                {"summary": "Future", "start": "2026-10-21T15:00:00", "end": "2026-10-21T16:00:00"},
                {"summary": "Past", "start": "2026-10-21T10:00:00", "end": "2026-10-21T11:00:00"},
                {"summary": "Soon", "start": "2026-10-21T13:00:00", "end": "2026-10-21T14:00:00"},
            ]
        }
    }
    events = parse_events(response, now)
    assert [e.title for e in events] == ["Soon", "Future"]


def test_clean_makes_one_short_plain_line():
    raw = "line1\nline2" + "x" * 10000 + "**bold**"
    out = clean(raw, limit=60)
    assert len(out) <= 60
    assert "\n" not in out
    assert "*" not in out
    assert out.endswith("…") or len(out) == 60


def test_500_items_are_capped():
    items_data = [
        {"uid": str(i), "summary": f"Item {i}", "status": "needs_action"}
        for i in range(500)
    ]
    response = {"todo.big": {"items": items_data}}
    items = parse_items("todo.big", response)
    assert len(items) == 200


def test_midnight_and_dst():
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 3, 29, 12, 0, tzinfo=tz)
    response = {
        "todo.x": {
            "items": [
                {
                    "uid": "1",
                    "summary": "Spring forward edge",
                    "status": "needs_action",
                    "due": "2026-03-29T02:30:00",
                },
            ]
        }
    }
    items = parse_items("todo.x", response)
    assert len(items) == 1
    pick_reminders(items, now)


class StubResponse:
    def __init__(self, status: int, body: str | bytes = ""):
        self.status = status
        self._body = body

    async def text(self):
        if isinstance(self._body, bytes):
            return self._body.decode()
        return self._body

    async def json(self):
        import json

        return json.loads(self._body)


class StubSession:
    def __init__(self, response: StubResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc

    def post(self, url, **kwargs):
        return StubRequest(self._response, self._exc)


class StubRequest:
    def __init__(self, response: StubResponse | None, exc: Exception | None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        if self._exc:
            raise self._exc
        return self._response

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_ha_errors_give_none(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    ha = Ha("https://ha.example", "token", StubSession(StubResponse(403)))

    assert await ha.todo_items("todo.x") is None

    ha2 = Ha("https://ha.example", "token", StubSession(None, asyncio.TimeoutError()))
    assert await ha2.todo_items("todo.x") is None

    ha3 = Ha("https://ha.example", "token", StubSession(StubResponse(200, "not json")))
    assert await ha3.todo_items("todo.x") is None

    assert len(caplog.records) >= 3
