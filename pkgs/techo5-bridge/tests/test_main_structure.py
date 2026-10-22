import asyncio
import os
import tempfile

import pytest

from techo5_bridge import __main__ as main_mod
from techo5_bridge.__main__ import Echo, fetch_items_from_ha, run


ECHO_METHODS = (
    "carry",
    "_carry",
    "_fetch_items",
    "_set",
    "_install_ca",
    "_image_timeout",
    "_on_state",
    "_on_adverts",
    "device_loop",
    "router_loop",
    "tick_loop",
    "code",
)


def test_echo_exposes_all_io_methods_on_the_class():
    for name in ECHO_METHODS:
        assert callable(getattr(Echo, name, None)), f"Echo.{name} must be a class method"


def test_fetch_items_from_ha_is_a_module_function_not_inside_echo():
    assert asyncio.iscoroutinefunction(fetch_items_from_ha)
    assert fetch_items_from_ha.__module__ == main_mod.__name__
    assert "fetch_items_from_ha" not in Echo.__dict__


def test_run_builds_device_router_and_tick_tasks(monkeypatch):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as key:
        key.write("0123456789abcdef0123456789abcdef")
        key_path = key.name
    cfg = {
        "router_url": "ws://127.0.0.1:9/room",
        "devices": [{"name": "kitchen", "room": "Kitchen", "address": "127.0.0.1", "key_file": key_path}],
    }
    created: list = []

    def capture_create_task(coro):
        created.append(coro.cr_code.co_name)
        coro.close()
        return asyncio.get_event_loop().create_future()

    async def fake_gather(*tasks):
        return []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(asyncio, "create_task", capture_create_task)
    monkeypatch.setattr(asyncio, "gather", fake_gather)
    monkeypatch.setattr(main_mod.aiohttp, "ClientSession", lambda: FakeSession())
    asyncio.run(run(cfg))
    os.unlink(key_path)
    assert set(created) == {"device_loop", "router_loop", "tick_loop"}
