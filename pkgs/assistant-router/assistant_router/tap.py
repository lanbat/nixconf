"""Tap your phone on a robot: who it is, there, and their phone's Wi-Fi address.

Each robot is an NFC tag holding a link to this page (firmware nfc.h), a new
one every minute: https://who.<domain>/tap/<room>?n=<nonce>. A phone held to
the robot opens it. Caddy lets it through only after Authentik's login
(forward auth), and passes on who that is (its user header, after the check:
a client's own is dropped; X-Authentik-Username) and where the
request came from (X-Forwarded-For). So:

- the signed-in user is a person here: their username is their key (a
  profile's person when it matches one, else added at runtime, people_store).
  A runtime person's name follows Authentik's on every visit, so renaming
  yourself there renames you here; a profile's person keeps the profile's;
- they are in that room, for two minutes ("tap" in people.py, as strong as a
  card): a hint for who is speaking, never authority over anything;
- the phone's Wi-Fi address, from the server's ARP table for the address it
  came from, is theirs: wifi-presence (a house sensor on the body socket) is
  told, and counts them home whenever that phone is on the network.

A link older than two minutes, or one for a room without a robot, only shows
the page. The page offers to forget the phone, or the person (one added here,
not one the profile declares). Learned phones are kept in a small JSON file
in the router's state directory: {mac: key}.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import urllib.parse
from typing import Any, Awaitable, Callable

from aiohttp import web

from .people import People

LOG = logging.getLogger("assistant_router")

LINK_PERIOD_S = 60.0     # a new link every minute
LINK_WITHIN_S = 120.0    # a first tap links the room's speaker of the last two minutes
MAX_PHONES = 48          # wifi-presence's kernel filter holds 60, with the profile's
PHONES_PER_PERSON = 4
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


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


class Taps:
    """The links, and the phones learned from taps."""

    def __init__(self, base_url: str, people: People, clock: Callable[[], float],
                 phones_path: str | None = None, remember: Callable[[str, str], None] | None = None,
                 runtime: Callable[[str], bool] | None = None, store=None,
                 arp_path: str = "/proc/net/arp", user_header: str = "X-Authentik-Username",
                 name_header: str | None = "X-Authentik-Name", learns: tuple[str, ...] = ()) -> None:
        self.base_url = base_url.rstrip("/")
        self.people = people
        self.clock = clock
        self.phones_path = phones_path
        self.phones = _load(phones_path)
        self._remember = remember or people.add
        self._runtime = runtime or (lambda key: False)  # added here, not by the profile
        self._store = store  # people_store.PeopleStore: logins linked to people (aliases)
        self.arp_path = arp_path
        self.user_header = user_header
        self.name_header = name_header
        self.learns = learns  # what a tap teaches the router meanwhile: "voice", "face" (the page says so)
        self._secret = secrets.token_bytes(16)  # links die with a restart; robots get new ones
        self.listeners: list[Callable[[dict[str, str]], Any]] = []

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

    def forget_phones(self, key: str) -> int:
        gone = [m for m, k in self.phones.items() if k == key]
        for m in gone:
            del self.phones[m]
        if gone:
            self._save()
        return len(gone)

    def refresh_name(self, key: str, name: str, username: str) -> None:
        """A runtime person's name from Authentik's, when it changed (the
        user renamed themselves there). Never a profile's person, and never
        to nothing: without the name header the stored name stays."""
        if not name or key not in self.people.names or not self._runtime(key):
            return
        fresh = display_name(name, username)
        if fresh != self.people.names[key]:
            LOG.info(json.dumps({"renamed": key}))
            self._remember(key, fresh)

    def tap(self, room: str, nonce: str, username: str, name: str, ip: str, rooms: list[str]) -> dict:
        """A phone opened a robot's link: what happened, for the page."""
        login = person_key(username)
        key = (self._store.resolve(login) if self._store is not None and login else None) or login
        out: dict = {"key": key, "name": None, "room": None, "phone": False, "stale": False}
        if key is None:
            return out
        room_name = next((r for r in rooms if _key(r) == _key(room)), None)
        if room_name is None or not self.valid(room, nonce):
            out["stale"] = True
            out["name"] = self.people.names.get(key) or display_name(name, username)
            return out
        if key not in self.people.names and self._store is not None:
            # A first tap by someone Nabu met by voice: the login becomes them,
            # instead of a second person (identity.py, "Meeting Nabu").
            spoke, _, _ = self.people.last_speaker(room_name, self.clock(), within=LINK_WITHIN_S)
            if spoke and self._runtime(spoke) and not self._store.linked(spoke):
                self._store.link(login, spoke)
                LOG.info(json.dumps({"linked": spoke}))
                key = out["key"] = spoke
        if key not in self.people.names:
            self._remember(key, display_name(name, username))
        else:
            self.refresh_name(key, name, username)
        out["name"] = self.people.names.get(key)
        out["room"] = room_name
        self.people.observe(room_name, "tap", key, 1.0, self.clock())
        mac = arp_lookup(ip, self.arp_path) if ip else None
        if mac and self.learn(mac, key):
            out["phone"] = True
        LOG.info(json.dumps({"tap": room_name, "person": key, "phone": out["phone"]}))
        return out


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
.muted {{ color: var(--muted); font-size: .9rem; }}
form {{ display: inline-block; margin: 1rem .5rem 0 0; }}
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


