"""What an Echo Show on TECHO5 does as the assistant router's body: the
decisions, without I/O (the bridge, __main__.py, does that).

The bridge is an ESPHome API client of the device (as Home Assistant is) and a
body of the router for the device's room (pkgs/assistant-router body.py,
"kind": "screen"). Everything goes through outputs, tuples the bridge carries
out:

    ("router", {...})                a message to the router
    ("set", object_id, value)        a device setting: a switch, select, number, text or light
    ("call", action, {arg: value})   one of the device's actions (TECHO5 docs/actions.md)
    ("image", why)                   a camera frame, answered with Brain.image(why, jpeg)
    ("ca",)                          install the CA over SSH, answered with Brain.ca_done(ok, now)

What it does:

- Says who is in front of it: the device's presence or person sensor.
- Asks the router who that is: a frame when a person comes into view, and
  every PROBE_S while one is there and the router names nobody ("seen").
- Hands a frame over for "remember me" ({"capture": true}).
- Greets ({"act": {"greet": {"name"}}}) with a notification, and shows a
  caption the same way.
- Shows the room's people their own screen: the person's dashboard view, glance
  chips, calendars and photos when the router places one known person in the
  room, the household's otherwise (Profile).
- Shows the sign-in code (the who page's link, as a QR code: qr.py) when
  someone it can't name stands in front of it, once each time they come, or
  on its action button.
- Turns its screen off when the room has been empty and the device idle for
  `screen_off_after`, and on again when anyone comes back. Only a screen it
  turned off itself is turned on again.
- Looks for the household's phones in what its Bluetooth hears, with
  room-presence's Recogniser, and reports them as the router's room sensors do.
- Keeps the device's own settings as the profile has them (`settings`).
- Puts the profile's root CA among the certificates the device trusts, so it
  checks Home Assistant's certificate rather than skipping the check (the
  device's root is read-only, so after each of its boots, over SSH: ca.py).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from room_presence import REPORT_SECONDS, Recogniser, Tracker

PROTOCOL = 1
CAN = ["caption", "greet", "capture", "phones", "here", "items", "view"]

PROBE_S = 20.0           # a frame this often while someone unnamed is in front
ITEMS_REFRESH_S = 60.0   # reminders, to-dos and events while a screen view is shown
CA_RETRY_S = 60.0        # an install of the CA that failed is tried again after this
SETTING_RETRY_S = 60.0   # a setting the device didn't take is sent again after this
_ITEMS_NEVER = object()  # _items_for before the first fetch is scheduled
REMINDER_SECONDS = 1800  # how long a reminder that came due stays up
GREET_SECONDS = 10       # how long a greeting stays up
THANKS_SECONDS = 5       # and the thanks that takes the sign-in code down
CODE_SECONDS = 60        # and the sign-in code
NOTIFY_TAG = "lanbat"


@dataclass
class Profile:
    view: str = ""                    # the view's path in the device's dashboard
    glance: list[str] = field(default_factory=list)
    calendars: list[str] = field(default_factory=list)
    immich_person: str = ""
    todo: str = ""
    reminders: str = ""
    next_up: list[str] = field(default_factory=list)  # the person's own choice of calendars for "Next up"

    @classmethod
    def from_json(cls, data: Any) -> "Profile":
        data = data if isinstance(data, dict) else {}
        return cls(view=str(data.get("view") or ""),
                   glance=[str(e) for e in data.get("glance") or []][:8],
                   calendars=[str(c) for c in data.get("calendars") or []],
                   immich_person=str(data.get("immich_person") or ""),
                   todo=str(data.get("todo") or ""),
                   reminders=str(data.get("reminders") or ""))


@dataclass
class DeviceConfig:
    name: str
    room: str
    dashboard: str = ""               # the device's dashboard (url path), "" for none
    # The streamed results page (dashcast's name for it, "web:Results"): while
    # the router shows a search's results the screen is this page, not a view
    # of the dashboard, so it fills the screen with no Home Assistant around it.
    results_page: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    profiles: dict[str, Profile] = field(default_factory=dict)  # person key -> theirs
    household: Profile = field(default_factory=Profile)
    screen_off_after: float = 300.0   # 0: never turned off
    ha_url: str = ""
    ha_token: str = ""
    code_url: str = ""                # where the device fetches the sign-in code, "" for none
    ssh_keys: str = ""                # authorized_keys for the device ("" leaves SSH alone)
    install_ca: bool = False          # put the profile's root CA among the device's trusted ones

    @classmethod
    def from_json(cls, data: dict, token: str = "", ssh_keys: str = "") -> "DeviceConfig":
        return cls(name=data["name"], room=data["room"], dashboard=data.get("dashboard") or "",
                   results_page=data.get("results_page") or "",
                   settings=dict(data.get("settings") or {}),
                   profiles={k: Profile.from_json(v) for k, v in (data.get("profiles") or {}).items()},
                   household=Profile.from_json(data.get("household")),
                   screen_off_after=float(data.get("screen_off_after", 300)),
                   ha_url=data.get("ha_url") or "", ha_token=token, code_url=data.get("code_url") or "",
                   ssh_keys=ssh_keys if data.get("install_ca") else "", install_ca=bool(data.get("install_ca")))


def _jpeg_b64(jpeg: bytes | None) -> str | None:
    return base64.b64encode(jpeg).decode() if jpeg else None


def _manufacturer_data(data: bytes) -> dict[int, bytes]:
    """An advertisement's manufacturer data (AD type 0xFF), by company ID."""
    out: dict[int, bytes] = {}
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + 1 + length > len(data):
            break
        if data[i + 1] == 0xFF and length >= 3:
            out[int.from_bytes(data[i + 2:i + 4], "little")] = bytes(data[i + 4:i + 1 + length])
        i += 1 + length
    return out


