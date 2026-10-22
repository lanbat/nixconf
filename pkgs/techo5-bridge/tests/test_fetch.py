from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from techo5_bridge.__main__ import fetch_items_from_ha
from techo5_bridge.brain import Brain, DeviceConfig, Profile
from techo5_bridge.lists import Event


class StubHa:
    async def todo_items(self, entity: str):
        return None

    async def events(self, calendars: list[str]):
        return None


def _brain() -> Brain:
    cfg = DeviceConfig(name="kitchen", room="Kitchen", ha_url="https://ha.example.org", ha_token="tok")
    return Brain(cfg, {"alex": "Alex"})


@pytest.mark.asyncio
async def test_all_none_ha_responses_leave_the_last_items_message():
    b = _brain()
    b.router({"here": [{"person": "alex"}]}, 0.0)
    tz = ZoneInfo("UTC")
    data = {
        "reminders": [],
        "todos": [],
        "more": 0,
        "events": [Event("Standup", datetime(2026, 10, 21, 14, 0, tzinfo=tz), False)],
    }
    b.items(1.0, data)
    last = b._last_items
    _, prof = b.wants_items()
    outs = await fetch_items_from_ha(b, StubHa(), "alex", prof, b._fetch_gen)
    assert outs == []
    assert b._last_items == last
