"""Tap your phone on a robot: who it is, there, and their phone's Wi-Fi address.

Each robot is an NFC tag holding a link to this page (firmware nfc.h), a new
one every minute: https://who.<domain>/tap/<room>?n=<nonce>. A phone held to
the robot opens it. The page is open to anyone, accounts or not (Caddy lets /,
/tap/*, /name, /forget-* and /settings through without a login); who the browser is comes
from the page's own signed cookie, never from a header a client could send:

- an account: /signin, behind the forward-auth provider's login (Authentik),
  reads who that is from its user header (after the check: a client's own is
  dropped; X-Authentik-Username), sets the cookie and goes back to the page.
  The username is their key (a profile's person when it matches one, else
  added at runtime, people_store, of kind "account"). After a day the cookie
  is checked with the provider again: a tap then passes through /signin,
  unseen while the provider's own login lasts, which also picks up a name
  changed there (a runtime person's name follows it; a profile's person keeps
  the profile's);
- a guest, with no account: they type their name on the page, and a fresh
  link proves they are at the robot. They become a runtime person of kind
  "guest" (key guest_<name>), the cookie remembers them for a year, and they
  may rename themselves. Signing in later on the same phone makes their
  account that person.

Either way, a tap with a fresh link means:

- they are in that room, for two minutes ("tap" in people.py, as strong as a
  card): a hint for who is speaking, never authority over anything;
- the phone's Wi-Fi address, from the server's ARP table for the address it
  came from, is theirs: wifi-presence (a house sensor on the body socket) is
  told, and counts them home whenever that phone is on the network.

A link older than two minutes, or one for a room without a robot, only shows
the page. The page offers to forget the phone, or the person (one added here,
not one the profile declares). Learned phones are kept in a small JSON file
in the router's state directory: {mac: key}; the cookies' key next to them.

With Home Assistant (hass.py) and a settings store (prefs.py), "My settings"
(/settings) lets each person with an account (never a guest: they could pick
someone else's lists and calendars to show) choose their screen's calendars, to-do list and
photos, and the calendar their reminders go in, from what Home Assistant has.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from typing import Any, Awaitable, Callable

from aiohttp import web

from .hass import HassError
from .people import People

LOG = logging.getLogger("assistant_router")

LINK_PERIOD_S = 60.0     # a new link every minute
LINK_WITHIN_S = 120.0    # a first tap links the room's speaker of the last two minutes
MAX_PHONES = 48          # wifi-presence's kernel filter holds 60, with the profile's
PHONES_PER_PERSON = 4
SESSION_COOKIE = "nabu_who"
SESSION_MAX_AGE_S = 365 * 86400.0  # a phone stays someone this long
ACCOUNT_RECHECK_S = 86400.0        # an account's cookie goes past the provider's login again after this
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_NEXT = re.compile(r"/(tap/[A-Za-z0-9%._-]{1,96}(\?n=[0-9a-f]{0,32})?)?")


def _key(room: str) -> str:
    return (room or "").strip().casefold()


def person_key(username: str) -> str | None:
    """A person key from an Authentik username: lowercase letters, digits and
    underscores (a face-library name, as server._person_key)."""
    slug = re.sub(r"[^a-z0-9]+", "_", (username or "").lower()).strip("_")[:40]
    return slug or None


def display_name(name: str, fallback: str) -> str:
    """A name fit for the prompt's people line: letters, spaces, a few marks."""
    clean = " ".join(re.sub(r"[^\w .'-]", "", name or "", flags=re.UNICODE).split())[:40]
    return clean or fallback


def arp_lookup(ip: str, path: str = "/proc/net/arp") -> str | None:
    """The MAC address the kernel has for `ip` on the LAN, or None."""
    try:
        with open(path, encoding="ascii") as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        cols = line.split()
        if len(cols) >= 4 and cols[0] == ip and int(cols[2], 16) & 0x2:
            mac = cols[3].lower()
            if _MAC.match(mac) and mac != "00:00:00:00:00:00":
                return mac
    return None


def _load(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(m): str(k) for m, k in data.items() if _MAC.match(str(m))}