def mac(address: int) -> str:
    return ":".join(f"{(address >> s) & 0xFF:02X}" for s in range(40, -8, -8))


class Brain:
    def __init__(self, cfg: DeviceConfig, people: dict[str, str], beacons: dict[str, str] | None = None) -> None:
        self.cfg = cfg
        self.people = dict(people)
        self.recogniser = Recogniser(beacons or {}, {}, bonds="/nonexistent/*/*/info")
        self.tracker = Tracker()
        self.echo: dict[str, Any] = {}    # object_id -> the device's last state
        self.here: list[dict] = []        # the router's people in the room
        self.link: str | None = None      # the who page's link for this room, from the router
        self.present = False
        self._present_at: float | None = None
        self._person = False
        self._next_probe: float | None = None
        self._named = False               # the router named someone in front since they came
        self._code_shown = False          # the sign-in code shown since they came
        self._code_until: float | None = None  # the code is up until then
        self._unused_since: float | None = None
        self._dimmed = False              # the screen is off because this turned it off
        # What each of the device's actions was last given. The device keeps
        # them, and several (home_glance, calendar_sources, home_assistant) make
        # it drop every API client, Home Assistant's too, to re-register: so each
        # is sent once, and again only when it changes, across reconnects.
        self._sent: dict[str, Any] = {}
        self._ca = "no"                   # this connection: "no", "running", "done"; or the retry time
        self._asked: dict[str, tuple[Any, float]] = {}  # object_id -> (value sent, when)
        self._reported = float("-inf")
        # What it decided and why, for the log (__main__ drains it): who the
        # router said is here, which screen that gave, when the screen woke or
        # slept. Never part of the outputs.
        self.notes: list[dict] = []
        self._noted_here: list[str] | None = None
        self._noted_view: str | None = None
        self._view_override: str | None = None  # the router's view while it shows search results
        self._items_for: str | None | object = _ITEMS_NEVER
        self._next_items: float = 0.0
        self._last_items: dict | None = None
        self._fetch_gen: int = 0
        # Reminders last read for the person in front, with when they are due, and the ones
        # already rung: a reminder chimes once, when it comes due while they are alone here.
        self.wall = lambda: datetime.now().astimezone()
        self._due: list[tuple[str, str, datetime]] = []   # (uid, title, due)
        self._due_for: str | None = None
        self._rung: set[tuple[str, str]] = set()          # (uid, due) rung
        self.rung_changed = False                         # __main__ keeps them in a file, so a restart doesn't ring again

    def note(self, **what: Any) -> None:
        self.notes.append(what)
        del self.notes[:-50]  # nothing reads them for a while: keep the last few

    def take_notes(self) -> list[dict]:
        notes, self.notes = self.notes, []
        return notes

    # ── the device ──────────────────────────────────────────────────────────
    def connected(self, now: float) -> list:
        """The device answered: tell it where Home Assistant is and whose
        screen to show, unless it was told already (its actions reconnect it)."""
        self._asked.clear()
        out: list = []
        self._ca = "no"
        if self.cfg.install_ca and self.cfg.ssh_keys:
            out += self._call("ssh_keys", {"keys": self.cfg.ssh_keys})
        if self.cfg.ha_url and self.cfg.ha_token:
            out += self._call("home_assistant", {"url": self.cfg.ha_url, "token": self.cfg.ha_token})
        return out + self._apply_profile()

    def _call(self, action: str, args: dict) -> list:
        if self._sent.get(action) == args:
            return []
        self._sent[action] = args
        return [("call", action, args)]

    def hello(self) -> dict:
        return {"hello": {"room": self.cfg.room, "kind": "screen", "proto": PROTOCOL, "can": CAN}}

    def state(self, object_id: str, value: Any, now: float) -> list:
        self.echo[object_id] = value
        out = self._converge(object_id, now) + self._install_ca(now)
        if object_id in ("presence", "person"):
            out += self._presence(now)
        if object_id == "button_action" and value == "press":
            out += self._show_code(now)
        if object_id == "screen" and value is True:
            self._dimmed = False
        out += self._screen(now)
        return out

    def settings(self, now: float) -> list:
        """Every setting the device doesn't have as the profile says."""
        out: list = []
        for object_id in self.cfg.settings:
            out += self._converge(object_id, now)
        return out

    def _converge(self, object_id: str, now: float) -> list:
        if object_id not in self.cfg.settings or object_id not in self.echo:
            return []
        want = self.cfg.settings[object_id]
        if self.echo[object_id] == want:
            self._asked.pop(object_id, None)
            return []
        sent = self._asked.get(object_id)
        if sent is not None and sent[0] == want and now - sent[1] < SETTING_RETRY_S:
            return []
        self._asked[object_id] = (want, now)
        return [("set", object_id, want)]

    def _presence(self, now: float) -> list:
        person = self.echo.get("person") is True
        present = person or self.echo.get("presence") is True
        out: list = []
        if person and not self._person:
            self._next_probe = now  # someone came into view: who?
        if not person:
            self._next_probe = None
        self._person = person
        if present != self.present:
            self.present = present
            self._present_at = now if present else None
            self.note(present=present, person=person, screen=self.echo.get("screen"), dimmed=self._dimmed)
            if not present:
                self._named = False
                self._code_shown = False
            out.append(("router", {"state": self._state(now)}))
        return out + self._probe(now)

    def _state(self, now: float) -> dict:
        since = 0 if self._present_at is None else int(now - self._present_at)
        return {"present": self.present, "present_since_s": since, "asleep": self._dimmed}

    def _probe(self, now: float) -> list:
        if self._next_probe is None or now < self._next_probe:
            return []
        self._next_probe = None if self._named else now + PROBE_S
        return [("image", "probe")]

    def image(self, why: str, jpeg: bytes | None) -> list:
        if why == "capture":
            return [("router", {"face_image": _jpeg_b64(jpeg)})]
        if why == "probe" and jpeg:
            return [("router", {"face_probe": _jpeg_b64(jpeg), "why": "seen"})]
        return []

    # ── the router ──────────────────────────────────────────────────────────
    def router(self, msg: Any, now: float) -> list:
        if not isinstance(msg, dict):
            return []
        out: list = []
        if msg.get("capture") is True:
            out.append(("image", "capture"))
        if isinstance(msg.get("items_stale"), dict):
            # Their lists changed (by voice, or on the who page): read them at the next tick.
            self._next_items = now
        if isinstance(msg.get("tap"), dict) and isinstance(msg["tap"].get("url"), str):
            self.link = msg["tap"]["url"]
        if "irks" in msg:
            self.recogniser.from_router(msg["irks"])
        if isinstance(msg.get("face_result"), dict):
            self.note(face_result=msg["face_result"].get("person"), confidence=msg["face_result"].get("confidence"))
            out += self._face_result(msg["face_result"], now)
        if isinstance(msg.get("here"), list):
            before = self.here
            self.here = [h for h in msg["here"] if isinstance(h, dict) and isinstance(h.get("person"), str)]
            if len(self.here) == 1 and len(before) == 1 and before[0]["person"] == self.here[0]["person"] \
                    and before[0].get("prefs") != self.here[0].get("prefs"):
                self._next_items = now  # the same person chose other lists or calendars: read them now
            keys = [h["person"] for h in self.here]
            if keys != self._noted_here:
                self._noted_here = keys
                self.note(here=keys)
            out += self._items_here_changed(now)
            if not self.here and self._person and self._named:
                # The router lost them (it restarted, or its evidence ran out)
                # while someone is still in front: ask again who it is.
                self._named = False
                self._next_probe = now
            if self.here:
                self._named = True
                self._next_probe = None
                # The code did its work (or someone was placed otherwise): take
                # it down, unless a greeting in this message does that.
                greeting = isinstance(msg.get("act"), dict) and isinstance(msg["act"].get("greet"), dict)
                if self._code_until is not None and now < self._code_until and not greeting:
                    first = (self.here[0].get("name") or self.here[0]["person"]).split()[0][:40]
                    out.append(self._notify("", f"Thanks, {first}!", "mdi:check-circle-outline", THANKS_SECONDS))
                    self._code_until = None
            out += self._apply_profile() + self._screen(now)
        act = msg.get("act")
        if isinstance(act, dict):
            greet = act.get("greet")
            if isinstance(greet, dict) and isinstance(greet.get("name"), str) and greet["name"].strip():
                # A notification's message is required (TECHO5 refuses an empty one).
                out.append(self._notify("", f"Hi {greet['name'].strip()[:40]}!", "mdi:hand-wave", GREET_SECONDS))
                self._code_until = None  # one at a time: the greeting replaces the code
            if isinstance(act.get("view"), str):
                # "media" while a search's results are open, "" to go back to
                # the person's own view.
                wanted = act["view"].strip() or None
                if wanted != self._view_override:
                    self._view_override = wanted
                    out += self._apply_profile()
            if isinstance(act.get("caption"), str) and act["caption"].strip():
                # A caption shows for GREET_SECONDS unless the sender says longer
                # (a list of videos the person is choosing from, say).
                seconds = act.get("caption_seconds")
                if type(seconds) is not int or not 1 <= seconds <= 600:
                    seconds = GREET_SECONDS
                out.append(self._notify("", act["caption"].strip()[:200], "mdi:message-text-outline", seconds))
        return out

    def _face_result(self, result: dict, now: float) -> list:
        if result.get("person"):
            self._named = True
            self._next_probe = None
            return []
        # Someone in front the cameras can't name and the router places nobody:
        # the sign-in code, once while they are here.
        if self._person and not self.here and not self._code_shown:
            return self._show_code(now)
        return []

    def _show_code(self, now: float) -> list:
        if not self.link or not self.cfg.code_url:
            return []
        self._code_shown = True
        self._code_until = now + CODE_SECONDS
        return [("call", "notify_picture", {
            "message": "Scan with your phone to tell me who you are", "title": "Who's this?",
            "picture": self.cfg.code_url, "chime": "", "speak": "", "sound": "",
            "tag": f"{NOTIFY_TAG}-code", "seconds": CODE_SECONDS})]

    def _notify(self, title: str, message: str, icon: str, seconds: int) -> tuple:
        if not message:  # TECHO5 refuses a notification without a message
            title, message = "", title
        return ("call", "notify", {"message": message, "title": title, "icon": icon, "chime": "", "speak": "",
                                   "tag": NOTIFY_TAG, "seconds": seconds})

    # ── whose screen ────────────────────────────────────────────────────────
    def profile(self) -> tuple[str | None, Profile]:
        """(the person, their profile) for one known person in the room; the
        household's otherwise. The router places only people it knows, the
        profile's and those it met (people_store), so `here` is who is known.
        A person has a view of their own in the dashboard only when the
        profile names them (pkgs/home-assistant-dashboards)."""
        known = [h["person"] for h in self.here]
        if len(known) == 1:
            key = known[0]
            mine = self.cfg.profiles.get(key)
            default = Profile(view=key if key in self.people else "",
                              glance=[f"sensor.lanbat_people_where_{k}" for k in sorted(self.people)
                                      if k != key][:8],
                              todo=f"todo.{key}_todo", reminders=f"todo.{key}_reminders")
            if mine is not None:
                default = Profile(view=mine.view or default.view, glance=mine.glance or default.glance,
                                  calendars=mine.calendars,
                                  immich_person=mine.immich_person,
                                  todo=mine.todo or f"todo.{key}_todo",
                                  reminders=mine.reminders or f"todo.{key}_reminders")
            return key, self._chosen(default, self.here[0].get("prefs"))
        return None, self.cfg.household

    @staticmethod
    def _chosen(p: Profile, prefs: Any) -> Profile:
        """What the person chose on the who page (the router's prefs.py) over
        the profile: the calendars of "Next up", the to-do list and the photos.
        Never the glance chips or the device's own calendars: those make it
        drop every connection, Home Assistant's too, each time someone arrives."""
        if not isinstance(prefs, dict):
            return p
        cals = prefs.get("calendars")
        if isinstance(cals, list):
            p.next_up = [c for c in cals if isinstance(c, str) and c.startswith("calendar.")][:8]
        if isinstance(prefs.get("todo"), str) and prefs["todo"].startswith("todo."):
            p.todo = prefs["todo"]
        if isinstance(prefs.get("immich_person"), str) and prefs["immich_person"]:
            p.immich_person = prefs["immich_person"][:60]
        return p

    def calendars_for(self, prof: Profile, device: bool = False) -> list[str]:
        """The calendars of a person's screen: their own (what they chose for Next up, else
        the profile's), then the house's, which every person's has too. The device's own
        calendar page (`device`) ignores what they chose for Next up: it keeps the profile's."""
        own = prof.calendars if device else (prof.next_up or prof.calendars)
        return list(dict.fromkeys([*own, *self.cfg.household.calendars]))

    def wants_items(self) -> tuple[str | None, Profile]:
        return self.profile()

    def _empty_items(self, person: str | None = None) -> dict:
        return {"items": {"person": person, "reminders": [], "todos": [], "more": 0, "events": []}}

    def _ring(self) -> list:
        """A chime and the reminder, for each one of the person in front that has come due.
        Only while they are the one person here: a reminder is never shown to a bystander."""
        if not self._due or len(self.here) != 1 or self.here[0]["person"] != self._due_for:
            return []
        now = self.wall()
        out: list = []
        for uid, title, due in self._due:
            key = (uid, due.isoformat())
            if due <= now and key not in self._rung:
                self._rung.add(key)
                self.rung_changed = True
                self.note(reminder=title[:40], for_=self._due_for)
                out.append(("call", "notify", {"message": title, "title": "Reminder", "icon": "mdi:bell-ring",
                                               "chime": "on", "speak": "", "tag": f"{NOTIFY_TAG}-reminder",
                                               "seconds": REMINDER_SECONDS}))
        return out

    def load_rung(self, rung: list) -> None:
        """The reminders rung before the last stop, as [uid, due] pairs."""
        self._rung = {(str(uid), str(due)) for uid, due in (r for r in rung if isinstance(r, (list, tuple))
                                                                 and len(r) == 2)}

    def rung(self) -> list:
        return sorted([uid, due] for uid, due in self._rung)

    def _remember_due(self, person: str | None, reminders: list) -> None:
        self._due = [(r.uid, r.title, r.due) for r in reminders if r.due is not None and person is not None]
        self._due_for = person
        live = {(uid, due.isoformat()) for uid, _, due in self._due}
        if person is not None and not self._rung <= live:
            # One that was done or removed is forgotten, and rings again if it returns. Only on
            # a read for someone: the household's read (nobody) says nothing of anyone's reminders.
            self._rung &= live
            self.rung_changed = True

    def _send_items(self, msg: dict, *, force: bool = False) -> list:
        if not force and msg == self._last_items:
            return []
        self._last_items = msg
        return [("router", msg)]

    def items(self, now: float, data: dict | None, *, for_person: str | None = None) -> list:
        from . import lists

        if data is None:
            self._remember_due(None, [])
            return self._send_items(self._empty_items(for_person))
        self._remember_due(for_person, data["reminders"])
        payload = {
            "person": for_person,
            "reminders": [{"uid": r.uid, "title": lists.clean(r.title), "due": r.due.isoformat() if r.due else None,
                           "list": r.list}
                          for r in data["reminders"]],
            "todos": [{"title": lists.clean(t.title)} for t in data["todos"]],
            "more": data["more"],
            "events": [{"title": lists.clean(e.title), "start": e.start.isoformat(), "all_day": e.all_day}
                       for e in data["events"]],
        }
        return self._send_items({"items": payload}) + self._ring()

    def merge_fetch(self, rem_raw: dict | None, todo_raw: dict | None, ev_raw: dict | None,
                    *, now=None) -> dict | None:
        from datetime import datetime

        from . import lists

        if rem_raw is None and todo_raw is None and ev_raw is None:
            return None
        if now is None:
            now = datetime.now().astimezone()
        person, prof = self.wants_items()
        reminders: list = []
        todos: list = []
        more = 0
        if person is not None and prof.reminders and rem_raw is not None:
            reminders = lists.pick_reminders(lists.parse_items(prof.reminders, rem_raw), now)
        if person is not None and prof.todo and todo_raw is not None:
            todos, more = lists.pick_todos(lists.parse_items(prof.todo, todo_raw))
        cals = self.calendars_for(prof)
        events = lists.parse_events(ev_raw, now) if ev_raw is not None and cals else []
        return {"reminders": reminders, "todos": todos, "more": more, "events": events}

    def _fetch_target_ok(self, for_person: str | None) -> bool:
        n = len(self.here)
        if n >= 2:
            return False
        if n == 1:
            return for_person == self.here[0]["person"]
        return for_person is None

    def apply_fetch(self, for_person: str | None, gen: int, rem_raw: dict | None, todo_raw: dict | None,
                    ev_raw: dict | None, *, now=None, mono: float = 0.0) -> list:
        if gen != self._fetch_gen or not self._fetch_target_ok(for_person):
            return []
        data = self.merge_fetch(rem_raw, todo_raw, ev_raw, now=now)
        if data is None:
            return []
        return self.items(mono, data, for_person=for_person)

    def router_connected(self, now: float) -> list:
        self._last_items = None
        if len(self.here) >= 2 or not self._can_fetch() or not self._screen_shows_items():
            return []
        person, prof = self.wants_items()
        self._next_items = now + ITEMS_REFRESH_S
        self._items_for = person if len(self.here) == 1 else None
        return self._maybe_fetch(person, prof, now)

    def _can_fetch(self) -> bool:
        return bool(self.cfg.ha_url and self.cfg.ha_token)

    def _screen_shows_items(self) -> bool:
        if self._dimmed:
            return False
        return self.echo.get("screen") is not False

    def _invalidate_fetches(self) -> None:
        self._fetch_gen += 1

    def _items_here_changed(self, now: float) -> list:
        n = len(self.here)
        person, prof = self.wants_items()
        prev = self._items_for
        if n >= 2:
            self._invalidate_fetches()
            self._items_for = "__multi__"
            self._next_items = now + ITEMS_REFRESH_S
            return self._send_items(self._empty_items(), force=True)
        if n == 1:
            out: list = []
            if prev != person:
                if prev is not _ITEMS_NEVER:
                    self._invalidate_fetches()
                self._items_for = person
                self._next_items = now + ITEMS_REFRESH_S
                out += self._maybe_fetch(person, prof, now)
            return out
        out = []
        if isinstance(prev, str):
            self._invalidate_fetches()
            out += self._send_items(self._empty_items(), force=True)
        if prev is not None:
            self._items_for = None
            self._next_items = now + ITEMS_REFRESH_S
            out += self._maybe_fetch(None, prof, now)
        return out

    def _maybe_fetch(self, person: str | None, prof: Profile, now: float) -> list:
        if not self._can_fetch():
            return []
        return [("fetch", person, prof, self._fetch_gen)]

    def _items_tick(self, now: float) -> list:
        if len(self.here) >= 2 or not self._can_fetch() or not self._screen_shows_items():
            return []
        person, prof = self.wants_items()
        if now < self._next_items:
            return []
        self._next_items = now + ITEMS_REFRESH_S
        self._items_for = person if len(self.here) == 1 else None
        return self._maybe_fetch(person, prof, now)

    def _apply_profile(self) -> list:
        _, p = self.profile()
        out: list = []
        if self.cfg.dashboard:
            view = self._view_override or p.view
            if self._view_override == "media" and self.cfg.results_page:
                path = self.cfg.results_page
            else:
                path = f"{self.cfg.dashboard}/{view}" if view else self.cfg.dashboard
            if path != self._noted_view:
                self._noted_view = path
                who, _ = self.profile()
                self.note(view=path, for_=who or "household")
            out += self._call("dashboard_path", {"path": path})
        out += self._call("home_glance", {"entities": ", ".join(p.glance)})
        out += self._call("calendar_sources", {"calendars": ", ".join(self.calendars_for(p, device=True))})
        if self._sent.get("immich_person") != p.immich_person:
            self._sent["immich_person"] = p.immich_person
            out.append(("set", "immich_person", p.immich_person))
        return out

    # ── the screen ──────────────────────────────────────────────────────────
    def in_use(self) -> bool:
        return (self.echo.get("now_playing_state") in ("playing", "buffering")
                or self.echo.get("video_state") not in (None, "idle", "")
                or self.echo.get("sounding") is True)

    def _screen(self, now: float) -> list:
        if self.cfg.screen_off_after <= 0:
            return []
        empty = not self.present and not self.here
        if not empty or self.in_use():
            self._unused_since = None
            if self._dimmed:
                self._dimmed = False
                self.note(screen="on", why="someone is here or it is in use")
                return [("set", "screen", True), ("router", {"state": self._state(now)})]
            return []
        if self._unused_since is None:
            self._unused_since = now
        if not self._dimmed and self.echo.get("screen") is True \
                and now - self._unused_since >= self.cfg.screen_off_after:
            self._dimmed = True
            self.note(screen="off", why=f"empty and unused for {int(now - self._unused_since)} s")
            return [("set", "screen", False), ("router", {"state": self._state(now)})]
        return []

    # ── Bluetooth ───────────────────────────────────────────────────────────
    def advert(self, address: int, rssi: int, data: bytes, now: float) -> None:
        person = self.recogniser.person(mac(address), _manufacturer_data(data))
        if person is not None:
            self.tracker.observe(person, rssi, now)

    # ── every second ────────────────────────────────────────────────────────
    def _install_ca(self, now: float) -> list:
        """Once a connection, as soon as the device's SSH is on: the install
        checks itself, so a device that has it already costs one login."""
        if not self.cfg.install_ca or not self.cfg.ssh_keys or self.echo.get("ssh") is not True:
            return []
        if self._ca == "no" or (isinstance(self._ca, float) and now >= self._ca):
            self._ca = "running"
            return [("ca",)]
        return []

    def ca_done(self, ok: bool, now: float) -> None:
        self._ca = "done" if ok else now + CA_RETRY_S

    def tick(self, now: float) -> list:
        out = self._probe(now) + self._screen(now) + self._install_ca(now) + self._items_tick(now) + self._ring()
        if now - self._reported >= REPORT_SECONDS:
            self._reported = now
            seen = self.tracker.report(now)
            if seen:
                out.append(("router", {"seen": seen}))
        return out
