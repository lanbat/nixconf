"""Duplicates: merging anyone, finding likely pairs, single face pictures, one sighting moved."""
import random

import pytest
from assistant_router.frigate import FrigateError, safe_name
from assistant_router.household import Household
from assistant_router.people import People
from assistant_router.people_store import PeopleStore
from assistant_router.sightings import Sightings
from assistant_router.tap import Sessions, Taps, make_tap_app
from assistant_router.unknowns import Strangers

BASE = "https://who.example.org"
ADMIN = {"X-Authentik-Username": "akadmin", "X-Authentik-Groups": "family|authentik Admins", "Origin": BASE}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Faces:
    """A face library: pictures by person, who a picture is recognised as, and the calls made."""

    def __init__(self):
        self.faces = {}
        self.recognised = {}  # image bytes -> (name, score)
        self.thumbnails = {}
        self.deleted = []
        self.registered = []

    async def list(self):
        return {k: list(v) for k, v in self.faces.items()}

    async def enroll(self, name, image, filename="capture.jpg"):
        self.faces.setdefault(name, []).append(f"{name}_{len(self.faces.get(name, []))}.webp")
        return {"success": True}

    async def register(self, name, image, filename="capture.jpg"):
        self.faces.setdefault(name, []).append(f"{name}_{len(self.faces.get(name, []))}.webp")
        self.registered.append((name, image))
        return {"success": True}

    async def delete(self, name, ids):
        self.deleted.append((name, list(ids)))
        self.faces[name] = [f for f in self.faces.get(name, []) if f not in ids]
        if not self.faces[name]:
            del self.faces[name]
        return {"success": True}

    async def image(self, name, file):
        if file not in self.faces.get(name, []):
            raise FrigateError("GET")
        return b"img:" + file.encode()

    async def thumbnail(self, event):
        if event not in self.thumbnails:
            raise FrigateError("thumbnail", status=404)
        return self.thumbnails[event]

    async def recognize(self, image, filename="probe.jpg"):
        name, score = self.recognised.get(image, (None, None))
        return {"success": name is not None, "name": name, "confidence": score}

    async def move(self, key, into):
        n = len(self.faces.get(key, []))
        if n:
            self.faces.setdefault(into, []).extend(self.faces.pop(key))
        return n


@pytest.fixture
def world(tmp_path):
    (tmp_path / "pictures").mkdir()
    clock = Clock()
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("guest_jane", "Jane")
    store.set_kind("guest_jane", "guest")
    faces = Faces()
    sightings = Sightings(str(tmp_path / "s.db"), str(tmp_path / "pictures"), clock=lambda: clock.t)
    strangers = Strangers(people, store, faces, sightings, clock=clock, rng=random.Random(3))
    home = Household(people, store, frigate=faces, sightings=sightings, strangers=strangers, clock=clock,
                     forget=lambda key: None)
    return home, people, store, faces, sightings, strangers, clock, tmp_path


async def unknown(world, room="Hall", at=None):
    home, people, store, faces, sightings, strangers, clock, _ = world
    key = await strangers.meet(room, b"first-" + room.encode())
    sightings.record(room, key, "robot", 0.5, jpeg=b"first-" + room.encode())  # the enrolled frame
    clock.t += 10
    return key


async def test_who_may_be_merged_into_whom(world):
    home, people, store, *_ = world
    a, b = await unknown(world, "Hall"), None
    world[6].t += 10
    b = await unknown(world, "Porch")
    assert a != b
    assert home.can_merge(a, "guest_jane") and home.can_merge(a, "kiril") and home.can_merge(a, b)
    assert not home.can_merge("guest_jane", a)  # a named person never becomes an Unknown
    assert not home.can_merge("kiril", "guest_jane") and not home.can_merge(a, a) and not home.can_merge(a, "nobody")


async def test_merging_an_unknown_into_a_guest_moves_what_it_learned(world):
    home, people, store, faces, sightings, strangers, clock, _ = world
    key = await unknown(world)
    assert strangers.recent("Hall", clock.t) == key
    assert await home.link(key, "guest_jane") == ["1 face picture", "sightings"]
    assert key not in people.names and not people.strangers
    assert faces.faces["guest_jane"] and [s["person"] for s in sightings.search()] == ["guest_jane"]
    assert strangers.recent("Hall", clock.t) is None  # the room's last stranger is gone
    # An Unknown into another Unknown: the other stays, and the room follows it.
    one = await unknown(world, "Hall")
    clock.t += 1000
    two = await unknown(world, "Porch")
    assert await home.link(one, two) is not None
    assert strangers._last["hall"][0] == two  # the room follows the one who stays
    assert two in people.strangers and one not in people.names
    # Not the other way round: an identified person is never absorbed by an Unknown.
    assert await home.link("guest_jane", two) is None


