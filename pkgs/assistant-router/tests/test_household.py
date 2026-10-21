import pytest

from assistant_router.cards import Cards
from assistant_router.frigate import FrigateError
from assistant_router.household import Household
from assistant_router.irks import Irks
from assistant_router.people import People
from assistant_router.people_store import PeopleStore
from assistant_router.tap import Sessions, Taps, make_tap_app

BASE = "https://who.example.org"
ADMIN = {"X-Authentik-Username": "akadmin", "X-Authentik-Groups": "family|authentik Admins",
         "Origin": BASE}
IRK = "0123456789abcdef0123456789abcdef"


class Voice:
    def __init__(self, prints=()):
        self.prints = set(prints)
        self.merged = []

    async def people(self):
        return sorted(self.prints)

    async def merge(self, key, into):
        if key not in self.prints:
            return False
        self.prints.discard(key)
        self.prints.add(into)
        self.merged.append((key, into))
        return True


class Faces:
    def __init__(self, faces=None, fail=False):
        self.faces = faces or {}
        self.fail = fail
        self.moved = []

    async def list(self):
        if self.fail:
            raise FrigateError("GET /api/faces")
        return {k: list(v) for k, v in self.faces.items()}

    async def move(self, key, into):
        n = len(self.faces.get(key, []))
        if n:
            self.faces.setdefault(into, []).extend(self.faces.pop(key))
            self.moved.append((key, into))
        return n


@pytest.fixture
def home(tmp_path):
    people = People({"kiril": "Kiril"})  # the profile's person
    store = PeopleStore(people, str(tmp_path / "people.json"))
    for key, name, kind in (("akadmin", "Kiril A", "account"), ("guest_jane", "Jane", "guest"),
                            ("jaine", "Jaine", "heard"), ("carol", "Carol", None)):
        store.add(key, name)
        if kind:
            store.set_kind(key, kind)
    taps = Taps(BASE, people, lambda: 1000.0, phones_path=str(tmp_path / "phones.json"), store=store,
                runtime=store.has, remember=store.add, sessions=Sessions(b"k" * 32),
                groups_header="X-Authentik-Groups", admins=("authentik Admins",))
    taps.learn("02:00:00:00:00:01", "jaine")
    cards = Cards(str(tmp_path / "cards.json"))
    cards.cards["04A1B2C3"] = "jaine"
    irks = Irks(str(tmp_path / "irks.json"))
    irks.add("jaine", IRK)
    voice = Voice({"jaine", "kiril"})
    faces = Faces({"jaine": ["j1.webp", "j2.webp"], "akadmin": ["a1.webp"]})
    forgotten = []

    async def forget(key):
        forgotten.append(key)
        store.remove(key)
        return True

    household = Household(people, store, taps=taps, voice=voice, frigate=faces, cards=cards, irks=irks,
                          forget=forget)
    return household, people, store, taps, voice, faces, cards, irks, forgotten


async def test_everyone_is_labelled_with_how_nabu_knows_their_name(home):
    household = home[0]
    found = {p["key"]: p for p in await household.everyone()}
    assert [p["key"] for p in await household.everyone()] == ["kiril", "akadmin", "guest_jane", "carol", "jaine"]
    assert (found["kiril"]["label"], found["kiril"]["profile"], found["kiril"]["editable"]) == ("Account", True, False)
    assert (found["akadmin"]["label"], found["akadmin"]["editable"]) == ("Account", False)
    assert found["guest_jane"]["label"] == "Guest" and found["jaine"]["label"] == "Heard"
    assert found["carol"]["label"] == "Not recorded" and found["carol"]["editable"]
    assert found["jaine"]["learned"] == ["voice", "2 face pictures", "Wi-Fi", "Bluetooth", "card"]
    assert found["akadmin"]["learned"] == ["1 face picture"]


async def test_the_list_without_the_face_library(home):
    household, *_, faces, _, _, _ = home[:6] + home[6:]
    faces.fail = True
    found = {p["key"]: p for p in await household.everyone()}
    assert found["jaine"]["learned"] == ["voice", "Wi-Fi", "Bluetooth", "card"]


def test_fixing_a_heard_name_confirms_it(home):
    household, people, store, *_ = home
    assert household.rename("jaine", "Jane Doe")
    assert people.names["jaine"] == "Jane Doe" and store.kind("jaine") == "guest"
    # Accounts and the profile's people keep the names that come from there.
    assert not household.rename("akadmin", "Someone") and not household.rename("kiril", "Someone")
    assert not household.rename("jaine", "<>") and not household.rename("nobody", "X")


async def test_forgetting_someone_heard(home):
    household, people, store, taps, *_, forgotten = home
    assert await household.forget("jaine")
    assert forgotten == ["jaine"] and "jaine" not in people.names and taps.phones == {}
    assert not await household.forget("akadmin")  # an account forgets itself, on its own page


