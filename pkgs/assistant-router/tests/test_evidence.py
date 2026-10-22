"""Which devices arrive with whom (evidence.py), and the admin pages that confirm it."""
import pytest
from assistant_router import evidence as ev
from assistant_router.evidence import Evidence, normalise_mac, private_address
from assistant_router.household import Household
from assistant_router.people import People
from assistant_router.people_store import PeopleStore
from assistant_router.tap import Sessions, Taps, make_tap_app

BASE = "https://who.example.org"
ADMIN = {"X-Authentik-Username": "akadmin", "X-Authentik-Groups": "family|authentik Admins", "Origin": BASE}
PHONE = "3a:1f:00:aa:bb:01"
TABLET = "3a:1f:00:aa:bb:02"


class World:
    """The router's clock and the wall clock, moved together."""

    def __init__(self, path=None, key_path=None, days=30.0):
        self.mono, self.wall = 1000.0, 1_800_000_000.0
        self.ev = Evidence(path, key_path, days, clock=lambda: self.mono, wall=lambda: self.wall)

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds

    def arrive(self, mac, persons=(), delay=30.0):
        """People are identified, `delay` later the device turns up, the window closes, everyone leaves."""
        for p in persons:
            self.ev.person_seen(p, self.mono)
        self.advance(delay)
        self.ev.observe_devices([mac], self.mono)
        self.advance(ev.WINDOW_S + 20)
        self.ev.observe_devices([mac], self.mono)  # still there: not another arrival
        self.advance(ev.GAP_S + 400)

    def cycles(self, mac, persons, n):
        for _ in range(n):
            self.arrive(mac, persons)


def test_addresses_and_private_addresses():
    assert normalise_mac("3A-1F-00-AA-BB-01") == PHONE and normalise_mac("nonsense") is None
    assert private_address("3a:1f:00:aa:bb:01") and not private_address("38:1f:00:aa:bb:01")


def test_an_unclaimed_device_is_kept_as_a_hash_and_the_key_persists(tmp_path):
    db, key = str(tmp_path / "e.db"), str(tmp_path / "e.key")
    w = World(db, key)
    w.arrive(PHONE)
    ident = w.ev.devices()[0]["id"]
    assert ident.startswith("wifi:") and PHONE not in ident and PHONE.replace(":", "") not in ident
    w.ev._db.commit()
    raw = open(db, "rb").read()
    assert PHONE.encode() not in raw and PHONE.replace(":", "").encode() not in raw  # no address on disk
    assert oct((tmp_path / "e.key").stat().st_mode & 0o777) == "0o600"
    again = World(db, key)  # a restart: the same key, so the same device
    assert again.ev.device_id(PHONE) == ident and len(again.ev.devices()) == 1


def test_a_device_that_arrives_with_one_person_again_and_again_is_suggested(tmp_path):
    w = World()
    w.cycles(PHONE, ["alex"], 5)
    (found,) = w.ev.suggestions()
    assert found["likely"] == {"person": "alex", "score": 1.0, "together": 5}
    assert found["arrivals"] == 5 and found["private"] and found["short"] == found["id"][5:11]


def test_too_few_arrivals_or_too_few_together_is_no_suggestion():
    w = World()
    w.cycles(PHONE, ["alex"], ev.MIN_ARRIVALS - 1)
    assert w.ev.suggestions() == []
    w2 = World()
    w2.cycles(TABLET, ["alex"], 3)
    w2.cycles(TABLET, [], 4)  # it arrives with no one more often than not
    assert w2.ev.devices()[0]["arrivals"] == 7 and w2.ev.suggestions() == []


def test_a_device_that_arrives_with_different_people_is_shared_and_never_suggested():
    w = World()
    w.cycles(TABLET, ["alex"], 5)
    w.cycles(TABLET, ["sam"], 3)  # 3 of 8 arrivals: over the runner-up share
    assert w.ev.suggestions() == []
    assert [d["likely"] for d in w.ev.devices()] == [None]


def test_a_person_arriving_long_before_or_after_does_not_count():
    w = World()
    for _ in range(6):
        w.ev.person_seen("alex", w.mono)
        w.advance(ev.WINDOW_S + 300)  # far outside the window
        w.ev.observe_devices([PHONE], w.mono)
        w.advance(ev.WINDOW_S + 20)
        w.ev.observe_devices([PHONE], w.mono)
        w.advance(ev.GAP_S + 400)
    assert w.ev.suggestions() == []


