"""Unknown people: a face matched to no one gets a name of its own (unknowns.py)."""
import asyncio
import base64
import itertools
import random

import pytest
from assistant_router.body import Bodies
from assistant_router.frigate import FrigateError, safe_name
from assistant_router.household import Household
from assistant_router.people import Assessment, People
from assistant_router.people_store import PeopleStore
from assistant_router.server import Config, make_app, make_body_app
from assistant_router.sightings import Sightings
from assistant_router.state import State
from assistant_router.tap import Taps
from assistant_router.unknowns import ADJECTIVES, NOUNS, Strangers, load_words, make_name, words

from test_server import FakeVoiceId, Sock, capture_ok, fakes, post, req  # noqa: F401


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Frigate:
    """Enough of Frigate's face library for strangers: enrol, register, thumbnails, recognise."""

    def __init__(self, fail=False):
        self.fail = fail
        self.faces = {}  # name -> [images]
        self.recognised = None  # what recognize() answers, when set
        self.thumbnails = {}

    async def enroll(self, name, image, filename="capture.jpg"):
        safe_name(name)
        if self.fail:
            raise FrigateError(f"POST /api/faces/{name}/register", status=400)
        self.faces.setdefault(name, []).append(image)
        return {"success": True}

    async def register(self, name, image, filename="capture.jpg"):
        if self.fail:
            raise FrigateError("register", status=400)
        self.faces.setdefault(name, []).append(image)
        return {"success": True}

    async def thumbnail(self, event):
        if event not in self.thumbnails:
            raise FrigateError("thumbnail", status=404)
        return self.thumbnails[event]

    async def recognize(self, image, filename="probe.jpg"):
        if self.recognised is not None:
            return self.recognised
        return {"success": False, "name": None, "confidence": None}

    async def list(self):
        return {k: [f"{k}_{i}.webp" for i in range(len(v))] for k, v in self.faces.items()}

    async def delete(self, name, ids):
        self.faces.pop(name, None)
        return {"success": True}


def build(tmp_path, clock=None, frigate=None, **kw):
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    frigate = frigate if frigate is not None else Frigate()
    clock = clock or Clock()
    kw.setdefault("rng", random.Random(1))
    return people, store, frigate, clock, Strangers(people, store, frigate, clock=clock, **kw)


# ── the names ────────────────────────────────────────────────────────────────

def test_every_combination_is_an_unknown_two_words_and_a_valid_key():
    for adjective, noun in itertools.product(ADJECTIVES, NOUNS):
        key, name = make_name(set(), random.Random(0), (adjective,), (noun,))
        assert name == f"Unknown {adjective.capitalize()} {noun.capitalize()}"
        assert key == f"unknown_{adjective}_{noun}"
        assert safe_name(key) == key and len(key) <= 40


def test_a_name_nobody_has_is_made_and_a_number_when_they_run_out():
    taken = {"unknown_only_one"}
    key, name = make_name(taken, random.Random(0), ("only",), ("one",))
    assert key == "unknown_only_one_2" and name == "Unknown Only One 2" and len(key) <= 40
    assert make_name(taken | {key}, random.Random(0), ("only",), ("one",))[0] == "unknown_only_one_3"


def test_a_sites_words_replace_the_built_in_ones_when_usable(tmp_path):
    assert words(["Fuzzy", "no good!", 5, "x"], ADJECTIVES) == ("fuzzy",)
    assert words([], ADJECTIVES) == ADJECTIVES and words(None, NOUNS) == NOUNS
    f = tmp_path / "w.json"
    f.write_text('{"adjectives": ["Cosy"], "nouns": []}')
    assert load_words(str(f)) == (("cosy",), NOUNS)
    assert load_words(str(tmp_path / "missing.json")) == (ADJECTIVES, NOUNS)


# ── strangers ────────────────────────────────────────────────────────────────

async def test_an_unknown_face_becomes_a_person_in_the_library_and_the_store(tmp_path):
    people, store, frigate, _, strangers = build(tmp_path)
    key = await strangers.meet("Hall", b"face1")
    assert key.startswith("unknown_") and people.names[key].startswith("Unknown ")
    assert frigate.faces == {key: [b"face1"]}
    assert store.kind(key) == "unknown" and key in people.strangers and store.has(key)
    assert strangers.recent("hall") == key
    # Remembered across a restart, as a stranger.
    again = People({})
    PeopleStore(again, str(tmp_path / "people.json"))
    assert again.names[key] == people.names[key] and key in again.strangers


