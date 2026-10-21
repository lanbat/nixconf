"""Meeting Nabu: enrolment by just talking (identity.py, server.py)."""
import pytest
from assistant_router.body import Bodies
from assistant_router.cards import READ_S, Cards
from assistant_router.identity import ASK_AGAIN, ASK_NAME, DECLINED, OFFER, answer, command
from assistant_router.people import Assessment, People
from assistant_router.people_store import PeopleStore
from assistant_router.server import Config, make_app
from assistant_router.state import State

from test_server import FakeFrigate, FakeVoiceId, ForgettingFrigate, Sock, capture_ok, fakes, post, req  # noqa: F401


@pytest.mark.parametrize("text,what", [
    ("Remember me", ("remember", None)),
    ("Okay Nabu, please remember me.", ("remember", None)),
    ("Learn my voice", ("remember", None)),
    ("Forget my card", ("forget_card", None)),
    ("Call me Kiki", ("call_me", "Kiki")),
    ("My name is Mary-Ann", ("call_me", "Mary-Ann")),
    ("Remember me as Jane", ("remember", "Jane")),
    ("Remember this card", ("remember_card", None)),
    ("This is my card", ("remember_card", None)),
    ("Forget me", ("forget_me", None)),
    ("Please forget everything about me", ("forget_me", None)),
    ("Remember to buy milk", None),
    ("Forget about the milk", None),
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
    assert said == "Nice to meet you, Jane!"  # one sentence: the rest is learned quietly
    assert people.names["jane"] == "Jane" and store.has("jane")
    assert store.kind("jane") == "heard"  # spelled as heard: on the admins' list to confirm
    assert frigate.enrolled[0][0] == "jane"
    assert {"nfc_read": READ_S} not in sock.sent  # a card only when asked for: phone taps keep working
    # The next sentence is an ordinary request; it also completes the voice.
    more = (await post(c, turn("I like tea in the morning.")))["message"]["content"]
    assert "voice" not in more and voice.prints == {"jane"}
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
    assert said == "Got it, Kiril! I've added your face and voice."
    assert ("kiril", "Remember me") in voice.learned and {"nfc_read": READ_S} not in sock.sent
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
    assert said == "Got it, Kiril! I've added your face."
    assert voice.texts["kiril"] == ["Remember me"] and voice.learned == []
    # The next sentences are ordinary requests that finish the voiceprint quietly.
    assert "Lovely" not in (await post(c, turn("I like tea in the morning.")))["message"]["content"]
    await post(c, turn("And coffee after lunch."))
    assert voice.prints == {"kiril"}
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


async def test_an_unclear_short_answer_is_asked_again_once(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    people = People({})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    c, _ = await client(aiohttp_client, base, people, store, FakeVoiceId(), FakeFrigate(), Cards())
    first = (await post(c, turn("What's the capital of the sea?")))["message"]["content"]
    assert first.endswith(OFFER)
    again = (await post(c, turn("Pardon?", [("What's the capital of the sea?", first)])))["message"]["content"]
    assert again == ASK_AGAIN
    assert (await post(c, turn("Jane")))["message"]["content"] == "Nice to meet you, Jane!"


async def test_a_request_instead_of_a_name_goes_on(aiohttp_client, fakes, tmp_path):
    _, _, cloud_reply, base = fakes
    people = People({})
    c, _ = await client(aiohttp_client, base, people, PeopleStore(people, str(tmp_path / "p.json")),
                        FakeVoiceId(), FakeFrigate(), Cards())
    first = (await post(c, turn("What's the capital of the sea?")))["message"]["content"]
    cloud_reply["v"] = {"role": "assistant", "content": "It's 3 pm."}
    said = (await post(c, turn("What time is it now?", [("What's the capital of the sea?", first)])))
    assert said["message"]["content"] == "It's 3 pm." and people.names == {}


async def test_remember_my_card_and_forget_me_need_no_cloud(aiohttp_client, fakes, tmp_path):
    seen, _, _, base = fakes
    import time
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("jane", "Jane")
    voice, cards = FakeVoiceId(enrolled=["jane"]), Cards()
    c, sock = await client(aiohttp_client, base, people, store, voice, ForgettingFrigate(), cards)
    people.observe("Bedroom 1", "tap", "jane", 1.0, time.monotonic())
    said = (await post(c, turn("Remember my card")))["message"]["content"]
    assert said == "Okay, Jane, hold your card on top of my head." and {"nfc_read": READ_S} in sock.sent
    clouds = len(seen.get("cloud", []))
    said = (await post(c, turn("Forget me")))["message"]["content"]
    assert "Jane" in said and "forgotten" in said and voice.forgotten == ["jane"]
    assert len(seen.get("cloud", [])) == clouds  # handled by the router itself