def test_staying_identified_is_not_arriving_again():
    w = World()
    w.ev.person_seen("alex", w.mono)
    for _ in range(10):  # seen every minute: one arrival, the first
        w.advance(60)
        w.ev.person_seen("alex", w.mono)
    assert list(w.ev._arrived) == ["alex"] and w.ev._arrived["alex"] == 1000.0


def test_claiming_needs_the_device_on_the_network_and_gives_its_address(tmp_path):
    db, key = str(tmp_path / "e.db"), str(tmp_path / "e.key")
    w = World(db, key)
    w.cycles(PHONE, ["alex"], 5)
    ident = w.ev.devices()[0]["id"]
    again = World(db, key)  # the router restarted: the hash cannot be turned back into an address
    assert again.ev.claimable(ident) is None and again.ev.claim(ident, "alex") is None
    assert w.ev.claimable(ident) == PHONE
    assert w.ev.claim(ident, "alex") == PHONE
    assert w.ev.devices() == [] and w.ev.claim(ident, "sam") is None  # claimed once
    assert w.ev.claim("wifi:nothing", "alex") is None


def test_ignored_devices_are_not_suggested():
    w = World()
    w.cycles(PHONE, ["alex"], 5)
    ident = w.ev.devices()[0]["id"]
    assert w.ev.ignore(ident) and w.ev.suggestions() == [] and not w.ev.ignore("wifi:nothing")


def test_unclaimed_devices_expire_and_claimed_ones_stay():
    w = World(days=30.0)
    w.cycles(PHONE, ["alex"], 5)
    w.cycles(TABLET, ["sam"], 1)
    w.ev.claim(w.ev.device_id(PHONE), "alex")
    w.advance(31 * 86400)
    assert w.ev.prune() == 1  # the tablet; the claimed phone is a person's
    assert w.ev._db.execute("SELECT person FROM devices").fetchall() == [("alex",)]
    assert w.ev._db.execute("SELECT COUNT(*) FROM together WHERE device != ?", (w.ev.device_id(PHONE),)).fetchone()[0] == 0


def test_a_person_forgotten_or_merged_takes_their_evidence_with_them():
    w = World()
    w.cycles(PHONE, ["alex"], 5)
    w.ev.rekey("alex", "alexander")
    assert w.ev.suggestions()[0]["likely"]["person"] == "alexander"
    w.ev.forget_person("alexander")
    assert w.ev.suggestions() == []


def test_two_people_that_one_device_arrives_with_are_one_person_met_twice():
    w = World()
    w.cycles(PHONE, ["unknown_giddy_goblin"], 4)
    w.cycles(PHONE, ["alex"], 4)  # shared: 4 and 4 of 8 arrivals
    assert w.ev.suggestions() == []
    (pair,) = w.ev.shared()
    assert {pair["a"], pair["b"]} == {"unknown_giddy_goblin", "alex"} and pair["score"] == 0.5
    assert w.ev.shared(0.6) == []


def test_people_report_who_was_identified_even_a_stranger():
    people = People({"kiril": "Kiril", "unknown_x_y": "Unknown X Y"})
    people.strangers.add("unknown_x_y")
    told = []
    people.on_identified = lambda person, now: told.append(person)
    people.observe("Hall", "face", "kiril", 0.9, 1.0)
    people.observe("Hall", "face", "unknown_x_y", 0.9, 1.0)  # a stranger is never present, but arrives
    people.observe_phone("Hall", "kiril", -60, 1.0)
    people.observe("Hall", "face", "ghost", 0.9, 1.0)  # nobody here
    people.observe("Hall", "face", "kiril", 7.0, 1.0)  # not a confidence
    assert told == ["kiril", "unknown_x_y", "kiril"] and list(people.here("Hall", 1.0)) == ["kiril"]


# ── Household and the admin pages ────────────────────────────────────────────

class Learned:
    def __init__(self):
        self.phones, self.listeners = {}, []

    def learn(self, mac, key):
        self.phones[mac] = key
        return True


