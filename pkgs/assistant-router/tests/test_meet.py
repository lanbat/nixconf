"""Meeting Nabu: enrolment by just talking (identity.py, server.py)."""
import pytest
from assistant_router.body import Bodies
from assistant_router.cards import READ_S, Cards
from assistant_router.identity import ASK_NAME, CARD_HINT, DECLINED, MORE_VOICE, OFFER, answer, command
from assistant_router.people import Assessment, People
from assistant_router.people_store import PeopleStore
from assistant_router.server import Config, make_app
from assistant_router.state import State

from test_server import FakeFrigate, FakeVoiceId, Sock, capture_ok, fakes, post, req  # noqa: F401


@pytest.mark.parametrize("text,what", [
    ("Remember me", ("remember", None)),
    ("Okay Nabu, please remember me.", ("remember", None)),
    ("Learn my voice", ("remember", None)),
    ("Forget my card", ("forget_card", None)),
    ("Call me Kiki", ("call_me", "Kiki")),
    ("My name is Mary-Ann", ("call_me", "Mary-Ann")),
    ("Remember to buy milk", None),
    ("Remember this card", None),
    ("What time is it?", None),
])
def test_commands(text, what):
    assert command(text) == what


@pytest.mark.parametrize("text,got", [
    ("Jane", ("name", "Jane")), ("I'm Jane", ("name", "Jane")), ("Hi, my name is Jane Doe", ("name", "Jane Doe")),
    ("No thanks", ("decline", None)), ("I'm just visiting", ("decline", None)), ("not now", ("decline", None)),
    ("What time is it?", ("unclear", None)), ("Turn on the lights", ("unclear", None)), ("Yes", ("unclear", None)),
])
def test_answers(text, got):
    assert answer(text) == got


async def client(aiohttp_client, base, people, store=None, voice=None, frigate=None, cards=None, offer=True):
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="cloud-first",
                 offer_to_meet=offer)
    bodies, sock = Bodies("You are Nabu."), Sock()
    bodies.connect("Bedroom 1", sock)
    app = make_app(cfg, State(), people=people, people_store=store, frigate=frigate, bodies=bodies,
                   capture_face=capture_ok, voice=voice, cards=cards)
    return await aiohttp_client(app), sock


def turn(text, before=()):
    history = []
    for user, reply in before:
        history += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
    return req(text, history=history)