async def test_linking_moves_everything_learned_to_the_account(home):
    household, people, store, taps, voice, faces, cards, irks, _ = home
    store.link("jdoe", "jaine")
    moved = await household.link("jaine", "akadmin")
    assert moved == ["voice", "2 face pictures", "Wi-Fi", "Bluetooth", "card"]
    assert voice.merged == [("jaine", "akadmin")] and faces.faces["akadmin"] == ["a1.webp", "j1.webp", "j2.webp"]
    assert taps.phones == {"02:00:00:00:00:01": "akadmin"} and cards.cards == {"04A1B2C3": "akadmin"}
    assert irks.all() == {"akadmin": [IRK]}
    assert "jaine" not in people.names and store.resolve("jdoe") == "akadmin"
    # Into the profile's person too; never an account away, nor into a guest.
    assert await household.link("carol", "kiril") == []
    assert await household.link("akadmin", "kiril") is None
    assert await household.link("guest_jane", "guest_jane") is None
    store.add("bob", "Bob")
    assert await household.link("bob", "guest_jane") is None


async def test_the_admins_page(aiohttp_client, home):
    household, people, store, taps, *_ = home
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], household=household))
    r = await c.get("/admin", headers=ADMIN)
    text = await r.text()
    assert r.status == 200 and "People Nabu knows" in text
    assert 'tag-heard">Heard' in text and 'tag-guest">Guest' in text and 'tag-account">Account' in text
    assert "(the profile)" in text and "nothing learned yet" in text
    # Fix a name.
    r = await c.post("/admin/name", data={"key": "jaine", "name": "Jane D"}, headers=ADMIN, allow_redirects=False)
    assert r.status == 302 and people.names["jaine"] == "Jane D"
    # Link: asks first, then moves.
    r = await c.post("/admin/link", data={"key": "jaine", "into": "akadmin"}, headers=ADMIN)
    assert "can't be split apart again" in await r.text() and "jaine" in people.names
    r = await c.post("/admin/link", data={"key": "jaine", "into": "akadmin", "confirm": "yes"}, headers=ADMIN)
    assert r.status == 200 and "jaine" not in people.names and "Kiril A has it all now" in await r.text()
    # Forget.
    r = await c.post("/admin/forget", data={"key": "carol"}, headers=ADMIN)
    assert r.status == 200 and "carol" not in people.names


async def test_only_admins_from_this_page(aiohttp_client, home):
    household, people, _, taps, *_ = home
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], household=household))
    member = {**ADMIN, "X-Authentik-Groups": "family"}
    nobody = {"X-Authentik-Groups": "authentik Admins", "Origin": BASE}  # no login: not past the provider
    for headers in (member, nobody, {}):
        assert (await c.get("/admin", headers=headers)).status == 403
        r = await c.post("/admin/forget", data={"key": "jaine"}, headers=headers)
        assert r.status == 403 and "jaine" in people.names
    r = await c.post("/admin/forget", data={"key": "jaine"}, headers={**ADMIN, "Origin": "https://evil.example"})
    assert r.status == 403 and "jaine" in people.names
    # No admin groups configured: no page.
    taps.admins = ()
    assert (await c.get("/admin", headers=ADMIN)).status == 403


async def test_the_admins_search_sightings(aiohttp_client, home, tmp_path):
    from assistant_router.sightings import Sightings
    household, people, store, taps, voice, faces, *_ = home
    (tmp_path / "pics").mkdir()
    household.sightings = Sightings(str(tmp_path / "s.db"), str(tmp_path / "pics"))
    inside = household.sightings.record("Bedroom 1", "akadmin", "robot", 0.82, jpeg=b"\xff\xd8inside")
    outside = household.sightings.record("Driveway", "akadmin", "camera", 0.7, event="1792518459.451025-ab12cd")
    household.sightings.record("Kitchen", None, "screen", None)

    async def thumbnail(event):
        return b"\xff\xd8thumb of " + event.encode()

    faces.thumbnail = thumbnail
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], household=household))
    text = await (await c.get("/admin", headers=ADMIN)).text()
    assert 'href="/admin/sightings"' in text
    text = await (await c.get("/admin/sightings?days=30", headers=ADMIN)).text()
    assert "Driveway" in text and "Bedroom 1" in text and "nobody known" in text and "(robot, 0.82)" in text
    text = await (await c.get("/admin/sightings?person=akadmin&place=Driveway", headers=ADMIN)).text()
    assert "Driveway" in text and "Bedroom 1, " not in text and "nobody known</h2>" not in text
    text = await (await c.get("/admin/sightings?person=", headers=ADMIN)).text()
    assert "Kitchen" in text and "Driveway, " not in text
    r = await c.get(f"/admin/sightings/{inside}/picture", headers=ADMIN)
    assert r.status == 200 and await r.read() == b"\xff\xd8inside" and r.headers["Content-Type"] == "image/jpeg"
    r = await c.get(f"/admin/sightings/{outside}/picture", headers=ADMIN)
    assert await r.read() == b"\xff\xd8thumb of 1792518459.451025-ab12cd"
    assert (await c.get("/admin/sightings/999/picture", headers=ADMIN)).status == 404
    member = {**ADMIN, "X-Authentik-Groups": "family"}
    assert (await c.get("/admin/sightings", headers=member)).status == 403
    assert (await c.get(f"/admin/sightings/{inside}/picture", headers=member)).status == 403


async def test_linking_moves_sightings_too(home, tmp_path):
    from assistant_router.sightings import Sightings
    household = home[0]
    household.sightings = Sightings(str(tmp_path / "s.db"))
    household.sightings.record("Kitchen", "jaine", "robot", 0.8)
    moved = await household.link("jaine", "akadmin")
    assert "sightings" in moved and household.sightings.search()[0]["person"] == "akadmin"
