import json
from datetime import datetime, timedelta, timezone

import pytest

from assistant_router.hass import HassError
from assistant_router.lists import (DEFAULT, MAX_LISTS, NOT_SURE, Command, Lists, Personal, command, entity,
                                    slug)


@pytest.mark.parametrize("name,want", [
    ("shopping", "shopping"), ("Hardware Store", "hardware_store"), ("my to do", DEFAULT), ("To-do", DEFAULT),
    ("the groceries", "groceries"), ("reminders", None), ("", None), ("!!!", None), ("a" * 40, "a" * 24),
])
def test_a_list_name_becomes_a_slug(name, want):
    assert slug(name) == want


@pytest.mark.parametrize("text,want", [
    ("add milk to my list", Command("add", "milk", None)),
    ("Okay Nabu, add milk and eggs to my shopping list please", Command("add", "milk and eggs", "shopping")),
    ("put batteries on my hardware store list", Command("add", "batteries", "hardware store")),
    ("take milk off my list", Command("remove", "milk", None)),
    ("remove the milk from my shopping list", Command("remove", "the milk", "shopping")),
    ("what's on my list", Command("read", "", None)),
    ("what is on my shopping list?", Command("read", "", "shopping")),
    ("read me my hardware list", Command("read", "", "hardware")),
    ("make a list called hardware", Command("make", "", "hardware")),
    ("create a new list named garden jobs please", Command("make", "", "garden jobs")),
    ("make me a new books list", Command("make", "", "books")),
])
def test_it_hears_a_list_command(text, want):
    assert command(text) == want


@pytest.mark.parametrize("text", [
    "turn on the kitchen light", "add milk to the shopping list", "what's on my calendar", "make a list",
    "add a birthday card to my wishlist now", "remind me to buy milk",
])
def test_and_leaves_the_rest_alone(text):
    assert command(text) is None


def test_the_lists_people_asked_for_are_kept(tmp_path):
    path = tmp_path / "lists.json"
    told = []
    lists = Lists(str(path))
    lists.listeners.append(told.append)
    assert lists.slugs("kiril") == [DEFAULT]
    assert lists.add("kiril", "shopping") and not lists.add("kiril", "shopping")
    assert told == ["kiril"]
    assert Lists(str(path)).slugs("kiril") == [DEFAULT, "shopping"]
    assert lists.add("kiril", DEFAULT)  # their own, asked for before it exists: made too
    for i in range(MAX_LISTS):
        lists.add("kiril", f"l{i}")
    assert not lists.add("kiril", "one_more")
    lists.add("guest_jo", "shopping")
    assert lists.rekey("guest_jo", "kiril") and "guest_jo" not in lists.lists
    assert lists.forget("kiril") and not lists.forget("kiril")
    assert json.loads(path.read_text()) == {}


def test_a_file_that_is_not_ours_is_ignored(tmp_path):
    path = tmp_path / "lists.json"
    path.write_text("[1]")
    assert Lists(str(path)).lists == {}
    path.write_text(json.dumps({"kiril": ["ok", "Bad Slug", 7, "../x"], "maria": "no"}))
    assert Lists(str(path)).lists == {"kiril": ["ok"]}


class FakeHass:
    def __init__(self, lists=("todo.kiril_todo",), items=None, down=False):
        self.lists, self.down = list(lists), down
        self.items = items if items is not None else [{"uid": "u1", "summary": "Milk", "status": "needs_action"},
                                                       {"uid": "u2", "summary": "Eggs", "status": "needs_action"}]
        self.calls = []

    async def states(self, domain=None):
        if self.down:
            raise HassError("GET /api/states")
        return [{"entity_id": e, "name": e, "state": "0"} for e in self.lists]

    async def call(self, domain, service, data, response=False):
        if self.down:
            raise HassError("POST")
        self.calls.append((domain, service, data))
        if response:
            return {data["entity_id"]: {"items": self.items}}
        return {}


def personal(tmp_path, **kw):
    told = []
    ha = FakeHass(**kw)
    lists = Lists(str(tmp_path / "lists.json"))
    return Personal(ha, lists, {"kiril": "Kiril", "maria": "Maria"}, told.append), ha, lists, told


async def say(p, who, text):
    return (await p.handle(who, command(text)))[0]


async def test_only_a_confirmed_change_may_be_reported_as_done(tmp_path):
    p, _, _, _ = personal(tmp_path)
    assert await p.handle("kiril", command("add bread to my list")) == ("Added bread to your list.", True)
    assert await p.handle("kiril", command("what's on my list")) == ("On your list: Milk and Eggs.", False)
    assert (await p.handle("kiril", command("take cheese off my list")))[1] is False
    p, _, _, _ = personal(tmp_path, down=True)
    assert await p.handle("kiril", command("add bread to my list")) == ("I can't reach your lists right now.", False)


async def test_it_adds_reads_and_removes_on_the_speakers_own_list(tmp_path):
    p, ha, _, told = personal(tmp_path)
    assert await say(p, "kiril", "add bread to my list") == "Added bread to your list."
    assert ha.calls[-1] == ("todo", "add_item", {"entity_id": "todo.kiril_todo", "item": "bread"})
    assert told == ["kiril"]
    assert await say(p, "kiril", "what's on my list") == "On your list: Milk and Eggs."
    assert await say(p, "kiril", "take milk off my list") == "Took Milk off your list."
    assert ha.calls[-1] == ("todo", "remove_item", {"entity_id": "todo.kiril_todo", "item": "u1"})
    assert await say(p, "kiril", "take cheese off my list") == "I can't find cheese on your list."
    assert told == ["kiril", "kiril"]