@pytest.fixture
def home(tmp_path):
    w = World()
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("alex", "Alex")
    store.set_kind("alex", "guest")
    store.add("unknown_giddy_goblin", "Unknown Giddy Goblin")
    store.set_kind("unknown_giddy_goblin", "unknown")
    taps = Taps(BASE, people, lambda: 1000.0, phones_path=str(tmp_path / "phones.json"), store=store,
                runtime=store.has, remember=store.add, sessions=Sessions(b"k" * 32),
                groups_header="X-Authentik-Groups", admins=("authentik Admins",))
    household = Household(people, store, taps=taps, evidence=w.ev, forget=lambda key: None)
    return household, w, taps, people


def test_confirming_teaches_the_device_like_a_tapped_phone(home):
    household, w, taps, _ = home
    w.cycles(PHONE, ["alex"], 5)
    ident = w.ev.devices()[0]["id"]
    assert not household.claim_device(ident, "nobody") and not household.claim_device("wifi:x", "alex")
    assert household.claim_device(ident, "alex")
    assert taps.phones == {PHONE: "alex"} and w.ev.devices() == []


async def test_a_shared_device_makes_an_unknown_a_merge_suggestion(home):
    household, w, *_ = home
    w.cycles(PHONE, ["unknown_giddy_goblin"], 4)
    w.cycles(PHONE, ["alex"], 4)
    (pair,) = await household.duplicates(fresh=True)
    assert (pair["key"], pair["into"], pair["why"]) == ("unknown_giddy_goblin", "alex", "shares a device")
    assert pair["sighting"] is None


async def test_the_devices_page_shows_a_suggestion_without_the_address(aiohttp_client, home):
    household, w, taps, people = home
    w.cycles(PHONE, ["alex"], 5)
    w.cycles(TABLET, ["alex", "kiril"], 1)
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Hall"], household=household))
    text = await (await c.get("/admin/devices", headers=ADMIN)).text()
    assert "Arrives with <b>Alex</b>: 5 of 5 times (100%)" in text and "It is Alex" in text
    assert PHONE not in text and PHONE.replace(":", "") not in text and TABLET not in text
    assert "/admin/devices" in await (await c.get("/admin", headers=ADMIN)).text()
    ident = w.ev.suggestions()[0]["id"]
    r = await c.post("/admin/devices/claim", data={"device": ident, "person": "alex"}, headers=ADMIN)
    assert r.status == 200 and taps.phones == {PHONE: "alex"}
    other = w.ev.devices()[0]["id"]
    r = await c.post("/admin/devices/ignore", data={"device": other}, headers=ADMIN, allow_redirects=False)
    assert r.status == 302 and "No devices yet" in await (await c.get("/admin/devices", headers=ADMIN)).text()


async def test_only_admins_from_this_site_see_or_change_devices(aiohttp_client, home):
    household, w, taps, _ = home
    w.cycles(PHONE, ["alex"], 5)
    ident = w.ev.devices()[0]["id"]
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Hall"], household=household))
    plain = {"X-Authentik-Username": "sam", "X-Authentik-Groups": "family", "Origin": BASE}
    evil = {**ADMIN, "Origin": "https://evil.example"}
    for headers in (plain, {}):
        assert (await c.get("/admin/devices", headers=headers)).status == 403
    for url in ("/admin/devices/claim", "/admin/devices/ignore"):
        for headers in (plain, {}, evil):
            r = await c.post(url, data={"device": ident, "person": "alex"}, headers=headers)
            assert r.status == 403, (url, headers)
    assert taps.phones == {} and w.ev.devices()[0]["id"] == ident


async def test_the_sensor_reports_devices_to_the_router(aiohttp_client, tmp_path):
    from assistant_router.body import Bodies
    from assistant_router.server import make_body_app
    w = World()
    c = await aiohttp_client(make_body_app(Bodies("You are Nabu."), clock=lambda: w.mono, people=People({}),
                                           evidence=w.ev))
    sensor = await c.ws_connect("/v1/body")
    await sensor.send_json({"hello": {"room": "house", "kind": "house-sensor", "proto": 1}})
    await sensor.send_json({"devices": [PHONE, "junk", 7, TABLET]})
    for _ in range(100):
        if len(w.ev.devices()) == 2:
            break
        import asyncio
        await asyncio.sleep(0.01)
    assert sorted(d["arrivals"] for d in w.ev.devices()) == [1, 1]  # the junk is dropped
    await sensor.close()
