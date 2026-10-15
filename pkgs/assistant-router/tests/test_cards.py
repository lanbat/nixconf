import json

import pytest
from assistant_router.body import Bodies
from assistant_router.cards import READ_S, Cards, command
from assistant_router.people import People
from assistant_router.server import Config, make_app, make_body_app
from assistant_router.state import State

from test_server import Sock, fakes, post, req  # noqa: F401  (fakes is a fixture)


@pytest.mark.parametrize("text,what", [
    ("Remember this card", None),  # cards go with "remember me" now (identity.py)
    ("Forget my card", "forget"),
    ("Please remove my cards", "forget"),
    ("Add a birthday card to the shopping list", None),
    ("Remember to buy a card", None),
    ("What is this card game?", None),
])
def test_what_was_said_about_a_card(text, what):
    assert command(text) == what


def test_the_card_tapped_after_asking_is_theirs(tmp_path):
    cards = Cards(str(tmp_path / "cards.json"))
    assert cards.tapped("Kitchen", "0435249AD11290", 0.0) == (None, False)
    cards.expect("Kitchen", "kiril", 10.0)
    assert cards.tapped("Bedroom", "0435249AD11290", 11.0) == (None, False)  # another room
    assert cards.tapped("kitchen", "0435249ad11290", 11.0) == ("kiril", True)
    assert cards.tapped("Kitchen", "0435249AD11290", 12.0) == ("kiril", False)
    assert json.loads((tmp_path / "cards.json").read_text()) == {"0435249AD11290": "kiril"}
    assert Cards(str(tmp_path / "cards.json")).cards == {"0435249AD11290": "kiril"}
    assert cards.forget("kiril") == 1 and cards.cards == {}


def test_a_late_tap_is_not_learned():
    cards = Cards()
    cards.expect("Kitchen", "kiril", 0.0)
    assert cards.tapped("Kitchen", "0435249AD11290", READ_S + 1.0) == (None, False)


def test_junk_uids_are_ignored():
    cards = Cards()
    cards.expect("Kitchen", "kiril", 0.0)
    assert cards.tapped("Kitchen", "<script>", 1.0) == (None, False)


async def card_client(aiohttp_client, base, people, cards):
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="local-first")
    bodies = Bodies("You are Nabu.")
    sock = Sock()
    bodies.connect("Bedroom 1", sock)
    app = make_app(cfg, State(), clock=lambda: 100.0, bodies=bodies, people=people, cards=cards)
    return await aiohttp_client(app), sock


async def test_forget_my_card(aiohttp_client, fakes):
    _, _, _, base = fakes
    people, cards = People({"kiril": "Kiril"}), Cards()
    cards.cards = {"0435249AD11290": "kiril"}
    people.observe("Bedroom 1", "card", "kiril", 1.0, 100.0)
    c, _ = await card_client(aiohttp_client, base, people, cards)
    said = (await post(c, req("Forget my card")))["message"]["content"]
    assert "forgotten your card" in said and cards.cards == {}


async def test_the_robot_learns_the_card_and_says_so(aiohttp_client):
    people, cards = People({"kiril": "Kiril"}), Cards()
    cards.expect("Kitchen", "kiril", 100.0)
    c = await aiohttp_client(make_body_app(Bodies(""), clock=lambda: 101.0, people=people, cards=cards))
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await robot.receive_json()  # body_hello
    await robot.send_json({"event": {"nfc": "0435249AD11290"}})
    assert await robot.receive_json() == {"nfc_read": 0}
    act = (await robot.receive_json())["act"]
    assert act["gesture"] == "nod" and "Kiril" in act["caption"]
    assert people.assess("Kitchen", 101.0).speaker == "kiril"
    await robot.close()