async def test_an_unknown_voice_is_asked_once_and_becomes_a_person(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    people = People({})
    store, voice, frigate, cards = PeopleStore(people, str(tmp_path / "people.json")), FakeVoiceId(), FakeFrigate(), Cards()
    c, sock = await client(aiohttp_client, base, people, store, voice, frigate, cards)
    first = (await post(c, turn("What's the capital of the sea?")))["message"]["content"]
    assert first.endswith(OFFER)
    said = (await post(c, turn("I'm Jane", [("What's the capital of the sea?", first)])))["message"]["content"]
    assert said.startswith("Nice to meet you, Jane! I've got your face.") and CARD_HINT in said
    assert said.endswith("so I learn your voice?")  # two sentences so far, voice-id wants three
    assert people.names["jane"] == "Jane" and store.has("jane")
    assert frigate.enrolled[0][0] == "jane" and {"nfc_read": READ_S} in sock.sent
    assert cards.tapped("Bedroom 1", "0435249AD11290", 1e12) == (None, False)  # the window is timed
    more = (await post(c, turn("I like tea in the morning.")))["message"]["content"]
    assert "I'll know your voice now" in more
    # The same room isn't asked again today.
    again = (await post(c, turn("And the capital of the moon?")))["message"]["content"]
    assert OFFER not in again


async def test_a_decline_keeps_nothing(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    people = People({})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    c, _ = await client(aiohttp_client, base, people, store, FakeVoiceId(), FakeFrigate(), Cards())
    first = (await post(c, turn("What's the capital of the sea?")))["message"]["content"]
    said = (await post(c, turn("No thanks, I'm just visiting", [("What's the capital of the sea?", first)])))
    assert said["message"]["content"] == DECLINED and people.names == {}


async def test_no_offer_to_a_known_voice_or_when_off(aiohttp_client, fakes):
    _, _, _, base = fakes
    people = People({"kiril": "Kiril"})
    c, _ = await client(aiohttp_client, base, people, voice=FakeVoiceId(), cards=Cards())
    await c.post("/v1/voice", json={"text": "What's the capital of the sea?", "scores": {"kiril": 0.85}})
    said = (await post(c, turn("What's the capital of the sea?")))["message"]["content"]
    assert OFFER not in said
    c2, _ = await client(aiohttp_client, base, People({}), voice=FakeVoiceId(), cards=Cards(), offer=False)
    assert OFFER not in (await post(c2, turn("What's the capital of the sea?")))["message"]["content"]


async def test_remember_me(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    people, voice, cards = People({"kiril": "Kiril"}), FakeVoiceId(enrolled=["kiril"]), Cards()
    c, sock = await client(aiohttp_client, base, people, voice=voice, frigate=FakeFrigate(), cards=cards)
    import time
    people.observe("Bedroom 1", "tap", "kiril", 1.0, time.monotonic())
    said = (await post(c, turn("Remember me")))["message"]["content"]
    assert said.startswith("Got it, Kiril! I've added your face and voice.") and CARD_HINT in said
    assert ("kiril", "Remember me") in voice.learned and {"nfc_read": READ_S} in sock.sent
    assert MORE_VOICE not in said
    # Someone unknown is asked their name.
    c2, _ = await client(aiohttp_client, base, People({}), voice=FakeVoiceId(), cards=Cards())
    assert (await post(c2, turn("Remember me")))["message"]["content"] == ASK_NAME


async def test_remember_me_after_a_tap_enrols_a_voice_never_heard(aiohttp_client, fakes, tmp_path):
    # Known by their phone, never by voice: voice-id has nothing to refine, so
    # "remember me" starts their voiceprint, under the same key.
    _, _, _, base = fakes
    people = People({"kiril": "Kiril"})
    store, voice = PeopleStore(people, str(tmp_path / "people.json")), FakeVoiceId()
    c, _ = await client(aiohttp_client, base, people, store, voice, FakeFrigate(), Cards())
    import time
    people.observe("Bedroom 1", "tap", "kiril", 1.0, time.monotonic())
    said = (await post(c, turn("Remember me")))["message"]["content"]
    assert said.startswith("Got it, Kiril! I've added your face.") and said.endswith(MORE_VOICE)
    assert voice.texts["kiril"] == ["Remember me"] and voice.learned == []
    assert "Lovely" in (await post(c, turn("I like tea in the morning.")))["message"]["content"]
    done = (await post(c, turn("And coffee after lunch.")))["message"]["content"]
    assert done == "Got it, Kiril, I'll know your voice now." and voice.prints == {"kiril"}
    assert not store.has("kiril")  # still the profile's person, not a second one


async def test_call_me_renames_only_your_own_runtime_name(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    import time
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("jane", "Jane")
    c, _ = await client(aiohttp_client, base, people, store, cards=Cards())
    people.observe("Bedroom 1", "tap", "jane", 1.0, time.monotonic())
    assert (await post(c, turn("Call me Kiki")))["message"]["content"] == "Okay, I'll call you Kiki."
    assert people.names["jane"] == "Kiki"
    store.link("jdoe", "jane")
    assert "Change it there" in (await post(c, turn("Call me Jay")))["message"]["content"]


async def test_my_name_is_from_someone_unknown_is_an_introduction(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    people = People({})
    c, _ = await client(aiohttp_client, base, people, PeopleStore(people, str(tmp_path / "p.json")),
                        FakeVoiceId(needed=1), cards=Cards())
    said = (await post(c, turn("My name is Jane")))["message"]["content"]
    assert said.startswith("Nice to meet you, Jane!") and people.names["jane"] == "Jane"


def test_aliases_persist_and_go_with_the_person(tmp_path):
    people = People({})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("jane", "Jane")
    store.link("jdoe", "jane")
    again = PeopleStore(People({}), str(tmp_path / "people.json"))
    assert again.resolve("jdoe") == "jane" and again.linked("jane")
    again.remove("jane")
    assert again.resolve("jdoe") is None and not again.linked("jane")
