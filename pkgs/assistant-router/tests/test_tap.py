import json

import pytest
from assistant_router.body import Bodies
from assistant_router.people import People
from assistant_router.server import make_body_app
from assistant_router.tap import Taps, arp_lookup, display_name, make_tap_app, person_key

BASE = "https://who.example.org"
ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.50     0x1         0x2         a2:3b:4c:5d:6e:7f     *        eth0
192.168.1.51     0x1         0x0         00:00:00:00:00:00     *        eth0
"""


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def setup(tmp_path):
    arp = tmp_path / "arp"
    arp.write_text(ARP)
    people = People({"kiril": "Kiril"})
    clock = Clock()
    taps = Taps(BASE, people, clock, phones_path=str(tmp_path / "phones.json"), arp_path=str(arp))
    return taps, people, clock, tmp_path


def nonce(link):
    return link.split("?n=")[1]


def test_a_link_lasts_one_to_two_minutes_and_one_room(setup):
    taps, _, clock, _ = setup
    n = nonce(taps.link("Kitchen"))
    assert taps.link("Kitchen").startswith(f"{BASE}/tap/kitchen?n=")
    assert taps.valid("kitchen", n)
    assert not taps.valid("Bedroom", n)
    clock.t += 60
    assert taps.valid("Kitchen", n)
    clock.t += 60
    assert not taps.valid("Kitchen", n)


def test_a_tap_names_them_there_and_learns_the_phone(setup):
    taps, people, clock, tmp_path = setup
    told = []
    taps.listeners.append(told.append)
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "kiril", "Kiril M", "192.168.1.50", ["Kitchen"])
    assert got == {"key": "kiril", "name": "Kiril", "room": "Kitchen", "phone": True, "stale": False}
    a = people.assess("Kitchen", clock())
    assert a.speaker == "kiril" and a.how == "phone tap"
    assert json.loads((tmp_path / "phones.json").read_text()) == {"a2:3b:4c:5d:6e:7f": "kiril"}
    assert told == [{"a2:3b:4c:5d:6e:7f": "kiril"}]


def test_someone_new_is_added_by_their_login(setup):
    taps, people, _, _ = setup
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "Jane.Doe", "Jane <b>Doe</b>", "", ["Kitchen"])
    assert got["key"] == "jane_doe" and people.names["jane_doe"] == "Jane bDoeb"
    assert got["phone"] is False


def test_a_stale_link_changes_nothing(setup):
    taps, people, clock, _ = setup
    old = nonce(taps.link("Kitchen"))
    clock.t += 180
    got = taps.tap("kitchen", old, "jane", "Jane", "192.168.1.50", ["Kitchen"])
    assert got["stale"] and "jane" not in people.names and taps.phones == {}
    got = taps.tap("garage", nonce(taps.link("Garage")), "kiril", "Kiril", "192.168.1.50", ["Kitchen"])
    assert got["stale"] and taps.phones == {}


def test_names_and_keys_are_clean():
    assert person_key("  Ä x--y ") == "x_y"
    assert person_key("!!!") is None
    assert display_name("Robert'); DROP", "bob") == "Robert' DROP"
    assert display_name("<>", "bob") == "bob"
    assert len(display_name("a" * 99, "x")) == 40


def test_only_complete_arp_entries(tmp_path):
    arp = tmp_path / "arp"
    arp.write_text(ARP)
    assert arp_lookup("192.168.1.50", str(arp)) == "a2:3b:4c:5d:6e:7f"
    assert arp_lookup("192.168.1.51", str(arp)) is None
    assert arp_lookup("10.0.0.1", str(tmp_path / "missing")) is None


def test_a_person_keeps_their_newest_phones(setup):
    taps, _, _, _ = setup
    for i in range(6):
        taps.learn(f"02:00:00:00:00:0{i}", "kiril")
    assert sorted(taps.phones) == [f"02:00:00:00:00:0{i}" for i in range(2, 6)]
    assert taps.forget_phones("kiril") == 4 and taps.phones == {}


def test_learned_phones_survive_a_restart(setup):
    taps, people, clock, tmp_path = setup
    taps.learn("02:00:00:00:00:01", "kiril")
    again = Taps(BASE, people, clock, phones_path=str(tmp_path / "phones.json"))
    assert again.phones == {"02:00:00:00:00:01": "kiril"}


async def test_the_page(aiohttp_client, setup):
    taps, _, _, _ = setup
    forgotten = []

    async def forget(key):
        forgotten.append(key)
        return False

    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], forget))
    me = {"X-Authentik-Username": "kiril", "X-Authentik-Name": "Kiril", "X-Forwarded-For": "192.168.1.50"}
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers=me)
    text = await r.text()
    assert r.status == 200 and "Hi Kiril!" in text and "in the Kitchen" in text and "Forget my phone" in text
    assert "learns" not in text  # no voice-id or Frigate to learn with
    taps.learns = ("voice", "face")
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers=me)
    assert "For two minutes, Nabu learns your voice and your face there" in await r.text()
    r = await c.get("/tap/kitchen?n=nope", headers=me)
    assert "expired" in await r.text()
    assert (await c.get("/tap/kitchen?n=x")).status == 403  # not signed in

    r = await c.post("/forget-phone", headers={**me, "Origin": "https://evil.example"})
    assert r.status == 403 and taps.phones
    r = await c.post("/forget-phone", headers={**me, "Origin": BASE})
    assert r.status == 200 and taps.phones == {}
    r = await c.post("/forget-me", headers={**me, "Referer": f"{BASE}/"})
    assert r.status == 200 and forgotten == ["kiril"] and "home's settings" in await r.text()


async def test_robots_get_links_and_the_house_sensor_gets_phones(aiohttp_client, setup):
    taps, people, clock, _ = setup
    c = await aiohttp_client(make_body_app(Bodies(""), clock=clock, people=people, taps=taps))
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    assert "body_hello" in await robot.receive_json()
    assert await robot.receive_json() == {"tap": {"url": taps.link("Kitchen")}}

    house = await c.ws_connect("/v1/body")
    await house.send_json({"hello": {"room": "house", "kind": "house-sensor", "proto": 1}})
    assert await house.receive_json() == {"phones": {}}
    taps.learn("02:00:00:00:00:01", "kiril")
    assert await house.receive_json() == {"phones": {"02:00:00:00:00:01": "kiril"}}

    # A room sensor that reads an access point's clients asks for them too;
    # one that doesn't isn't sent them.
    import asyncio
    ap = await c.ws_connect("/v1/body")
    await ap.send_json({"hello": {"room": "Office", "kind": "room-sensor", "proto": 1, "wifi": True}})
    assert await ap.receive_json() == {"phones": {"02:00:00:00:00:01": "kiril"}}
    plain = await c.ws_connect("/v1/body")
    await plain.send_json({"hello": {"room": "Kitchen", "kind": "room-sensor", "proto": 1}})
    taps.learn("02:00:00:00:00:02", "kiril")
    assert (await ap.receive_json())["phones"]["02:00:00:00:00:02"] == "kiril"
    with __import__("pytest").raises(asyncio.TimeoutError):
        await asyncio.wait_for(plain.receive_json(), 0.3)


def test_renaming_yourself_in_authentik_renames_you_here(setup, tmp_path):
    taps, people, _, _ = setup
    runtime = {"akadmin"}
    renamed = []

    def remember(key, name):
        renamed.append((key, name))
        people.add(key, name)

    taps._remember, taps._runtime = remember, runtime.__contains__
    people.add("akadmin", "authentik Default Admin")
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "akadmin", "Kiril", "", ["Kitchen"])
    assert got["name"] == "Kiril" and people.names["akadmin"] == "Kiril"
    # The same name again, no name header, or a profile's person: nothing changes.
    taps.tap("kitchen", nonce(taps.link("Kitchen")), "akadmin", "Kiril", "", ["Kitchen"])
    taps.refresh_name("akadmin", "", "akadmin")
    taps.refresh_name("kiril", "Someone Else", "kiril")
    assert renamed == [("akadmin", "Kiril")] and people.names["kiril"] == "Kiril"


def test_a_stale_link_renames_nobody(setup):
    taps, people, clock, _ = setup
    taps._runtime = {"akadmin"}.__contains__
    people.add("akadmin", "Old Name")
    old = nonce(taps.link("Kitchen"))
    clock.t += 180
    taps.tap("kitchen", old, "akadmin", "New Name", "", ["Kitchen"])
    assert people.names["akadmin"] == "Old Name"


async def test_opening_the_page_picks_up_the_new_name(aiohttp_client, setup):
    taps, people, _, _ = setup
    taps._runtime = {"akadmin"}.__contains__
    people.add("akadmin", "authentik Default Admin")
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    r = await c.get("/", headers={"X-Authentik-Username": "akadmin", "X-Authentik-Name": "Kiril"})
    assert "Hi Kiril!" in await r.text() and people.names["akadmin"] == "Kiril"


def test_a_first_tap_links_the_person_nabu_met_by_voice(setup, tmp_path):
    from assistant_router.people import Assessment
    from assistant_router.people_store import PeopleStore
    taps, people, clock, _ = setup
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("jane", "Jane")
    taps._store, taps._runtime, taps._remember = store, store.has, store.add
    people.remember("Kitchen", Assessment(speaker="jane", posterior=0.9, how="voice"), clock())
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "jdoe", "Jane Doe", "", ["Kitchen"])
    assert got["key"] == "jane" and store.resolve("jdoe") == "jane"
    assert people.names["jane"] == "Jane Doe" and "jdoe" not in people.names  # one person, Authentik's name
    clock.t += 600  # later, nobody speaking: the login still names her
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "jdoe", "Jane Doe", "", ["Kitchen"])
    assert got["key"] == "jane"


def test_a_login_with_nobody_recognised_is_a_new_person(setup, tmp_path):
    from assistant_router.people_store import PeopleStore
    taps, people, _, _ = setup
    store = PeopleStore(people, str(tmp_path / "people.json"))
    taps._store, taps._runtime, taps._remember = store, store.has, store.add
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "sam", "Sam", "", ["Kitchen"])
    assert got["key"] == "sam" and people.names["sam"] == "Sam" and store.resolve("sam") is None