async def test_face_pictures_are_listed_and_deleted_one_at_a_time(world):
    home, people, store, faces, *_ = world
    faces.faces["guest_jane"] = ["j1.webp", "j2.webp"]
    faces.faces["train"] = ["t1.webp"]  # Frigate's own unclassified attempts: nobody
    assert await home.pictures("guest_jane") == ["j1.webp", "j2.webp"]
    assert not await home.remove_picture("guest_jane", "other.webp")  # not one of theirs
    assert not await home.remove_picture("kiril", "k.webp")  # the profile's person stays as it is
    assert await home.remove_picture("guest_jane", "j1.webp")
    assert faces.deleted == [("guest_jane", ["j1.webp"])] and await home.pictures("guest_jane") == ["j2.webp"]
    assert await home.pictures("nobody") == []


def test_train_is_not_a_person():
    with pytest.raises(FrigateError):
        safe_name("train")


async def test_one_sighting_can_be_someone_else(world):
    home, people, store, faces, sightings, strangers, clock, _ = world
    key = await unknown(world)
    first = sightings.search()[0]["id"]
    sightings.record("Hall", key, "robot", 0.5, jpeg=b"later")
    later = sightings.search()[0]["id"]
    assert await home.move_sighting(later, "guest_jane")
    assert [(s["id"], s["person"]) for s in sightings.search()] == [(later, "guest_jane"), (first, key)]
    assert await home.move_sighting(later, None) and sightings.get(later)["person"] is None
    # "This is them": the picture also goes into their face pictures.
    assert await home.move_sighting(later, "guest_jane", add_picture=True)
    assert faces.registered == [("guest_jane", b"later")]
    assert not await home.move_sighting(later, "nobody") and not await home.move_sighting(999, "guest_jane")
    assert await home.move_sighting(later, "kiril", add_picture=True)  # an account's face may be taught too
    assert faces.registered[-1] == ("kiril", b"later") and sightings.get(later)["person"] == "kiril"
    faces.thumbnails.clear()
    sightings.record("Porch", key, "camera", None, event="1792518459.451025-ab12cd")
    gone = sightings.search()[0]["id"]  # a camera sighting whose thumbnail Frigate no longer has
    assert not await home.move_sighting(gone, "guest_jane", add_picture=True) and sightings.get(gone)["person"] == key


async def test_duplicates_are_unknowns_whose_later_pictures_look_like_someone(world):
    home, people, store, faces, sightings, strangers, clock, _ = world
    key = await unknown(world)
    for n in (b"a", b"b", b"c", b"d"):
        sightings.record("Hall", key, "robot", 0.5, jpeg=n)
        clock.t += 1
    faces.recognised = {b"b": ("guest_jane", 0.82), b"c": ("guest_jane", 0.7), b"d": (key, 0.99),
                        b"a": ("kiril", 0.4)}
    pairs = await home.duplicates()
    assert [(p["key"], p["into"], p["score"]) for p in pairs] == [(key, "guest_jane", 0.82)]
    # The enrolled frame is not tried: it would only say it is itself. Below the cutoff is no pair.
    faces.recognised = {b"first-Hall": ("guest_jane", 0.99), b"b": ("kiril", 0.5)}
    assert await home.duplicates() == pairs  # kept for a while: reloading costs nothing
    assert await home.duplicates(fresh=True) == []


async def test_duplicates_only_pair_what_can_be_merged(world):
    home, people, store, faces, sightings, strangers, clock, _ = world
    key = await unknown(world)
    sightings.record("Hall", key, "robot", 0.5, jpeg=b"x")
    faces.recognised = {b"x": ("ghost", 0.9)}  # a name in the library nobody here has
    assert await home.duplicates(fresh=True) == []


# ── the admin pages ──────────────────────────────────────────────────────────