async def test_the_same_person_in_the_room_gets_more_pictures_not_another_name(tmp_path):
    people, _, frigate, clock, strangers = build(tmp_path)
    key = await strangers.meet("Hall", b"a")
    await strangers.meet("Hall", b"b")  # too soon after the first picture: nothing added
    clock.t += 10
    assert await strangers.meet("Hall", b"c") == key
    assert frigate.faces[key] == [b"a", b"c"] and len(people.strangers) == 1
    clock.t += strangers.RECENT_S + 10  # long after: someone else, a new name
    other = await strangers.meet("Hall", b"d")
    assert other != key and len(people.strangers) == 2


async def test_pictures_per_stranger_are_capped(tmp_path):
    _, _, frigate, clock, strangers = build(tmp_path)
    key = await strangers.meet("Hall", b"0")
    for i in range(1, 20):
        clock.t += 10
        await strangers.meet("Hall", str(i).encode())
    assert len(frigate.faces[key]) == strangers.MAX_IMAGES


async def test_new_strangers_are_rate_limited_and_capped(tmp_path):
    people, _, _, clock, strangers = build(tmp_path, max_people=2)
    assert await strangers.meet("Hall", b"1")
    assert await strangers.meet("Porch", b"2") is None  # a second one straight after: not yet
    clock.t += 10
    assert await strangers.meet("Porch", b"2")
    clock.t += 10
    assert await strangers.meet("Gate", b"3") is None  # the limit: none are dropped, none are added
    assert len(people.strangers) == 2


async def test_a_picture_the_library_refuses_makes_nobody(tmp_path):
    people, store, frigate, _, strangers = build(tmp_path, frigate=Frigate(fail=True))
    assert await strangers.meet("Hall", b"x") is None
    assert set(people.names) == {"kiril"} and not people.strangers and not store.has("kiril")


async def test_a_stranger_is_never_present_greeted_or_spoken_to_by_name(tmp_path):
    people, _, _, clock, strangers = build(tmp_path)
    key = await strangers.meet("Hall", b"x")
    people.observe("Hall", "face", key, 0.9, clock())
    assert people.here("Hall", clock()) == {} and people.line("Hall", clock()) is None
    people.observe("Hall", "face", "kiril", 0.9, clock())
    assert list(people.here("Hall", clock())) == ["kiril"]


async def test_the_admin_page_lists_them_as_unknown_and_can_name_them(tmp_path):
    people, store, _, _, strangers = build(tmp_path)
    key = await strangers.meet("Hall", b"x")
    home = Household(people, store, frigate=Frigate())
    mine = [p for p in await home.everyone() if p["key"] == key]
    assert mine and mine[0]["label"] == "Unknown" and mine[0]["editable"]
    assert home.rename(key, "Aunt Jo") and people.names[key] == "Aunt Jo"
    assert store.kind(key) == "guest" and key not in people.strangers


# ── a body's face probe, and a camera ────────────────────────────────────────

async def body_client(aiohttp_client, tmp_path, frigate, strangers=None, people=None, store=None):
    (tmp_path / "pictures").mkdir(exist_ok=True)
    sightings = Sightings(str(tmp_path / "s.db"), str(tmp_path / "pictures"))
    bodies = Bodies("You are Nabu.")
    bodies.events = asyncio.Queue()
    if strangers is not None:
        strangers.sightings = sightings
    c = await aiohttp_client(make_body_app(bodies, clock=Clock(), people=people, frigate=frigate,
                                           sightings=sightings, strangers=strangers))
    return c, sightings, bodies


async def probe(robot, picture=b"\xff\xd8face"):
    await robot.send_json({"face_probe": base64.b64encode(picture).decode(), "why": "seen"})
    for _ in range(50):
        got = await robot.receive_json()
        if "face_result" in got:
            return got["face_result"]


async def test_a_robot_that_sees_a_stranger_records_them_by_their_name(aiohttp_client, tmp_path):
    people, store, frigate, clock, strangers = build(tmp_path, clock=Clock())
    c, sightings, bodies = await body_client(aiohttp_client, tmp_path, frigate, strangers, people)
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Bedroom 1", "kind": "stackchan", "proto": 1}})
    await robot.receive_json()
    # The library knows the face as nobody: a new Unknown, but the robot is told nobody it knows.
    assert await probe(robot) == {"person": None, "confidence": None}
    (found,) = sightings.search()
    key = found["person"]
    assert key in people.strangers and found["place"] == "Bedroom 1" and found["picture"]
    room, event = await asyncio.wait_for(bodies.events.get(), 1)
    assert event["sighting"]["person"] == people.names[key]
    assert not sightings.search(person="")  # nothing is "nobody known" any more
    # The library has learnt the face: it names them next time, still as a stranger.
    frigate.recognised = {"success": True, "name": key, "confidence": 0.8}
    assert await probe(robot) == {"person": None, "confidence": 0.8}
    assert [f["person"] for f in sightings.search()] == [key, key]
    assert len(people.strangers) == 1 and len(frigate.faces) == 1
    await robot.close()


