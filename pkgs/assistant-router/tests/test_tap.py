import json
import os
import stat

import pytest
from assistant_router.body import Bodies
from assistant_router.people import People
from assistant_router.people_store import PeopleStore
from assistant_router.server import make_body_app
from assistant_router.tap import (SESSION_COOKIE, Sessions, Taps, arp_lookup, display_name, make_tap_app,
                                  person_key, safe_next, session_secret)

BASE = "https://who.example.org"
ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.50     0x1         0x2         a2:3b:4c:5d:6e:7f     *        eth0
192.168.1.51     0x1         0x0         00:00:00:00:00:00     *        eth0
"""
ME = {"X-Authentik-Username": "kiril", "X-Authentik-Name": "Kiril"}
PHONE = {"X-Forwarded-For": "192.168.1.50"}


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


@pytest.fixture
def stored(setup):
    """With the runtime people store, as the router runs, and a wall clock for the cookies."""
    taps, people, clock, tmp_path = setup
    store = PeopleStore(people, str(tmp_path / "people.json"))
    taps._store, taps._runtime, taps._remember = store, store.has, store.add
    wall = Clock(1_800_000_000.0)
    taps.sessions = Sessions(b"k" * 32, clock=wall)
    return taps, people, clock, store, wall


def nonce(link):
    return link.split("?n=")[1]


def cookie_of(resp):
    morsel = resp.cookies.get(SESSION_COOKIE)
    return morsel.value if morsel is not None else None


def with_cookie(cookie, **headers):
    return {**headers, "Cookie": f"{SESSION_COOKIE}={cookie}"}


async def sign_in(c, user="kiril", name="Kiril", cookie=None, next_="/"):
    headers = {"X-Authentik-Username": user, "X-Authentik-Name": name}
    if cookie:
        headers["Cookie"] = f"{SESSION_COOKIE}={cookie}"
    r = await c.get(f"/signin?next={next_}", headers=headers, allow_redirects=False)
    assert r.status == 302
    return cookie_of(r)


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
    me = with_cookie(await sign_in(c), **PHONE)
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers=me)
    text = await r.text()
    assert r.status == 200 and "Hi Kiril!" in text and "in the Kitchen" in text and "Forget my phone" in text
    assert "learns" not in text  # no voice-id or Frigate to learn with
    assert "Change my name" not in text  # an account's name is its login's
    taps.learns = ("voice", "face")
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers=me)
    assert "For two minutes, Nabu learns your voice and your face there" in await r.text()
    r = await c.get("/tap/kitchen?n=nope", headers=me)
    assert "expired" in await r.text()

    r = await c.post("/forget-phone", headers={**me, "Origin": "https://evil.example"})
    assert r.status == 403 and taps.phones
    r = await c.post("/forget-phone", headers={**me, "Origin": BASE})
    assert r.status == 200 and taps.phones == {}
    r = await c.post("/forget-me", headers={**me, "Referer": f"{BASE}/"})
    assert r.status == 200 and forgotten == ["kiril"] and "home's settings" in await r.text()


async def test_without_a_cookie_the_page_asks_and_headers_name_nobody(aiohttp_client, setup):
    taps, people, clock, _ = setup
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    # The provider's headers reach only /signin: on the open paths a client could send them.
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers={**ME, **PHONE})
    text = await r.text()
    assert r.status == 200 and "tell me your name" in text and "Sign in with your account" in text
    assert "Hi Kiril!" not in text and people.assess("Kitchen", clock()).speaker is None and taps.phones == {}
    assert "/signin?next=%2Ftap%2Fkitchen%3Fn%3D" in text  # back to this tap after signing in
    r = await c.get("/", headers=ME)
    assert "Sign in with your account" in await r.text()
    assert (await c.post("/forget-me", headers={**ME, "Origin": BASE})).status == 403
    assert "expired" in await (await c.get("/tap/kitchen?n=nope")).text()


async def test_a_guest_says_their_name_and_is_remembered(aiohttp_client, stored):
    taps, people, clock, store, _ = stored
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    form = {"name": "Jane <i>", "room": "kitchen", "n": nonce(taps.link("Kitchen"))}
    r = await c.post("/name", data=form, headers={"Origin": BASE, **PHONE})
    text = await r.text()
    assert r.status == 200 and "Hi Jane i!" in text and "in the Kitchen" in text and "Change my name" in text
    guest = cookie_of(r)
    assert people.names["guest_jane_i"] == "Jane i" and store.kind("guest_jane_i") == "guest"
    assert taps.phones == {"a2:3b:4c:5d:6e:7f": "guest_jane_i"}
    assert people.assess("Kitchen", clock()).speaker == "guest_jane_i"
    # Their next tap knows them; they may change their name.
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers=with_cookie(guest, **PHONE))
    assert "Hi Jane i!" in await r.text()
    r = await c.post("/name", data={"name": "Janie"}, headers=with_cookie(guest, Origin=BASE))
    assert r.status == 200 and people.names["guest_jane_i"] == "Janie"
    assert (await c.post("/name", data={"name": "<>"}, headers=with_cookie(guest, Origin=BASE))).status == 400
    assert "Hi Janie!" in await (await c.get("/", headers=with_cookie(guest))).text()


async def test_a_guest_needs_a_fresh_link_and_a_form_from_here(aiohttp_client, stored):
    taps, people, clock, _, _ = stored
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    stale = {"name": "Jane", "room": "kitchen", "n": nonce(taps.link("Kitchen"))}
    clock.t += 180
    r = await c.post("/name", data=stale, headers={"Origin": BASE})
    assert r.status == 400 and "expired" in await r.text()
    fresh = {**stale, "n": nonce(taps.link("Kitchen"))}
    assert (await c.post("/name", data=fresh, headers={"Origin": "https://evil.example"})).status == 403
    assert (await c.post("/name", data={**fresh, "room": "garage"}, headers={"Origin": BASE})).status == 400
    assert not [k for k in people.names if k.startswith("guest_")]


def test_a_guest_never_takes_an_accounts_key(stored):
    taps, people, _, store, _ = stored
    people.add("maria", "Maria")
    assert taps.new_guest("Maria") == "guest_maria"
    assert taps.new_guest("Maria") == "guest_maria_2"
    store.link("guest_x", "kiril")  # a login named like a guest key
    assert taps.new_guest("X") == "guest_x_2"
    assert taps.new_guest("!!") is None


def test_without_the_people_store_nobody_is_added_as_a_guest(setup):
    taps, people, _, _ = setup
    assert taps.new_guest("Jane") is None and not [k for k in people.names if k.startswith("guest_")]


async def test_a_guest_who_signs_in_becomes_that_account(aiohttp_client, stored):
    taps, people, _, store, _ = stored
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    form = {"name": "Jane", "room": "kitchen", "n": nonce(taps.link("Kitchen"))}
    guest = cookie_of(await c.post("/name", data=form, headers={"Origin": BASE, **PHONE}))
    account = await sign_in(c, "jdoe", "Jane Doe", cookie=guest)
    assert store.resolve("jdoe") == "guest_jane" and store.kind("guest_jane") == "account"
    assert people.names["guest_jane"] == "Jane Doe"  # the account's name, from now on
    r = await c.get(f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}", headers=with_cookie(account, **PHONE))
    text = await r.text()
    assert "Hi Jane Doe!" in text and "Change my name" not in text and "jdoe" not in people.names
    # The old guest cookie is nobody now: the guest is an account.
    r = await c.post("/name", data={"name": "Someone"}, headers=with_cookie(guest, Origin=BASE))
    assert r.status == 400 and people.names["guest_jane"] == "Jane Doe"
    # An account's name isn't changed here.
    r = await c.post("/name", data={"name": "Someone"}, headers=with_cookie(account, Origin=BASE))
    assert r.status == 403 and "comes from your account" in await r.text()


async def test_signing_in_as_someone_known_keeps_them_and_the_guest_apart(aiohttp_client, stored):
    taps, people, _, store, _ = stored
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    form = {"name": "Kid", "room": "kitchen", "n": nonce(taps.link("Kitchen"))}
    guest = cookie_of(await c.post("/name", data=form, headers={"Origin": BASE}))
    await sign_in(c, "kiril", "Kiril", cookie=guest)  # a parent signing in on the child's phone
    assert store.resolve("kiril") is None and store.kind("guest_kid") == "guest"


async def test_an_account_is_checked_with_the_provider_again_after_a_day(aiohttp_client, stored):
    taps, people, _, store, wall = stored
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    account = await sign_in(c)
    link = f"/tap/kitchen?n={nonce(taps.link('Kitchen'))}"
    wall.t += 86400 + 1
    r = await c.get(link, headers=with_cookie(account), allow_redirects=False)
    assert r.status == 302 and r.headers["Location"].startswith("/signin?next=%2Ftap%2Fkitchen%3Fn%3D")


async def test_a_forged_or_old_cookie_is_nobody(aiohttp_client, stored):
    taps, _, _, _, wall = stored
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    account = await sign_in(c)
    body, _, sig = account.rpartition(".")
    forged = Sessions(b"x" * 32, clock=wall).make("account", "kiril", login="kiril")
    for cookie in (f"{body}.{sig[:-2]}AA", forged, "garbage", f"{body}x.{sig}"):
        assert "Sign in with your account" in await (await c.get("/", headers=with_cookie(cookie))).text()
    wall.t += 366 * 86400
    assert taps.sessions.read(account) is None


def test_signin_goes_back_only_to_this_page():
    assert safe_next("/tap/kitchen?n=0123abcd") == "/tap/kitchen?n=0123abcd"
    assert safe_next("/tap/bedroom%201?n=ab") == "/tap/bedroom%201?n=ab"
    assert safe_next("/") == "/"
    for bad in ("https://evil.example", "//evil.example", "/tap/../x", "/tap/k?n=1&x=2", "", "/admin"):
        assert safe_next(bad) == "/"


def test_the_cookies_key_is_kept_for_the_router_alone(tmp_path):
    path = str(tmp_path / "who-session.key")
    first = session_secret(path)
    assert len(first) == 32 and session_secret(path) == first
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert session_secret(None) != session_secret(None)


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


async def test_signing_in_picks_up_the_new_name(aiohttp_client, setup):
    taps, people, _, _ = setup
    taps._runtime = {"akadmin"}.__contains__
    people.add("akadmin", "authentik Default Admin")
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    account = await sign_in(c, "akadmin", "Kiril")
    assert people.names["akadmin"] == "Kiril"
    assert "Hi Kiril!" in await (await c.get("/", headers=with_cookie(account))).text()


def test_a_first_tap_links_the_person_nabu_met_by_voice(setup, tmp_path):
    from assistant_router.people import Assessment
    taps, people, clock, _ = setup
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("jane", "Jane")
    taps._store, taps._runtime, taps._remember = store, store.has, store.add
    people.remember("Kitchen", Assessment(speaker="jane", posterior=0.9, how="voice"), clock())
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "jdoe", "Jane Doe", "", ["Kitchen"])
    assert got["key"] == "jane" and store.resolve("jdoe") == "jane" and store.kind("jane") == "account"
    assert people.names["jane"] == "Jane Doe" and "jdoe" not in people.names  # one person, Authentik's name
    clock.t += 600  # later, nobody speaking: the login still names her
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "jdoe", "Jane Doe", "", ["Kitchen"])
    assert got["key"] == "jane"


def test_a_login_with_nobody_recognised_is_a_new_person(setup, tmp_path):
    taps, people, _, _ = setup
    store = PeopleStore(people, str(tmp_path / "people.json"))
    taps._store, taps._runtime, taps._remember = store, store.has, store.add
    got = taps.tap("kitchen", nonce(taps.link("Kitchen")), "sam", "Sam", "", ["Kitchen"])
    assert got["key"] == "sam" and people.names["sam"] == "Sam" and store.resolve("sam") is None
    assert store.kind("sam") == "account"


async def test_forgetting_a_linked_login_forgets_the_person_it_is(aiohttp_client, stored):
    taps, people, _, store, _ = stored
    store.add("jane", "Jane")
    store.link("jdoe", "jane")
    forgotten = []

    async def forget(key):
        forgotten.append(key)
        return True

    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], forget))
    account = await sign_in(c, "jdoe", "Jane Doe")
    assert (await c.post("/forget-me", headers=with_cookie(account, Origin=BASE))).status == 200
    assert forgotten == ["jane"]


class FakeHass:
    def __init__(self, down=False):
        self.down = down

    async def states(self, domain=None):
        from assistant_router.hass import HassError
        if self.down:
            raise HassError("GET /api/states")
        return [{"entity_id": "calendar.family", "name": "Family", "state": "off"},
                {"entity_id": "calendar.kiril", "name": "Kiril", "state": "off"},
                {"entity_id": "todo.kiril_reminders", "name": "kiril_reminders", "state": "0"},
                {"entity_id": "todo.kiril_todo", "name": "kiril_todo", "state": "1"},
                {"entity_id": "todo.maria_todo", "name": "maria_todo", "state": "1"},
                {"entity_id": "todo.shopping_list", "name": "Shopping list", "state": "3"}]


def form_token(text):
    return text.split('name="token" value="')[1].split('"')[0]


async def test_my_settings(aiohttp_client, stored, tmp_path):
    from assistant_router.prefs import Prefs
    taps, people, _, _, _ = stored
    people.add("maria", "Maria")
    prefs = Prefs(str(tmp_path / "prefs.json"))
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], prefs=prefs, hass=FakeHass()))
    account = await sign_in(c)
    me = with_cookie(account)
    assert "/settings" in await (await c.get("/", headers=me)).text()
    r = await c.get("/settings", headers=me)
    text = await r.text()
    assert r.status == 200 and "Family" in text and "Shopping list" in text
    assert "kiril_reminders" not in text  # a reminders list is never the to-do list
    assert "maria_todo" not in text  # nor another person's own list
    form = {"token": form_token(text), "calendar": "calendar.kiril", "reminder_calendar": "calendar.kiril",
            "todo": "todo.shopping_list", "immich_person": "Kiril"}
    r = await c.post("/settings", data=form, headers=with_cookie(account, Origin=BASE))
    assert r.status == 200
    assert prefs.get("kiril") == {"calendars": ["calendar.kiril"], "reminder_calendar": "calendar.kiril",
                                  "todo": "todo.shopping_list", "immich_person": "Kiril"}
    # Only what Home Assistant has, and only from this page with this cookie's token.
    form2 = {**form, "calendar": "calendar.nope", "todo": "todo.kiril_reminders"}
    await c.post("/settings", data=form2, headers=with_cookie(account, Origin=BASE))
    assert prefs.get("kiril") == {"reminder_calendar": "calendar.kiril", "immich_person": "Kiril"}
    assert (await c.post("/settings", data=form, headers=with_cookie(account, Origin="https://evil.example"))).status == 403
    assert (await c.post("/settings", data={**form, "token": "x"}, headers=with_cookie(account, Origin=BASE))).status == 403
    assert (await c.post("/settings", data=form, headers={"Origin": BASE})).status == 403
    assert (await c.get("/settings")).status == 403


async def test_my_settings_need_home_assistant(aiohttp_client, stored, tmp_path):
    from assistant_router.prefs import Prefs
    taps, _, _, _, _ = stored
    prefs = Prefs(str(tmp_path / "prefs.json"))
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], prefs=prefs, hass=FakeHass(down=True)))
    account = await sign_in(c)
    assert (await c.get("/settings", headers=with_cookie(account))).status == 503
    c2 = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"]))
    assert "/settings" not in await (await c2.get("/", headers=with_cookie(account))).text()


async def test_a_guest_has_no_settings_to_show_other_peoples_things(aiohttp_client, stored, tmp_path):
    from assistant_router.prefs import Prefs
    taps, _, _, _, _ = stored
    prefs = Prefs(str(tmp_path / "prefs.json"))
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], prefs=prefs, hass=FakeHass()))
    form = {"room": "Kitchen", "n": nonce(taps.link("Kitchen")), "name": "Jo"}
    guest = cookie_of(await c.post("/name", data=form, headers={"Origin": BASE, **PHONE}))
    assert guest
    assert "/settings" not in await (await c.get("/", headers=with_cookie(guest))).text()
    assert (await c.get("/settings", headers=with_cookie(guest))).status == 403
    post = {"token": taps.sessions.form_token(guest), "todo": "todo.kiril_todo"}
    assert (await c.post("/settings", data=post, headers=with_cookie(guest, Origin=BASE))).status == 403
    assert prefs.prefs == {}


class ListsHass:
    """Home Assistant's to-do lists, as far as the page uses them."""

    def __init__(self):
        self.items = {"todo.kiril_todo": [{"uid": "u1", "summary": "Milk <b>", "status": "needs_action"}],
                      "todo.maria_todo": [{"uid": "m1", "summary": "Secret", "status": "needs_action"}]}
        self.calls = []

    async def states(self, domain=None):
        return [{"entity_id": e, "name": e, "state": "1"} for e in self.items]

    async def call(self, domain, service, data, response=False):
        self.calls.append((service, data))
        rows = self.items[data["entity_id"]]
        if response:
            return {data["entity_id"]: {"items": [r for r in rows if r["status"] == "needs_action"]}}
        if service == "add_item":
            rows.append({"uid": f"n{len(rows)}", "summary": data["item"], "status": "needs_action"})
        elif service == "update_item":
            for r in rows:
                if r["uid"] == data["item"]:
                    r["status"] = data["status"]
        elif service == "remove_item":
            rows[:] = [r for r in rows if r["uid"] != data["item"]]
        return {}