async def admin(aiohttp_client, world):
    home, people, store, faces, sightings, strangers, clock, tmp = world
    taps = Taps(BASE, people, clock, phones_path=str(tmp / "phones.json"), store=store, runtime=store.has,
                remember=store.add, sessions=Sessions(b"k" * 32), groups_header="X-Authentik-Groups",
                admins=("authentik Admins",))
    home.taps = taps
    return await aiohttp_client(make_tap_app(taps, lambda: ["Hall"], household=home))


async def test_the_admin_can_merge_an_unknown_into_a_guest(aiohttp_client, world):
    home, people, *_ = world
    key = await unknown(world)
    c = await admin(aiohttp_client, world)
    text = await (await c.get("/admin", headers=ADMIN)).text()
    assert "Merge into…" in text and "Jane (Guest)" in text and "Unknown" in text and "Find duplicates" in text
    r = await c.post("/admin/link", data={"key": key, "into": "guest_jane"}, headers=ADMIN)
    assert "Link them?" in await r.text() and key in people.names
    r = await c.post("/admin/link", data={"key": key, "into": "guest_jane", "confirm": "yes"}, headers=ADMIN)
    assert r.status == 200 and key not in people.names


async def test_the_admin_looks_at_and_deletes_a_picture(aiohttp_client, world):
    home, people, store, faces, *_ = world
    faces.faces["guest_jane"] = ["j1.webp"]
    c = await admin(aiohttp_client, world)
    assert "/admin/pictures/guest_jane" in await (await c.get("/admin", headers=ADMIN)).text()
    assert "j1.webp" in await (await c.get("/admin/pictures/guest_jane", headers=ADMIN)).text()
    r = await c.get("/admin/pictures/guest_jane/j1.webp", headers=ADMIN)
    assert r.status == 200 and await r.read() == b"img:j1.webp"
    assert (await c.get("/admin/pictures/guest_jane/other.webp", headers=ADMIN)).status == 404
    r = await c.post("/admin/pictures/delete", data={"key": "guest_jane", "file": "j1.webp"}, headers=ADMIN)
    assert "Delete this picture?" in await r.text() and faces.faces
    r = await c.post("/admin/pictures/delete", data={"key": "guest_jane", "file": "j1.webp", "confirm": "yes"},
                     headers=ADMIN)
    assert "last picture" in await r.text() and not faces.faces


async def test_the_admin_moves_a_sighting_and_finds_duplicates(aiohttp_client, world):
    home, people, store, faces, sightings, strangers, clock, _ = world
    key = await unknown(world)
    sightings.record("Hall", key, "robot", 0.5, jpeg=b"later")
    later = sightings.search()[0]["id"]
    faces.recognised = {b"later": ("guest_jane", 0.9)}
    c = await admin(aiohttp_client, world)
    assert f"/admin/sightings/{later}/move" in await (await c.get("/admin/sightings", headers=ADMIN)).text()
    text = await (await c.post("/admin/duplicates", headers=ADMIN)).text()
    assert "looks like Jane" in text and "0.90" in text and f"/admin/sightings/{later}/picture" in text
    r = await c.post(f"/admin/sightings/{later}/move", data={"person": "guest_jane"}, headers=ADMIN,
                     allow_redirects=False)
    assert r.status == 302 and sightings.get(later)["person"] == "guest_jane"


async def test_only_admins_from_this_site_may_change_these(aiohttp_client, world):
    home, people, store, faces, sightings, *_ = world
    key = await unknown(world)
    faces.faces["guest_jane"] = ["j1.webp"]
    c = await admin(aiohttp_client, world)
    plain = {"X-Authentik-Username": "sam", "X-Authentik-Groups": "family", "Origin": BASE}
    evil = {**ADMIN, "Origin": "https://evil.example"}
    for method, url, data in (("post", "/admin/pictures/delete", {"key": "guest_jane", "file": "j1.webp"}),
                              ("post", "/admin/duplicates", {}),
                              ("post", "/admin/sightings/1/move", {"person": "guest_jane"}),
                              ("get", "/admin/pictures/guest_jane", None), ("get", "/admin/duplicates", None),
                              ("get", "/admin/pictures/guest_jane/j1.webp", None)):
        for headers in (plain, {}, *((evil,) if method == "post" else ())):
            r = await getattr(c, method)(url, data=data, headers=headers) if data is not None \
                else await getattr(c, method)(url, headers=headers)
            assert r.status == 403, (url, headers)
    assert faces.faces == {"guest_jane": ["j1.webp"], key: faces.faces[key]}