def make_tap_app(taps: Taps, rooms: Callable[[], list[str]],
                 forget_person: Callable[[str], Awaitable[bool]] | None = None) -> web.Application:
    """The page behind https://who.<domain>, on the loopback for Caddy."""

    def who(request: web.Request) -> tuple[str | None, str, str]:
        user = request.headers.get(taps.user_header, "")
        name = request.headers.get(taps.name_header, "") if taps.name_header else ""
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return user or None, name, ip

    def buttons(key: str) -> str:
        phones = sum(1 for k in taps.phones.values() if k == key)
        out = []
        if phones:
            out.append('<form method="post" action="/forget-phone"><button>Forget my phone</button></form>')
        if forget_person is not None:
            out.append('<form method="post" action="/forget-me"><button>Forget me</button></form>')
        return "".join(out)

    async def tap(request: web.Request) -> web.Response:
        user, name, ip = who(request)
        if user is None:
            return page("Nabu", "<p>Sign in first.</p>", 403)
        got = taps.tap(request.match_info["room"], request.query.get("n", ""), user, name, ip, rooms())
        if got["key"] is None:
            return page("Nabu", "<p>Nabu can't make a name from this login.</p>", 400)
        hi = f"Hi {got['name']}!"
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
                 'anything because of it.</p>' + buttons(got["key"]))
        return page(hi, body)

    async def index(request: web.Request) -> web.Response:
        user, auth_name, _ip = who(request)
        key = person_key(user or "")
        if key is None:
            return page("Nabu", "<p>Sign in first.</p>", 403)
        taps.refresh_name(key, auth_name, user or "")
        name = taps.people.names.get(key)
        body = "<p>Hold your phone to Nabu's head to say hello.</p>" + buttons(key)
        return page(f"Hi {name}!" if name else "Hi!", body)

    def same_site(request: web.Request) -> bool:
        # A form on this page, not one on another site posting with the
        # login cookie.
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        return origin.rstrip("/") == taps.base_url or origin.startswith(taps.base_url + "/")

    async def forget_phone(request: web.Request) -> web.Response:
        key = person_key(who(request)[0] or "")
        if key is None or not same_site(request):
            return page("Nabu", "<p>Not from here.</p>", 403)
        n = taps.forget_phones(key)
        return page("Done", f"<p>Nabu forgot {n} phone{'s' if n != 1 else ''} of yours.</p>")

    async def forget_me(request: web.Request) -> web.Response:
        key = person_key(who(request)[0] or "")
        if key is None or not same_site(request) or forget_person is None:
            return page("Nabu", "<p>Not from here.</p>", 403)
        taps.forget_phones(key)
        if await forget_person(key):
            return page("Done", "<p>Nabu forgot you: your phones, face and voice. Tap again to say hello.</p>")
        return page("Done", "<p>Nabu forgot your phones, face and voice. Your name is in the home's "
                            "settings, so it stays: ask whoever looks after them.</p>")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/tap/{room}", tap)
    app.router.add_post("/forget-phone", forget_phone)
    app.router.add_post("/forget-me", forget_me)
    return app