async def lists_client(aiohttp_client, stored, tmp_path):
    from assistant_router.lists import Lists, Personal
    taps, people, _, _, _ = stored
    people.add("maria", "Maria")
    ha = ListsHass()
    told = []
    lists = Lists(str(tmp_path / "lists.json"))
    personal = Personal(ha, lists, people.names, told.append)
    c = await aiohttp_client(make_tap_app(taps, lambda: ["Kitchen"], personal=personal))
    return c, ha, lists, told, taps


async def test_my_lists(aiohttp_client, stored, tmp_path):
    c, ha, lists, told, taps = await lists_client(aiohttp_client, stored, tmp_path)
    account = await sign_in(c)
    me = with_cookie(account)
    assert "/lists" in await (await c.get("/", headers=me)).text()
    text = await (await c.get("/lists", headers=me)).text()
    assert "Milk &lt;b&gt;" in text and "Secret" not in text  # escaped, and only their own
    token = form_token(text)
    post = lambda action, **f: c.post(f"/lists/{action}", data={"token": token, "list": "todo", **f},  # noqa: E731
                                      headers=with_cookie(account, Origin=BASE))
    assert "Bread" in await (await post("add", item="Bread")).text()
    assert ("add_item", {"entity_id": "todo.kiril_todo", "item": "Bread"}) in ha.calls
    await post("done", item="u1")
    assert ha.items["todo.kiril_todo"][0]["status"] == "completed"
    await post("remove", item="n1")
    assert [r["summary"] for r in ha.items["todo.kiril_todo"]] == ["Milk <b>"]
    assert told == ["kiril", "kiril", "kiril"]
    # An item that isn't on their open list, or a list that isn't theirs, changes nothing.
    n = len(ha.calls)
    await post("remove", item="m1")
    await post("add", item="x", **{"list": "maria"})
    assert not [c_ for c_ in ha.calls[n:] if c_[0] in ("remove_item", "add_item")]
    assert "maria" not in "".join(str(c_) for c_ in ha.calls[n:] if c_[0] != "get_items")
    # A new list is asked for, not made here.
    assert "ready in a minute" in await (await post("new", name="Hardware")).text()
    assert lists.slugs("kiril") == ["todo", "hardware"]
    assert "Being set up" in await (await c.get("/lists", headers=me)).text()


async def test_my_lists_are_for_accounts_from_this_page(aiohttp_client, stored, tmp_path):
    c, ha, _, _, taps = await lists_client(aiohttp_client, stored, tmp_path)
    account = await sign_in(c)
    token = form_token(await (await c.get("/lists", headers=with_cookie(account))).text())
    data = {"token": token, "list": "todo", "item": "x"}
    assert (await c.post("/lists/add", data=data, headers=with_cookie(account, Origin="https://evil.example"))).status == 403
    assert (await c.post("/lists/add", data={**data, "token": "x"}, headers=with_cookie(account, Origin=BASE))).status == 403
    assert (await c.get("/lists")).status == 403
    form = {"room": "Kitchen", "n": nonce(taps.link("Kitchen")), "name": "Jo"}
    guest = cookie_of(await c.post("/name", data=form, headers={"Origin": BASE, **PHONE}))
    assert (await c.get("/lists", headers=with_cookie(guest))).status == 403
    assert "/lists" not in await (await c.get("/", headers=with_cookie(guest))).text()
    assert not [c_ for c_ in ha.calls if c_[0] == "add_item"]