async def test_the_list_is_always_the_speakers_never_one_that_was_named(tmp_path):
    p, ha, _, _ = personal(tmp_path, lists=("todo.kiril_todo", "todo.maria_todo"))
    await say(p, "kiril", "add bread to my maria list")
    assert not [c for c in ha.calls if "maria" in c[2]["entity_id"]]
    assert await say(p, "kiril", "add bread to my maria list") == \
        'You don\'t have a maria list. Say "make a list called maria".'


async def test_nobody_in_particular_is_nobody(tmp_path):
    p, ha, lists, _ = personal(tmp_path)
    assert await say(p, None, "add bread to my list") == NOT_SURE
    assert await say(p, "stranger", "add bread to my list") == NOT_SURE
    assert ha.calls == [] and lists.lists == {}


async def test_a_list_that_does_not_exist_yet_is_made_and_the_item_is_not_lost_silently(tmp_path):
    p, ha, lists, _ = personal(tmp_path, lists=())
    said = await say(p, "kiril", "add bread to my list")
    assert "setting up" in said and ha.calls == []
    assert lists.lists == {"kiril": [DEFAULT]}


async def test_making_a_list(tmp_path):
    p, _, lists, _ = personal(tmp_path)
    assert await say(p, "kiril", "make a list called hardware store") == \
        "I'll have your hardware store list ready in a minute."
    assert lists.slugs("kiril") == [DEFAULT, "hardware_store"]
    assert "already" in await say(p, "kiril", "make a list called hardware store")
    assert "already" in await say(p, "kiril", "make a list called to do")
    assert "couldn't" in await say(p, "kiril", "make a list called reminders")


async def test_a_list_they_made_is_used_by_name(tmp_path):
    p, ha, _, _ = personal(tmp_path, lists=("todo.kiril_todo", "todo.kiril_hardware_store"))
    await say(p, "kiril", "make a list called hardware store")
    assert await say(p, "kiril", "put screws on my hardware store list") == "Added screws to your hardware store list."
    assert ha.calls[-1][2]["entity_id"] == "todo.kiril_hardware_store"
    assert await say(p, "kiril", "what's on my hardware store list") == \
        "On your hardware store list: Milk and Eggs."


async def test_a_long_list_is_read_in_part_and_an_empty_one_says_so(tmp_path):
    rows = [{"uid": str(i), "summary": f"item {i}", "status": "needs_action"} for i in range(9)]
    p, _, _, _ = personal(tmp_path, items=rows)
    assert (await say(p, "kiril", "what's on my list")).endswith("item 5, and 3 more.")
    p, _, _, _ = personal(tmp_path, items=[])
    assert await say(p, "kiril", "what's on my list") == "Your list is empty."


async def test_when_home_assistant_is_down_it_says_so_and_changes_nothing(tmp_path):
    p, ha, lists, told = personal(tmp_path, down=True)
    assert await say(p, "kiril", "add bread to my list") == "I can't reach your lists right now."
    assert told == [] and lists.lists == {}
    assert entity("kiril", "todo") == "todo.kiril_todo"


NOW = datetime(2026, 10, 22, 14, 0, tzinfo=timezone(timedelta(hours=1)))


async def test_a_reminder_goes_in_the_speakers_reminders_list_with_its_time(tmp_path):
    p, ha, _, told = personal(tmp_path, lists=("todo.kiril_todo", "todo.kiril_reminders"))
    got = await p.remind("kiril", "call mum", NOW.replace(hour=17), NOW)
    assert got == ("OK, I'll remind you to call mum at 5 PM.", True)
    assert ha.calls[-1] == ("todo", "add_item", {"entity_id": "todo.kiril_reminders", "item": "call mum",
                                                  "due_datetime": "2026-10-22 17:00:00"})
    assert told == ["kiril"]
    assert (await p.remind(None, "x", NOW, NOW))[1] is False
    assert (await p.remind("stranger", "x", NOW, NOW))[0] == NOT_SURE


async def test_a_reminders_list_that_does_not_exist_yet_is_asked_for_and_never_listed_as_a_list(tmp_path):
    p, ha, lists, _ = personal(tmp_path, lists=("todo.kiril_todo",))
    said, done = await p.remind("kiril", "call mum", NOW.replace(hour=17), NOW)
    assert not done and "setting up" in said and ha.calls == []
    assert lists.lists == {"kiril": ["reminders"]}  # post-setup makes todo.kiril_reminders from this
    assert lists.slugs("kiril") == [DEFAULT]          # but it is not a list to add to by name


async def test_reminders_can_be_completed_and_dropped_but_not_added_to_by_name(tmp_path):
    rows = [{"uid": "r1", "summary": "call mum", "status": "needs_action", "due": "2026-10-22T17:00:00"}]
    p, ha, _, _ = personal(tmp_path, lists=("todo.kiril_todo", "todo.kiril_reminders"), items=rows)
    assert (await p.items("kiril", "reminders"))[0]["due"] == "2026-10-22T17:00:00"
    assert await p.complete("kiril", "reminders", "r1")
    assert ha.calls[-1][1] == "update_item" and ha.calls[-1][2]["entity_id"] == "todo.kiril_reminders"
    assert not await p.put("kiril", "reminders", "buy milk")
    assert not await p.complete("kiril", "reminders", "someone-elses")
    assert not await p.complete("maria", "reminders", "r1") or ha.calls[-1][2]["entity_id"] != "todo.kiril_reminders"