async def test_without_the_feature_a_stranger_stays_nobody_known(aiohttp_client, tmp_path):
    people = People({"kiril": "Kiril"})
    c, sightings, _ = await body_client(aiohttp_client, tmp_path, Frigate(), None, people)
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Bedroom 1", "kind": "stackchan", "proto": 1}})
    await robot.receive_json()
    await probe(robot)
    assert [f["person"] for f in sightings.search()] == [None]


async def test_a_camera_face_matched_to_no_one_is_named_from_its_thumbnail(aiohttp_client, tmp_path):
    people, store, frigate, clock, strangers = build(tmp_path)
    event = "1792518459.451025-ab12cd"
    frigate.thumbnails[event] = b"thumb"
    c, sightings, _ = await body_client(aiohttp_client, tmp_path, frigate, strangers, people)
    cam = await c.ws_connect("/v1/body")
    await cam.send_json({"hello": {"room": "Driveway", "kind": "face-source", "proto": 1}})
    for _ in range(3):  # the mapper repeats the event while the face is there: one stranger
        await cam.send_json({"faces": [], "unknown": [{"event": event}]})
    for _ in range(100):
        if sightings.search():
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    (found,) = sightings.search()
    assert found["place"] == "Driveway" and found["source"] == "camera" and found["event"] == event
    assert found["person"] in people.strangers and frigate.faces[found["person"]] == [b"thumb"]
    await cam.close()


# ── identifying themselves ───────────────────────────────────────────────────

async def test_telling_nabu_their_name_renames_the_stranger_in_place(aiohttp_client, fakes, tmp_path):
    _, _, _, base = fakes
    people, store, frigate, clock, strangers = build(tmp_path)
    key = await strangers.meet("Bedroom 1", b"face")
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="cloud-first")
    bodies = Bodies("You are Nabu.")
    bodies.connect("Bedroom 1", Sock())
    app = make_app(cfg, State(), people=people, people_store=store, frigate=frigate, bodies=bodies,
                   capture_face=capture_ok, voice=FakeVoiceId(), strangers=strangers, clock=clock)
    c = await aiohttp_client(app)
    said = (await post(c, req("Remember me as Jane")))["message"]["content"]
    assert said == "Nice to meet you, Jane!"
    assert people.names[key] == "Jane" and store.kind(key) == "heard" and key not in people.strangers
    assert set(people.names) == {"kiril", key}  # nobody new: the stranger is Jane
    assert strangers.recent("Bedroom 1") is None


def test_a_login_tapped_where_a_stranger_was_seen_is_them(tmp_path):
    people, store, frigate, clock, strangers = build(tmp_path)
    key = asyncio.run(strangers.meet("Kitchen", b"face"))
    taps = Taps("https://who.example", people, clock, phones_path=str(tmp_path / "phones.json"),
                arp_path=str(tmp_path / "arp"), store=store, runtime=store.has, remember=store.add)
    taps.strangers = strangers
    got = taps.tap("kitchen", taps.link("Kitchen").split("?n=")[1], "jdoe", "Jane Doe", "", ["Kitchen"])
    assert got["key"] == key and people.names[key] == "Jane Doe"
    assert store.kind(key) == "account" and store.resolve("jdoe") == key and key not in people.strangers
    assert set(people.names) == {"kiril", key}


def test_a_login_where_a_voice_was_just_known_links_the_voice_first(tmp_path):
    people, store, frigate, clock, strangers = build(tmp_path)
    asyncio.run(strangers.meet("Kitchen", b"face"))
    store.add("jane", "Jane")
    store.set_kind("jane", "heard")
    taps = Taps("https://who.example", people, clock, phones_path=str(tmp_path / "phones.json"),
                arp_path=str(tmp_path / "arp"), store=store, runtime=store.has, remember=store.add)
    taps.strangers = strangers
    people.remember("Kitchen", Assessment(speaker="jane", posterior=0.9, how="voice"), clock())
    got = taps.tap("kitchen", taps.link("Kitchen").split("?n=")[1], "jdoe", "Jane Doe", "", ["Kitchen"])
    assert got["key"] == "jane" and len(people.strangers) == 1