def session_secret(path: str | None) -> bytes:
    """The cookies' key: kept in the router's state so sessions outlive a
    restart, made on first use, readable by the router only. Without a path,
    one for this run."""
    if not path:
        return secrets.token_bytes(32)
    try:
        with open(path, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key[:32]
    except OSError:
        pass
    key = secrets.token_bytes(32)
    fd = os.open(f"{path}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.replace(f"{path}.tmp", path)
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Sessions:
    """Who a browser is, in a cookie the router signs (HMAC-SHA256): an account
    (its login and name, as /signin saw them) or a guest (their key)."""

    def __init__(self, secret: bytes, clock: Callable[[], float] = time.time) -> None:
        self._secret = secret
        self.clock = clock

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())

    def make(self, kind: str, key: str, login: str = "", name: str = "") -> str:
        body = _b64(json.dumps({"kind": kind, "key": key, "login": login, "name": name,
                                "at": int(self.clock())}, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def read(self, value: str | None) -> dict | None:
        """The session in `value`, or None: unsigned, altered, or too old."""
        body, _, sig = (value or "").rpartition(".")
        if not body or not hmac.compare_digest(sig, self._sign(body)):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, binascii.Error):
            return None
        if not isinstance(data, dict) or data.get("kind") not in ("account", "guest") \
                or not isinstance(data.get("key"), str) or type(data.get("at")) is not int:
            return None
        if self.clock() - data["at"] > SESSION_MAX_AGE_S:
            return None
        return data

    def fresh(self, session: dict) -> bool:
        """Whether the cookie may name them without asking the provider again."""
        return session["kind"] != "account" or self.clock() - session["at"] <= ACCOUNT_RECHECK_S

    def form_token(self, cookie: str | None) -> str:
        """A form's hidden token, for this cookie alone: another site can't read it."""
        return self._sign("form:" + (cookie or ""))


class Taps:
    """The links, and the phones learned from taps."""

    def __init__(self, base_url: str, people: People, clock: Callable[[], float],
                 phones_path: str | None = None, remember: Callable[[str, str], None] | None = None,
                 runtime: Callable[[str], bool] | None = None, store=None,
                 arp_path: str = "/proc/net/arp", user_header: str = "X-Authentik-Username",
                 name_header: str | None = "X-Authentik-Name", learns: tuple[str, ...] = (),
                 sessions: Sessions | None = None, account_url: str | None = None,
                 groups_header: str | None = None, admins: tuple[str, ...] = ()) -> None:
        self.base_url = base_url.rstrip("/")
        self.people = people
        self.clock = clock
        self.phones_path = phones_path
        self.phones = _load(phones_path)
        self._remember = remember or people.add
        self._runtime = runtime or (lambda key: False)  # added here, not by the profile
        self._store = store  # people_store.PeopleStore: logins linked to people (aliases), and kinds
        self.arp_path = arp_path
        self.user_header = user_header
        self.name_header = name_header
        self.learns = learns  # what a tap teaches the router meanwhile: "voice", "face" (the page says so)
        self.sessions = sessions or Sessions(session_secret(None))
        self.account_url = account_url  # where an account's name is changed (the provider's settings)
        self.groups_header = groups_header  # the provider's groups, after its check ("a|b")
        self.admins = tuple(admins)  # its groups whose members see /admin
        self._secret = secrets.token_bytes(16)  # links die with a restart; robots get new ones
        self.listeners: list[Callable[[dict[str, str]], Any]] = []
        self.forgetters: list[Callable[[str], Any]] = []
        self.strangers = None  # unknowns.Strangers: an Unknown seen in the room is who signs in there

    def _nonce(self, room: str, period: int) -> str:
        msg = f"{_key(room)}:{period}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()[:16]

    def link(self, room: str, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        return (f"{self.base_url}/tap/{urllib.parse.quote(_key(room), safe='')}"
                f"?n={self._nonce(room, int(now // LINK_PERIOD_S))}")

    def valid(self, room: str, nonce: str, now: float | None = None) -> bool:
        """This minute's link or the last one's."""
        now = self.clock() if now is None else now
        period = int(now // LINK_PERIOD_S)
        return any(hmac.compare_digest(nonce or "", self._nonce(room, p)) for p in (period, period - 1))

    def room(self, room: str, nonce: str, rooms: list[str]) -> str | None:
        """The room, as the robot names it, when `nonce` is its fresh link."""
        name = next((r for r in rooms if _key(r) == _key(room)), None)
        return name if name is not None and self.valid(room, nonce) else None

    def _save(self) -> None:
        if self.phones_path:
            tmp = f"{self.phones_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.phones, f, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.phones_path)
        for listener in self.listeners:
            listener(dict(self.phones))

    def learn(self, mac: str, key: str) -> bool:
        """`mac` is `key`'s phone; False when there is no room for it."""
        if self.phones.get(mac) == key:
            return True
        theirs = [m for m, k in self.phones.items() if k == key]
        if len(theirs) >= PHONES_PER_PERSON:
            self.phones.pop(theirs[0])  # the oldest
        if mac not in self.phones and len(self.phones) >= MAX_PHONES:
            return False
        self.phones[mac] = key
        self._save()
        return True

    def rekey_phones(self, key: str, into: str) -> int:
        """`key`'s phones are `into`'s (an admin linked the two)."""
        moved = [m for m, k in self.phones.items() if k == key]
        for m in moved:
            self.phones[m] = into
        if moved:
            self._save()
        return len(moved)

    def forget_phones(self, key: str) -> int:
        """`key`'s phones, by Wi-Fi address here and anywhere else told
        (forgetters: their Bluetooth keys)."""
        for forget in self.forgetters:
            forget(key)
        gone = [m for m, k in self.phones.items() if k == key]
        for m in gone:
            del self.phones[m]
        if gone:
            self._save()
        return len(gone)

    def refresh_name(self, key: str, name: str, username: str) -> None:
        """A runtime person's name from Authentik's, when it changed (the
        user renamed themselves there). Never a profile's person, never a
        guest, and never to nothing: without the name header the stored name
        stays."""
        if not name or key not in self.people.names or not self._runtime(key) or self.is_guest(key):
            return
        fresh = display_name(name, username)
        if fresh != self.people.names[key]:
            LOG.info(json.dumps({"renamed": key}))
            self._remember(key, fresh)

    def _kind(self, key: str) -> str | None:
        return self._store.kind(key) if self._store is not None else None

    def _set_kind(self, key: str, kind: str) -> None:
        if self._store is not None:
            self._store.set_kind(key, kind)

    def is_guest(self, key: str) -> bool:
        return key in self.people.names and self._kind(key) == "guest"

    def account_key(self, username: str) -> str | None:
        """The person a login is: the one it is linked to, else its own key."""
        login = person_key(username)
        return (self._store.resolve(login) if self._store is not None and login else None) or login

    def signed_in(self, username: str, name: str, guest: str | None = None) -> str | None:
        """/signin saw this login: their key. A guest signing in on the same
        phone becomes that account, when the login is nobody here yet."""
        login = person_key(username)
        if login is None:
            return None
        key = self.account_key(username)
        if key not in self.people.names and guest and self.is_guest(guest) \
                and self._store is not None and not self._store.linked(guest):
            self._store.link(login, guest)
            self._set_kind(guest, "account")
            LOG.info(json.dumps({"linked": guest, "was": "guest"}))
            key = guest
        if key in self.people.names:
            self.refresh_name(key, name, username)
        return key

    def visit(self, room: str, nonce: str, key: str, ip: str, rooms: list[str]) -> dict:
        """`key`'s phone opened a robot's link: they are there, and it is theirs."""
        out: dict = {"key": key, "name": self.people.names.get(key), "room": None, "phone": False,
                     "stale": False}
        room_name = self.room(room, nonce, rooms)
        if room_name is None:
            out["stale"] = True
            return out
        out["room"] = room_name
        self.people.observe(room_name, "tap", key, 1.0, self.clock())
        mac = arp_lookup(ip, self.arp_path) if ip else None
        if mac and self.learn(mac, key):
            out["phone"] = True
        LOG.info(json.dumps({"tap": room_name, "person": key, "phone": out["phone"]}))
        return out

    def tap(self, room: str, nonce: str, username: str, name: str, ip: str, rooms: list[str]) -> dict:
        """An account's phone opened a robot's link: what happened, for the page."""
        login = person_key(username)
        key = self.account_key(username)
        out: dict = {"key": key, "name": None, "room": None, "phone": False, "stale": False}
        if key is None:
            return out
        room_name = self.room(room, nonce, rooms)
        if room_name is None:
            out["stale"] = True
            out["name"] = self.people.names.get(key) or display_name(name, username)
            return out
        if key not in self.people.names and self._store is not None:
            # A first tap by someone Nabu met by voice: the login becomes them,
            # instead of a second person (identity.py, "Meeting Nabu").
            spoke, _, _ = self.people.last_speaker(room_name, self.clock(), within=LINK_WITHIN_S)
            if spoke and self._runtime(spoke) and not self._store.linked(spoke) and not self.is_guest(spoke):
                self._store.link(login, spoke)
                self._set_kind(spoke, "account")
                LOG.info(json.dumps({"linked": spoke}))
                key = spoke
            elif self.strangers is not None:
                # Nobody spoke, but an Unknown was just seen here: the login is them
                # (unknowns.py). They keep their key, and take the account's name.
                met = self.strangers.recent(room_name, self.clock())
                if met and not self._store.linked(met):
                    self._store.link(login, met)
                    self.strangers.identify(met, display_name(name, username), "account")
                    key = met
        if key not in self.people.names:
            self._remember(key, display_name(name, username))
            self._set_kind(key, "account")
        else:
            self.refresh_name(key, name, username)
        return self.visit(room_name, nonce, key, ip, rooms)

    def new_guest(self, name: str) -> str | None:
        """Someone without an account, by the name they typed: their new key,
        guest_<name> (never an account's), or None when no one can be added."""
        clean = display_name(name, "")
        slug = person_key(clean)
        if self._store is None or not clean or slug is None:
            return None
        base = f"guest_{slug}"[:40]
        key, n = base, 2
        while key in self.people.names or self._store.resolve(key) is not None:
            key, n = f"{base}_{n}", n + 1
        self._remember(key, clean)
        self._set_kind(key, "guest")
        LOG.info(json.dumps({"guest": key}))
        return key

    def rename_guest(self, key: str, name: str) -> bool:
        """A guest gave a new name; an account's name follows its login."""
        clean = display_name(name, "")
        if not clean or not self.is_guest(key):
            return False
        if clean != self.people.names[key]:
            LOG.info(json.dumps({"renamed": key}))
            self._remember(key, clean)
        return True


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nabu</title>
<style>
:root {{ color-scheme: light dark; --bg: #fff8f0; --fg: #2b2118; --muted: #7a6a5c; --accent: #e8743b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #1d1814; --fg: #f3e9df; --muted: #b3a394; }} }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 18px/1.5 system-ui, sans-serif; }}
main {{ max-width: 32rem; margin: 0 auto; padding: 2rem 1rem; }}
h1 {{ font-size: 1.6rem; margin: 0 0 .5rem; }}
p {{ margin: .5rem 0; }}
a {{ color: var(--accent); }}
.muted {{ color: var(--muted); font-size: .9rem; }}
section.person {{ border-top: 1px solid var(--muted); padding: .75rem 0; }}
section.person h2 {{ font-size: 1.15rem; margin: 0; }}
.tag {{ font-size: .75rem; font-weight: normal; padding: .1rem .5rem; border-radius: 1rem; margin-left: .4rem;
        vertical-align: middle; border: 1px solid var(--muted); }}
.tag-account {{ background: #2f7d5a; border-color: #2f7d5a; color: #fff; }}
.tag-guest {{ background: #3b6fb6; border-color: #3b6fb6; color: #fff; }}
.tag-heard, .tag-not-recorded {{ background: #b8892c; border-color: #b8892c; color: #fff; }}
select {{ font: inherit; padding: .4rem; border-radius: .5rem; background: transparent; color: var(--fg); }}
form {{ display: inline-block; margin: 1rem .5rem 0 0; }}
form.name, form.settings {{ display: block; }}
fieldset {{ border: 1px solid var(--muted); border-radius: .5rem; margin: 1rem 0; padding: .5rem .75rem; }}
legend {{ padding: 0 .25rem; }}
label {{ display: block; margin-bottom: .5rem; }}
label.check {{ display: flex; gap: .5rem; align-items: center; }}
label.check input {{ width: auto; }}
input {{ font: inherit; padding: .5rem; border-radius: .5rem; border: 1px solid var(--muted);
        background: transparent; color: var(--fg); width: 100%; box-sizing: border-box; }}
button {{ font: inherit; padding: .5rem 1rem; border-radius: .5rem; border: 1px solid var(--muted);
         background: transparent; color: var(--fg); }}
</style></head>
<body><main>
<h1>{title}</h1>
{body}
</main></body></html>
"""


def page(title: str, body: str, status: int = 200) -> web.Response:
    return web.Response(text=PAGE.format(title=html.escape(title), body=body), status=status,
                        content_type="text/html", headers={"Cache-Control": "no-store"})


def safe_next(target: str) -> str:
    """Where /signin goes back to: this page's own paths only."""
    return target if _NEXT.fullmatch(target or "") else "/"


def make_tap_app(taps: Taps, rooms: Callable[[], list[str]],
                 forget_person: Callable[[str], Awaitable[bool]] | None = None,
                 household=None, prefs=None, hass=None, personal=None) -> web.Application:
    """The page behind https://who.<domain>, on the loopback for Caddy; with
    `household` (household.Household), "People Nabu knows" for admins; with
    `prefs` (prefs.Prefs) and `hass` (hass.Hass), "My settings"; with `personal`
    (lists.Personal), "My lists"."""

    def ip_of(request: web.Request) -> str:
        return request.headers.get("X-Forwarded-For", "").split(",")[0].strip()

    def session(request: web.Request) -> dict | None:
        s = taps.sessions.read(request.cookies.get(SESSION_COOKIE))
        if s is not None and s["kind"] == "guest" and not taps.is_guest(s["key"]):
            return None  # forgotten, or an account since
        return s

    def key_of(s: dict) -> str | None:
        return s["key"] if s["kind"] == "guest" else taps.account_key(s.get("login", ""))

    def with_cookie(resp: web.Response, value: str) -> web.Response:
        resp.set_cookie(SESSION_COOKIE, value, max_age=int(SESSION_MAX_AGE_S), path="/", secure=True,
                        httponly=True, samesite="Lax")
        return resp

    def go(target: str) -> web.Response:
        return web.Response(status=302, headers={"Location": target, "Cache-Control": "no-store"})

    def signin_link(target: str, text: str = "Sign in with your account") -> str:
        return f'<a href="/signin?next={urllib.parse.quote(safe_next(target), safe="")}">{text}</a>'

    def account_note() -> str:
        where = (f' <a href="{html.escape(taps.account_url, quote=True)}">Change it there.</a>'
                 if taps.account_url else "")
        return f"<p>Your name comes from your account.{where}</p>"

    def name_form(value: str = "", room: str = "", nonce: str = "", label: str = "Your name",
                  button: str = "That's me") -> str:
        hidden = (f'<input type="hidden" name="room" value="{html.escape(room, quote=True)}">'
                  f'<input type="hidden" name="n" value="{html.escape(nonce, quote=True)}">' if room else "")
        return (f'<form class="name" method="post" action="/name">{hidden}'
                f'<label>{label} <input name="name" maxlength="40" required autocomplete="given-name" '
                f'value="{html.escape(value, quote=True)}"></label><button>{button}</button></form>')

    def buttons(key: str, guest: bool) -> str:
        out = []
        if guest:
            out.append(name_form(taps.people.names.get(key, ""), label="Not quite right?",
                                 button="Change my name"))
        if sum(1 for k in taps.phones.values() if k == key):
            out.append('<form method="post" action="/forget-phone"><button>Forget my phone</button></form>')
        if forget_person is not None:
            out.append('<form method="post" action="/forget-me"><button>Forget me</button></form>')
        return "".join(out)

    def tapped(got: dict, guest: bool) -> web.Response:
        hi = f"Hi {got['name']}!" if got["name"] else "Hi!"
        if got["stale"]:
            body = "<p>That link has expired. Hold your phone to Nabu again.</p>"
        else:
            body = f"<p>Nabu knows you're in the {html.escape(got['room'])}.</p>"
            if got["phone"]:
                body += "<p>Nabu will also know when this phone is home.</p>"
            if taps.learns:
                what = " and ".join(f"your {w}" for w in taps.learns)
                body += (f"<p>For two minutes, Nabu learns {what} there, so it knows you next time. "
                         "Forget me removes them.</p>")
        body += ('<p class="muted">A friendly guess for Nabu, never a key: it never decides or unlocks '
                 'anything because of it.</p>' + buttons(got["key"], guest))
        return page(hi, body)

    async def signin(request: web.Request) -> web.Response:
        # Behind the provider's login, the only place its headers are read.
        user = request.headers.get(taps.user_header, "")
        name = request.headers.get(taps.name_header, "") if taps.name_header else ""
        current = taps.sessions.read(request.cookies.get(SESSION_COOKIE))
        guest = current["key"] if current is not None and current["kind"] == "guest" else None
        key = taps.signed_in(user, name, guest)
        if key is None:
            return page("Nabu", "<p>Sign in first.</p>", 403)
        return with_cookie(go(safe_next(request.query.get("next", ""))),
                           taps.sessions.make("account", key, login=person_key(user) or "", name=name))

    async def tap(request: web.Request) -> web.Response:
        room, nonce = request.match_info["room"], request.query.get("n", "")
        s = session(request)
        if s is None:
            if taps.room(room, nonce, rooms()) is None:
                return page("Nabu", "<p>That link has expired. Hold your phone to Nabu again.</p>")
            here = f"/tap/{urllib.parse.quote(_key(room), safe='')}?n={nonce}"
            return page("Hi! I'm Nabu.", f"<p>{signin_link(here)}, or tell me your name:</p>"
                                         + name_form(room=room, nonce=nonce))
        if not taps.sessions.fresh(s):
            return go(f"/signin?next={urllib.parse.quote(safe_next(request.path_qs), safe='')}")
        if s["kind"] == "account":
            got = taps.tap(room, nonce, s.get("login", ""), s.get("name", ""), ip_of(request), rooms())
            if got["key"] is None:
                return page("Nabu", "<p>Nabu can't make a name from this login.</p>", 400)
            return tapped(got, guest=False)
        return tapped(taps.visit(room, nonce, s["key"], ip_of(request), rooms()), guest=True)

    def same_site(request: web.Request) -> bool:
        # A form on this page, not one on another site posting with the cookie.
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        return origin.rstrip("/") == taps.base_url or origin.startswith(taps.base_url + "/")

    async def name(request: web.Request) -> web.Response:
        if not same_site(request):
            return page("Nabu", "<p>Not from here.</p>", 403)
        form = await request.post()
        wanted = display_name(str(form.get("name", "")), "")
        if not wanted:
            return page("Nabu", "<p>Nabu didn't catch a name there. Try again.</p>", 400)
        s = session(request)
        if s is not None and s["kind"] == "account":
            return page("Nabu", account_note(), 403)
        if s is not None:
            taps.rename_guest(s["key"], wanted)
            return page(f"Hi {wanted}!", "<p>Nabu will call you that from now on.</p>" + buttons(s["key"], True))
        room, nonce = str(form.get("room", "")), str(form.get("n", ""))
        if taps.room(room, nonce, rooms()) is None:
            return page("Nabu", "<p>That link has expired. Hold your phone to Nabu again.</p>", 400)
        key = taps.new_guest(wanted)
        if key is None:
            return page("Nabu", f"<p>Nabu can't add anyone new here. {signin_link('/')}.</p>", 400)
        return with_cookie(tapped(taps.visit(room, nonce, key, ip_of(request), rooms()), guest=True),
                           taps.sessions.make("guest", key))

    async def index(request: web.Request) -> web.Response:
        s = session(request)
        if s is None:
            return page("Hi! I'm Nabu.", "<p>Hold your phone to Nabu's head to say hello.</p>"
                                         f"<p>{signin_link('/')}</p>")
        key = key_of(s)
        shown = taps.people.names.get(key or "") or display_name(s.get("name", ""), "")
        body = "<p>Hold your phone to Nabu's head to say hello.</p>"
        if key and s["kind"] == "account" and prefs is not None and hass is not None:
            body += '<p><a href="/settings">My settings</a> <span class="muted">(your screen)</span></p>'
        if key and s["kind"] == "account" and personal is not None:
            body += '<p><a href="/lists">My lists</a> · <a href="/reminders">My reminders</a></p>'
        if s["kind"] == "account":
            body += account_note()
            if household is not None and taps.admins:
                body += '<p><a href="/admin">People Nabu knows</a> <span class="muted">(the home\'s admins)</span></p>'
        return page(f"Hi {shown}!" if shown else "Hi!", body + buttons(key or "", s["kind"] == "guest"))

    # ── My settings: anyone the page knows, for themselves ───────────────────

    async def choices(key: str) -> tuple[list[dict], list[dict]]:
        """(calendars, to-do lists) Home Assistant has, to choose from; a
        reminders list is shown by itself, never as the to-do list."""
        entities = await hass.states()
        cals = [e for e in entities if e["entity_id"].startswith("calendar.")]
        # Not a reminders list, and not another person's own lists (todo.<key>_...).
        others = tuple(f"todo.{k}_" for k in taps.people.names if k != key)
        todos = [e for e in entities if e["entity_id"].startswith("todo.")
                 and not e["entity_id"].endswith("_reminders") and not e["entity_id"].startswith(others)]
        return cals, todos

    def settings_form(request: web.Request, mine: dict, cals: list[dict], todos: list[dict]) -> str:
        esc = lambda v: html.escape(v, quote=True)  # noqa: E731
        token = taps.sessions.form_token(request.cookies.get(SESSION_COOKIE))
        chosen = set(mine.get("calendars", []))
        boxes = "".join(
            f'<label class="check"><input type="checkbox" name="calendar" value="{esc(c["entity_id"])}"'
            f'{" checked" if c["entity_id"] in chosen else ""}> {html.escape(c["name"])}</label>'
            for c in cals) or '<p class="muted">Home Assistant has no calendars yet.</p>'

        def select(name: str, options: list[dict], current: str, none: str) -> str:
            opts = [f'<option value="">{html.escape(none)}</option>'] + [
                f'<option value="{esc(o["entity_id"])}"{" selected" if o["entity_id"] == current else ""}>'
                f'{html.escape(o["name"])}</option>' for o in options]
            return f'<select name="{name}">{"".join(opts)}</select>'

        return (f'<form class="settings" method="post" action="/settings">'
                f'<input type="hidden" name="token" value="{esc(token)}">'
                f'<fieldset><legend>Next up on your screen</legend>{boxes}</fieldset>'
                f'<fieldset><legend>Your reminders also go in</legend>'
                f'{select("reminder_calendar", cals, mine.get("reminder_calendar", ""), "No calendar")}</fieldset>'
                f'<fieldset><legend>The to-do list your screen shows</legend>'
                f'{select("todo", todos, mine.get("todo", ""), "Your own to-do list")}</fieldset>'
                f'<fieldset><legend>Photos</legend><label>Whose, as Immich names them '
                f'<input name="immich_person" maxlength="60" value="{esc(mine.get("immich_person", ""))}">'
                f'</label></fieldset>'
                f'<button>Save</button></form>'
                '<p class="muted">Your screen uses these while you are alone in front of it. '
                'Anyone with a Home Assistant login can see the calendars and lists there.</p>'
                '<p><a href="/">Back</a></p>')

    async def settings(request: web.Request) -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None and s["kind"] == "account" else None
        if key is None or prefs is None or hass is None:
            return page("Nabu", "<p>My settings are for people who sign in with an account.</p>", 403)
        try:
            cals, todos = await choices(key)
        except HassError:
            return page("My settings", "<p>Home Assistant isn't answering right now. Try again later.</p>", 503)
        return page("My settings", settings_form(request, prefs.get(key), cals, todos))

    async def save_settings(request: web.Request) -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None and s["kind"] == "account" else None
        if key is None or prefs is None or hass is None or not same_site(request):
            return page("Nabu", "<p>Not from here.</p>", 403)
        form = await request.post()
        want = taps.sessions.form_token(request.cookies.get(SESSION_COOKIE))
        if not hmac.compare_digest(str(form.get("token", "")), want):
            return page("Nabu", "<p>Not from here.</p>", 403)
        try:
            cals, todos = await choices(key)
        except HassError:
            return page("My settings", "<p>Home Assistant isn't answering right now, so nothing was "
                                       "saved. Try again later.</p>", 503)
        known_cals = {c["entity_id"] for c in cals}
        known_todos = {t["entity_id"] for t in todos}
        chosen = {
            "calendars": [c for c in form.getall("calendar", []) if c in known_cals],
            "reminder_calendar": next((c for c in [form.get("reminder_calendar", "")] if c in known_cals), ""),
            "todo": next((t for t in [form.get("todo", "")] if t in known_todos), ""),
            "immich_person": str(form.get("immich_person", "")),
        }
        mine = prefs.set(key, chosen)
        LOG.info(json.dumps({"settings": key, "chose": sorted(mine)}))
        return page("Saved", "<p>Your screen uses them the next time it sees you alone.</p>"
                             + settings_form(request, mine, cals, todos))

    # ── My lists: accounts, their own lists only ──────────────────────────────

    def lists_page(request: web.Request, key: str, shown: list[tuple[str, list[dict] | None]],
                   note: str = "") -> web.Response:
        esc = lambda v: html.escape(v, quote=True)  # noqa: E731
        token = form_token(request)

        def form(action: str, which: str, fields: str, button: str) -> str:
            return (f'<form method="post" action="/lists/{action}"><input type="hidden" name="token" value="{esc(token)}">'
                    f'<input type="hidden" name="list" value="{esc(which)}">{fields}<button>{button}</button></form>')

        out = [note]
        for which, rows in shown:
            title = "My list" if which == "todo" else which.replace("_", " ").capitalize()
            out.append(f"<section class=\"person\"><h2>{html.escape(title)}</h2>")
            if rows is None:
                out.append('<p class="muted">Being set up: it appears in a minute or two.</p></section>')
                continue
            for r in rows:
                out.append(f'<p>{html.escape(r["summary"])} '
                           + form("done", which, f'<input type="hidden" name="item" value="{esc(r["uid"])}">', "Done")
                           + form("remove", which, f'<input type="hidden" name="item" value="{esc(r["uid"])}">', "Remove")
                           + "</p>")
            if not rows:
                out.append('<p class="muted">Nothing on it.</p>')
            out.append(form("add", which, '<label>Add <input name="item" maxlength="100" required></label>', "Add")
                       + "</section>")
        out.append('<h2>A new list</h2>' + form("new", "", '<label>Called <input name="name" maxlength="30" '
                                                          'required></label>', "Make it"))
        out.append('<p class="muted">Anyone with a Home Assistant login can open these lists there. '
                   'Nabu changes only yours, and never deletes a list: ask whoever looks after the home.</p>'
                   '<p><a href="/">Back</a></p>')
        return page("My lists", "".join(out))

    def form_token(request: web.Request) -> str:
        return taps.sessions.form_token(request.cookies.get(SESSION_COOKIE))

    async def my_lists(request: web.Request, note: str = "") -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None and s["kind"] == "account" else None
        if key is None or personal is None:
            return page("Nabu", "<p>My lists are for people who sign in with an account.</p>", 403)
        shown = []
        for which in personal.lists.slugs(key):
            have = await personal.exists(key, which)
            if have is None:
                return page("My lists", "<p>Home Assistant isn't answering right now. Try again later.</p>", 503)
            shown.append((which, await personal.items(key, which) if have else None))
        return lists_page(request, key, shown, note)

    async def lists_get(request: web.Request) -> web.Response:
        return await my_lists(request)

    async def lists_post(request: web.Request) -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None and s["kind"] == "account" else None
        if key is None or personal is None or not same_site(request):
            return page("Nabu", "<p>Not from here.</p>", 403)
        form = await request.post()
        if not hmac.compare_digest(str(form.get("token", "")), form_token(request)):
            return page("Nabu", "<p>Not from here.</p>", 403)
        action, which = request.match_info["action"], str(form.get("list", ""))
        item, ok = str(form.get("item", "")), False
        if action == "add":
            ok = await personal.put(key, which, item)
        elif action == "done":
            ok = await personal.complete(key, which, item)
        elif action == "remove":
            ok = await personal.drop(key, which, item)
        elif action == "new":
            said = personal.make(key, str(form.get("name", "")))
            return await my_lists(request, f"<p>{html.escape(said)}</p>")
        return await my_lists(request, "" if ok else "<p>That didn't work. Try again.</p>")

    # ── My reminders: accounts, their own reminders list only ─────────────────

    def when_text(due: str) -> str:
        """A due time as Home Assistant gave it ("2026-10-22T17:00:00"), as the page shows it."""
        return due.replace("T", " ")[:16]

    async def my_reminders(request: web.Request, note: str = "") -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None and s["kind"] == "account" else None
        if key is None or personal is None:
            return page("Nabu", "<p>My reminders are for people who sign in with an account.</p>", 403)
        have = await personal.exists(key, "reminders")
        if have is None:
            return page("My reminders", "<p>Home Assistant isn't answering right now. Try again later.</p>", 503)
        esc = lambda v: html.escape(v, quote=True)  # noqa: E731
        token = form_token(request)

        def form(action: str, fields: str, button: str) -> str:
            return (f'<form method="post" action="/reminders/{action}"><input type="hidden" name="token" '
                    f'value="{esc(token)}">{fields}<button>{button}</button></form>')

        out = [note]
        rows = (await personal.items(key, "reminders") or []) if have else []
        if not have:
            out.append('<p class="muted">Your reminders are being set up: they appear in a minute or two.</p>')
        for r in sorted(rows, key=lambda r: (r["due"] == "", r["due"])):
            when = f'<span class="muted">{html.escape(when_text(r["due"]))}</span> ' if r["due"] else ""
            item = f'<input type="hidden" name="item" value="{esc(r["uid"])}">'
            out.append(f'<p>{when}{html.escape(r["summary"])} {form("done", item, "Done")}'
                       f'{form("remove", item, "Remove")}</p>')
        if have and not rows:
            out.append('<p class="muted">No reminders.</p>')
        out.append('<h2>A new reminder</h2>' + form(
            "add", '<label>To <input name="item" maxlength="100" required></label>'
                   '<label>When <input type="datetime-local" name="when" required></label>', "Remind me"))
        out.append('<p class="muted">Or say "Okay Nabu, remind me to call mum at 5pm". A reminder chimes on '
                   'your screen when it is due and you are alone in front of it, and waits for you if you are '
                   'not.</p><p><a href="/">Back</a></p>')
        return page("My reminders", "".join(out))

    async def reminders_get(request: web.Request) -> web.Response:
        return await my_reminders(request)

    async def reminders_post(request: web.Request) -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None and s["kind"] == "account" else None
        if key is None or personal is None or not same_site(request):
            return page("Nabu", "<p>Not from here.</p>", 403)
        form = await request.post()
        if not hmac.compare_digest(str(form.get("token", "")), form_token(request)):
            return page("Nabu", "<p>Not from here.</p>", 403)
        action, item, ok = request.match_info["action"], str(form.get("item", "")), False
        if action == "add":
            try:
                due = datetime.datetime.strptime(str(form.get("when", "")), "%Y-%m-%dT%H:%M").astimezone()
            except ValueError:
                return await my_reminders(request, "<p>That time didn't make sense. Try again.</p>")
            said, ok = await personal.remind(key, " ".join(item.split())[:100], due,
                                             datetime.datetime.now().astimezone())
            return await my_reminders(request, f"<p>{html.escape(said)}</p>" if not ok else "")
        if action == "done":
            ok = await personal.complete(key, "reminders", item)
        elif action == "remove":
            ok = await personal.drop(key, "reminders", item)
        return await my_reminders(request, "" if ok else "<p>That didn't work. Try again.</p>")

    # ── People Nabu knows: the home's admins only ─────────────────────────────
    # Behind the provider's login (never an open path), so its headers are its own.

    def is_admin(request: web.Request) -> bool:
        if household is None or not taps.groups_header or not taps.admins:
            return False
        if not request.headers.get(taps.user_header):
            return False
        groups = {g.strip() for g in request.headers.get(taps.groups_header, "").split("|")}
        return any(a in groups for a in taps.admins)

    def admins_only() -> web.Response:
        return page("Nabu", "<p>This page is for the home's admins.</p>", 403)

    def field(name: str, value: str) -> str:
        return f'<input type="hidden" name="{name}" value="{html.escape(value, quote=True)}">'

    async def admin(request: web.Request) -> web.Response:
        if not is_admin(request):
            return admins_only()
        everyone = await household.everyone()
        rows = []
        for p in everyone:
            label, name = p["label"], html.escape(p["name"])
            tag = f'<span class="tag tag-{label.lower().replace(" ", "-")}">{label}</span>'
            where = " (the profile)" if p["profile"] else ""
            row = (f'<section class="person"><h2>{name}{tag}</h2>'
                   f'<p class="muted">{html.escape(", ".join(p["learned"]) or "nothing learned yet")}{where}</p>')
            if p["pictures"] and household.frigate is not None:
                row += (f'<p><a href="/admin/pictures/{urllib.parse.quote(p["key"], safe="")}">'
                        f'Face pictures ({p["pictures"]})</a></p>')
            if p["editable"]:
                row += (f'<form class="name" method="post" action="/admin/name">{field("key", p["key"])}'
                        f'<label>Fix the name <input name="name" maxlength="40" required '
                        f'value="{html.escape(p["name"], quote=True)}"></label><button>Save</button></form>')
                others = [a for a in everyone if household.can_merge(p["key"], a["key"])]
                if others:
                    options = "".join(f'<option value="{html.escape(a["key"], quote=True)}">'
                                      f'{html.escape(a["name"])} ({a["label"]})</option>' for a in others)
                    row += (f'<form method="post" action="/admin/link">{field("key", p["key"])}'
                            f'<select name="into" aria-label="Merge into">{options}</select>'
                            f'<button>Merge into…</button></form>')
                row += (f'<form method="post" action="/admin/forget">{field("key", p["key"])}'
                        f'<button>Forget</button></form>')
            rows.append(row + "</section>")
        legend = ('<p class="muted"><b>Account</b>: signed in, or in the home\'s profile; the name comes from '
                  'there. <b>Guest</b>: a name typed on this page, with no account. <b>Heard</b>: met by '
                  'voice, the name as Nabu heard it: fix it if it is spelled wrong.</p>')
        legend += ('<p class="muted"><b>Unknown</b>: a face that matched no one, with a name Nabu made up. '
                   'Merge them into who they are, or give them their name.</p>')
        if household.sightings is not None:
            legend += '<p><a href="/admin/sightings">Sightings</a>: who was seen where, and when.</p>'
        if household.frigate is not None and household.people.strangers:
            legend += '<p><a href="/admin/duplicates">Find duplicates</a>: Unknowns who look like someone else.</p>'
        return page("People Nabu knows", legend + "".join(rows))

    async def admin_sightings(request: web.Request) -> web.Response:
        if not is_admin(request) or household.sightings is None:
            return admins_only()
        names = taps.people.names
        person = request.query.get("person")
        place = request.query.get("place") or None
        try:
            days = max(0.0, min(float(request.query.get("days") or 7), 365.0))
        except ValueError:
            days = 7.0
        found = household.sightings.search(person=person if person in names or person == "" else None,
                                           place=place, days=days or None)
        def option(value: str, label: str, chosen) -> str:
            sel = " selected" if chosen == value else ""
            return f'<option value="{html.escape(value, quote=True)}"{sel}>{html.escape(label)}</option>'
        people_opts = option("_any", "Anyone", person if person is not None else "_any") + option("", "Nobody known", person) \
            + "".join(option(k, n, person) for k, n in sorted(names.items(), key=lambda kv: kv[1].casefold()))
        place_opts = option("", "Anywhere", place or "") + "".join(option(p, p, place) for p in household.sightings.places())
        form = (f'<form method="get" action="/admin/sightings"><select name="person" aria-label="Who">{people_opts}'
                f'</select> <select name="place" aria-label="Where">{place_opts}</select> '
                f'<label>Last <input name="days" value="{days:g}" inputmode="numeric" style="width:4rem"> days</label>'
                '<button>Search</button></form>')
        move_opts = option("", "Nobody known", "_none") \
            + "".join(option(k, n, "_none") for k, n in sorted(names.items(), key=lambda kv: kv[1].casefold()))
        rows = []
        for s in found:
            who = html.escape(names.get(s["person"] or "", "nobody known") if s["person"] else "nobody known")
            sure = f", {s['confidence']:.2f}" if s["confidence"] is not None else ""
            img = (f'<img src="/admin/sightings/{s["id"]}/picture" alt="" loading="lazy" '
                   'style="width:4rem;height:4rem;object-fit:cover;border-radius:.5rem;float:left;margin-right:.75rem">'
                   if s["picture"] or s["event"] else "")
            mover = (f'<form method="post" action="/admin/sightings/{s["id"]}/move">'
                     f'<select name="person" aria-label="Was actually">{move_opts}</select>'
                     '<label><input type="checkbox" name="add_picture" value="yes"> add this picture to them</label> '
                     '<button>Move</button></form>')
            rows.append(f'<section class="person">{img}<h2>{who}</h2><p class="muted">{html.escape(s["place"])}, '
                        f'{html.escape(s["when"])} ({html.escape(s["source"])}{sure})</p>{mover}'
                        '<div style="clear:both"></div></section>')
        body = form + ("".join(rows) if rows else "<p>Nobody seen, for that.</p>")
        return page("Sightings", body + '<p><a href="/admin">Back</a></p>')

    async def admin_picture(request: web.Request) -> web.Response:
        if not is_admin(request) or household.sightings is None:
            return admins_only()
        try:
            sighting = int(request.match_info["id"])
        except ValueError:
            return web.Response(status=404)
        image = household.sightings.picture(sighting)
        event = household.sightings.event(sighting) if image is None else None
        if image is None and event and household.frigate is not None:
            try:
                image = await household.frigate.thumbnail(event)
            except Exception:  # pylint: disable=broad-except
                image = None
        if image is None:
            return web.Response(status=404)
        return web.Response(body=image, content_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})

    async def admin_name(request: web.Request) -> web.Response:
        if not is_admin(request) or not same_site(request):
            return admins_only()
        form = await request.post()
        if not household.rename(str(form.get("key", "")), str(form.get("name", ""))):
            return page("Nabu", "<p>Nabu can't change that name.</p>", 400)
        return go("/admin")

    async def admin_forget(request: web.Request) -> web.Response:
        if not is_admin(request) or not same_site(request):
            return admins_only()
        form = await request.post()
        key = str(form.get("key", ""))
        name = taps.people.names.get(key, key)
        if not await household.forget(key):
            return page("Nabu", "<p>Nabu can't forget them here.</p>", 400)
        return page("Done", f"<p>Nabu forgot {html.escape(name)}.</p><p><a href=\"/admin\">Back</a></p>")

    async def admin_link(request: web.Request) -> web.Response:
        if not is_admin(request) or not same_site(request):
            return admins_only()
        form = await request.post()
        key, into = str(form.get("key", "")), str(form.get("into", ""))
        names = taps.people.names
        if key not in names or into not in names:
            return page("Nabu", "<p>Nabu doesn't know them.</p>", 400)
        if form.get("confirm") != "yes":
            return page("Link them?", (
                f"<p>This moves everything Nabu learned about <b>{html.escape(names[key])}</b> (voice, face "
                f"pictures, phones, cards, sightings) to <b>{html.escape(names[into])}</b>, and "
                f"{html.escape(names[key])} goes. It can't be split apart again.</p>"
                f'<form method="post" action="/admin/link">{field("key", key)}{field("into", into)}'
                f'{field("confirm", "yes")}<button>Yes, they are the same person</button></form>'
                '<p><a href="/admin">No, go back</a></p>'))
        moved = await household.link(key, into)
        if moved is None:
            return page("Nabu", "<p>Nabu can't link them.</p>", 400)
        what = ", ".join(moved) or "nothing learned yet"
        return page("Done", f"<p>{html.escape(names.get(into, into))} has it all now: {html.escape(what)}.</p>"
                            '<p><a href="/admin">Back</a></p>')

    def pictures_back(key: str) -> str:
        return f'<p><a href="/admin/pictures/{urllib.parse.quote(key, safe="")}">Back</a></p>'

    async def admin_pictures(request: web.Request) -> web.Response:
        if not is_admin(request) or household.frigate is None:
            return admins_only()
        key = request.match_info["key"]
        if key not in taps.people.names:
            return page("Nabu", "<p>Nabu doesn't know them.</p>", 404)
        files = await household.pictures(key)
        quoted = urllib.parse.quote(key, safe="")
        rows = []
        for file in files:
            src = f"/admin/pictures/{quoted}/{urllib.parse.quote(file, safe='')}"
            delete = ""
            if household.editable(key):
                delete = (f'<form method="post" action="/admin/pictures/delete">{field("key", key)}'
                          f'{field("file", file)}<button>Delete</button></form>')
            rows.append(f'<section class="person"><img src="{src}" alt="" loading="lazy" '
                        'style="width:8rem;height:8rem;object-fit:cover;border-radius:.5rem">'
                        f'<p class="muted">{html.escape(file)}</p>{delete}</section>')
        body = "".join(rows) if rows else "<p>No face pictures.</p>"
        return page(f"Face pictures of {html.escape(taps.people.names[key])}",
                    body + '<p><a href="/admin">Back</a></p>')

    async def admin_picture_file(request: web.Request) -> web.Response:
        if not is_admin(request) or household.frigate is None:
            return admins_only()
        key, file = request.match_info["key"], request.match_info["file"]
        if file not in await household.pictures(key):  # only what the library lists for them
            return web.Response(status=404)
        try:
            image = await household.frigate.image(key, file)
        except Exception:  # pylint: disable=broad-except
            return web.Response(status=404)
        return web.Response(body=image, content_type="image/webp" if file.endswith(".webp") else "image/jpeg",
                            headers={"Cache-Control": "private, max-age=600"})

    async def admin_picture_delete(request: web.Request) -> web.Response:
        if not is_admin(request) or not same_site(request) or household.frigate is None:
            return admins_only()
        form = await request.post()
        key, file = str(form.get("key", "")), str(form.get("file", ""))
        if key not in taps.people.names or file not in await household.pictures(key):
            return page("Nabu", "<p>Nabu doesn't have that picture.</p>", 400)
        if form.get("confirm") != "yes":
            quoted = urllib.parse.quote(key, safe="")
            return page("Delete this picture?", (
                f'<img src="/admin/pictures/{quoted}/{urllib.parse.quote(file, safe="")}" alt="" '
                'style="width:8rem;height:8rem;object-fit:cover;border-radius:.5rem">'
                f"<p>Nabu will stop using it to recognise <b>{html.escape(taps.people.names[key])}</b>.</p>"
                f'<form method="post" action="/admin/pictures/delete">{field("key", key)}{field("file", file)}'
                f'{field("confirm", "yes")}<button>Yes, delete it</button></form>'
                f'{pictures_back(key)}'))
        if not await household.remove_picture(key, file):
            return page("Nabu", "<p>Nabu can't delete that picture.</p>", 400)
        left = len(await household.pictures(key))
        note = "" if left else (f"<p>That was their last picture, so Nabu can no longer recognise them. "
                                'Forget them from <a href="/admin">the list</a> if they should go.</p>')
        return page("Done", f"<p>Deleted.</p>{note}" + (pictures_back(key) if left else '<p><a href="/admin">Back</a></p>'))

    async def admin_sighting_move(request: web.Request) -> web.Response:
        if not is_admin(request) or not same_site(request) or household.sightings is None:
            return admins_only()
        form = await request.post()
        try:
            sighting = int(request.match_info["id"])
        except ValueError:
            return web.Response(status=404)
        person = str(form.get("person", ""))
        if not await household.move_sighting(sighting, person or None, form.get("add_picture") == "yes"):
            return page("Nabu", "<p>Nabu can't move that sighting.</p>", 400)
        return go("/admin/sightings")

    async def admin_duplicates(request: web.Request) -> web.Response:
        if not is_admin(request) or household.frigate is None or household.sightings is None:
            return admins_only()
        run = request.method == "POST"
        if run and not same_site(request):
            return admins_only()
        names = taps.people.names
        pairs = await household.duplicates(fresh=run)
        rows = []
        for d in pairs:
            if d["key"] not in names or d["into"] not in names:
                continue
            rows.append(
                f'<section class="person"><img src="/admin/sightings/{d["sighting"]}/picture" alt="" '
                'style="width:4rem;height:4rem;object-fit:cover;border-radius:.5rem;float:left;margin-right:.75rem">'
                f'<h2>{html.escape(names[d["key"]])} looks like {html.escape(names[d["into"]])}</h2>'
                f'<p class="muted">{d["score"]:.2f}, seen at {html.escape(d["place"])}</p>'
                f'<form method="post" action="/admin/link">{field("key", d["key"])}{field("into", d["into"])}'
                '<button>Merge them…</button></form><div style="clear:both"></div></section>')
        body = ('<form method="post" action="/admin/duplicates"><button>Find duplicates</button></form>'
                '<p class="muted">Asks the face library who each Unknown\'s later pictures look like. It takes '
                'a moment, and is only done when you press this.</p>')
        body += "".join(rows) if rows else "<p>No likely duplicates.</p>"
        return page("Duplicates", body + '<p><a href="/admin">Back</a></p>')

    async def forget_phone(request: web.Request) -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None else None
        if key is None or not same_site(request):
            return page("Nabu", "<p>Not from here.</p>", 403)
        n = taps.forget_phones(key)
        return page("Done", f"<p>Nabu forgot {n} phone{'s' if n != 1 else ''} of yours.</p>")

    async def forget_me(request: web.Request) -> web.Response:
        s = session(request)
        key = key_of(s) if s is not None else None
        if key is None or not same_site(request) or forget_person is None:
            return page("Nabu", "<p>Not from here.</p>", 403)
        taps.forget_phones(key)
        if await forget_person(key):
            resp = page("Done", "<p>Nabu forgot you: your phones, face and voice. Tap again to say hello.</p>")
            if s["kind"] == "guest":
                resp.del_cookie(SESSION_COOKIE, path="/")
            return resp
        return page("Done", "<p>Nabu forgot your phones, face and voice. Your name is in the home's "
                            "settings, so it stays: ask whoever looks after them.</p>")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/signin", signin)
    app.router.add_get("/tap/{room}", tap)
    app.router.add_post("/name", name)
    app.router.add_post("/forget-phone", forget_phone)
    app.router.add_post("/forget-me", forget_me)
    app.router.add_get("/reminders", reminders_get)
    app.router.add_post("/reminders/{action:add|done|remove}", reminders_post)
    app.router.add_get("/lists", lists_get)
    app.router.add_post("/lists/{action:add|done|remove|new}", lists_post)
    app.router.add_get("/settings", settings)
    app.router.add_post("/settings", save_settings)
    app.router.add_get("/admin", admin)
    app.router.add_post("/admin/name", admin_name)
    app.router.add_post("/admin/forget", admin_forget)
    app.router.add_post("/admin/link", admin_link)
    app.router.add_get("/admin/sightings", admin_sightings)
    app.router.add_get("/admin/sightings/{id}/picture", admin_picture)
    app.router.add_post("/admin/sightings/{id}/move", admin_sighting_move)
    app.router.add_get("/admin/pictures/{key}", admin_pictures)
    app.router.add_get("/admin/pictures/{key}/{file}", admin_picture_file)
    app.router.add_post("/admin/pictures/delete", admin_picture_delete)
    app.router.add_get("/admin/duplicates", admin_duplicates)
    app.router.add_post("/admin/duplicates", admin_duplicates)
    return app
