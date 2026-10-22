import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from room_presence import _ah
from techo5_bridge.brain import (
    CAN,
    ITEMS_REFRESH_S,
    PROBE_S,
    Brain,
    DeviceConfig,
    Profile,
    _manufacturer_data,
    mac,
)
from techo5_bridge.lists import Event, Item

PEOPLE = {"kiril": "Kiril Example", "maria": "Maria"}


def brain(**extra) -> Brain:
    people = extra.pop("people", PEOPLE)
    cfg = DeviceConfig(name="kitchen", room="Kitchen", dashboard="lanbat-screen-kitchen",
                       code_url="http://192.0.2.10:8775/code/x.png", **extra)
    return Brain(cfg, people)


def calls(outs, action=None):
    return [o for o in outs if o[0] == "call" and (action is None or o[1] == action)]


def to_router(outs):
    return [o[1] for o in outs if o[0] == "router"]


def fetches(outs):
    return [o for o in outs if o[0] == "fetch"]


EMPTY_ITEMS = {"items": {"person": None, "reminders": [], "todos": [], "more": 0, "events": []}}


def test_it_says_hello_as_a_screen():
    hello = brain().hello()["hello"]
    assert hello["room"] == "Kitchen" and hello["kind"] == "screen" and hello["can"] == CAN
    assert "gesture" not in CAN and "mood" not in CAN


def test_it_tells_the_device_where_home_assistant_is():
    b = brain(ha_url="https://ha.example.org", ha_token="t0ken")
    outs = b.connected(0.0)
    assert ("call", "home_assistant", {"url": "https://ha.example.org", "token": "t0ken"}) in outs
    assert not calls(brain().connected(0.0), "home_assistant")  # no token: not called


def test_someone_in_front_is_reported_and_looked_at():
    b = brain()
    outs = b.state("person", True, 10.0)
    assert to_router(outs) == [{"state": {"present": True, "present_since_s": 0, "asleep": False}}]
    assert ("image", "probe") in outs
    probe = b.image("probe", b"\xff\xd8jpeg")
    assert probe == [("router", {"face_probe": base64.b64encode(b"\xff\xd8jpeg").decode(), "why": "seen"})]
    # Not named yet: again a while later, not before.
    assert ("image", "probe") not in b.tick(10.0 + PROBE_S - 1)
    assert ("image", "probe") in b.tick(10.0 + PROBE_S)
    # Named: no more frames.
    b.router({"here": [{"person": "kiril", "name": "Kiril Example", "confidence": 0.9}]}, 50.0)
    assert ("image", "probe") not in b.tick(50.0 + 2 * PROBE_S)
    outs = b.state("person", False, 100.0)
    assert to_router(outs)[0]["state"]["present"] is False


def test_presence_alone_counts_but_is_not_a_face():
    b = brain()
    outs = b.state("presence", True, 1.0)
    assert to_router(outs)[0]["state"]["present"] is True
    assert ("image", "probe") not in outs


def test_a_face_for_remember_me():
    b = brain()
    assert b.router({"capture": True}, 0.0) == [("image", "capture")]
    assert b.image("capture", b"jpg") == [("router", {"face_image": base64.b64encode(b"jpg").decode()})]
    assert b.image("capture", None) == [("router", {"face_image": None})]


def test_a_greeting_and_a_caption_are_notifications():
    b = brain()
    (out,) = b.router({"act": {"greet": {"name": "Kiril"}}}, 0.0)
    # TECHO5 refuses a notification whose message is empty: the greeting is the message.
    assert out[1] == "notify" and out[2]["message"] == "Hi Kiril!" and out[2]["seconds"] > 0
    assert set(out[2]) == {"message", "title", "icon", "chime", "speak", "tag", "seconds"}  # every argument
    (out,) = b.router({"act": {"caption": "Dinner's ready"}}, 0.0)
    assert out[2]["message"] == "Dinner's ready"


def test_one_known_person_gets_their_own_screen():
    b = brain(profiles={"kiril": Profile(glance=["sensor.washer"], immich_person="Kiril")},
              household=Profile(calendars=["calendar.family"], glance=["binary_sensor.front_door"]))
    outs = b.router({"here": [{"person": "kiril", "name": "Kiril", "confidence": 0.9}]}, 0.0)
    assert ("call", "dashboard_path", {"path": "lanbat-screen-kitchen/kiril"}) in outs
    assert ("call", "home_glance", {"entities": "sensor.washer"}) in outs
    assert ("call", "calendar_sources", {"calendars": "calendar.family"}) in outs  # the household's, theirs unset
    assert ("set", "immich_person", "Kiril") in outs
    # Nothing changed: nothing sent again.
    assert not calls(b.router({"here": [{"person": "kiril", "name": "Kiril", "confidence": 0.8}]}, 1.0))
    # Two people: the household's screen.
    outs = b.router({"here": [{"person": "kiril"}, {"person": "maria"}]}, 2.0)
    assert ("call", "dashboard_path", {"path": "lanbat-screen-kitchen"}) in outs
    assert ("call", "home_glance", {"entities": "binary_sensor.front_door"}) in outs
    assert ("set", "immich_person", "") in outs


def test_a_person_without_a_profile_sees_where_the_others_are():
    b = brain()
    outs = b.router({"here": [{"person": "maria"}]}, 0.0)
    assert ("call", "dashboard_path", {"path": "lanbat-screen-kitchen/maria"}) in outs
    assert ("call", "home_glance", {"entities": "sensor.lanbat_people_where_kiril"}) in outs


def test_what_a_person_chose_on_the_who_page_wins_over_the_profile():
    b = brain(profiles={"kiril": Profile(glance=["sensor.washer"], calendars=["calendar.work"])},
              household=Profile(calendars=["calendar.family"]), ha_url="https://ha.example.org", ha_token="t")
    prefs = {"calendars": ["calendar.kiril", "sensor.nope"], "todo": "todo.shopping_list", "immich_person": "K"}
    outs = b.router({"here": [{"person": "kiril", "name": "Kiril", "prefs": prefs}]}, 0.0)
    _, p = b.wants_items()
    assert p.next_up == ["calendar.kiril"] and p.todo == "todo.shopping_list"
    assert p.reminders == "todo.kiril_reminders"  # never chosen: the person's own
    assert ("set", "immich_person", "K") in outs
    # The device's own glance chips and calendars stay the profile's: changing
    # them makes it drop every connection.
    assert ("call", "home_glance", {"entities": "sensor.washer"}) in outs
    assert ("call", "calendar_sources", {"calendars": "calendar.work, calendar.family"}) in outs  # and the house's
    assert fetches(outs)[0][2].todo == "todo.shopping_list"


def test_the_house_calendar_is_in_everyones_next_up():
    b = brain(profiles={"kiril": Profile(calendars=["calendar.work"])},
              household=Profile(calendars=["calendar.family", "calendar.work"]),
              ha_url="https://ha.example.org", ha_token="t")
    # Their profile's, then the house's, each once.
    b.router({"here": [{"person": "kiril"}]}, 0.0)
    _, p = b.wants_items()
    assert b.calendars_for(p) == ["calendar.work", "calendar.family"]
    # What they chose for Next up replaces the profile's, never the house's.
    b.router({"here": [{"person": "kiril", "prefs": {"calendars": ["calendar.kiril"]}}]}, 1.0)
    _, p = b.wants_items()
    assert b.calendars_for(p) == ["calendar.kiril", "calendar.family", "calendar.work"]
    # The device's own calendar page keeps the profile's, plus the house's.
    assert b.calendars_for(p, device=True) == ["calendar.work", "calendar.family"]
    # Someone with no profile, and nobody: the house's alone.
    b.router({"here": [{"person": "maria"}]}, 2.0)
    assert b.calendars_for(b.wants_items()[1]) == ["calendar.family", "calendar.work"]
    b.router({"here": []}, 3.0)
    assert b.calendars_for(b.wants_items()[1]) == ["calendar.family", "calendar.work"]


def test_new_choices_are_read_at_once():
    b = brain(ha_url="https://ha.example.org", ha_token="t")
    b.router({"here": [{"person": "kiril"}]}, 0.0)
    assert not fetches(b.tick(5.0))  # read when they came; next in a minute
    b.router({"here": [{"person": "kiril", "prefs": {"todo": "todo.shopping_list"}}]}, 6.0)
    (fetch,) = fetches(b.tick(7.0))
    assert fetch[2].todo == "todo.shopping_list"


def test_a_changed_list_is_read_at_once():
    b = brain(ha_url="https://ha.example.org", ha_token="t")
    b.router({"here": [{"person": "kiril"}]}, 0.0)
    assert not fetches(b.tick(5.0))  # read when they came; the next in a minute
    b.router({"items_stale": {"person": "kiril"}}, 6.0)
    assert fetches(b.tick(6.5))
    assert "items" in CAN


def test_someone_it_cant_name_is_shown_the_sign_in_code_once():
    b = brain()
    b.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    b.state("person", True, 1.0)
    (code,) = calls(b.router({"face_result": {"person": None, "confidence": None}}, 2.0), "notify_picture")
    assert code[2]["picture"] == "http://192.0.2.10:8775/code/x.png"
    assert not calls(b.router({"face_result": {"person": None}}, 30.0))  # once while they are here
    b.state("person", False, 40.0)
    b.state("presence", False, 40.0)
    b.state("person", True, 50.0)
    assert calls(b.router({"face_result": {"person": None}}, 51.0), "notify_picture")  # they came back


def test_no_code_for_someone_named_or_already_placed():
    b = brain()
    b.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    b.state("person", True, 1.0)
    assert not calls(b.router({"face_result": {"person": "Kiril", "confidence": 0.9}}, 2.0))
    b2 = brain()
    b2.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    b2.state("person", True, 1.0)
    b2.router({"here": [{"person": "maria"}]}, 1.5)
    assert not calls(b2.router({"face_result": {"person": None}}, 2.0), "notify_picture")


def test_the_action_button_shows_the_code():
    b = brain()
    b.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    assert calls(b.state("button_action", "press", 1.0), "notify_picture")


def test_the_screen_goes_off_in_an_empty_unused_room_and_back_on():
    b = brain(screen_off_after=300)
    b.state("screen", True, 0.0)
    assert ("set", "screen", False) not in b.tick(299.0)
    outs = b.tick(300.0)
    assert ("set", "screen", False) in outs
    assert to_router(outs)[0]["state"]["asleep"] is True
    outs = b.state("presence", True, 400.0)
    assert ("set", "screen", True) in outs


def test_the_screen_stays_on_while_in_use_or_someone_is_placed_there():
    b = brain(screen_off_after=60)
    b.state("screen", True, 0.0)
    b.state("now_playing_state", "playing", 0.0)
    assert ("set", "screen", False) not in b.tick(1000.0)
    b.state("now_playing_state", "idle", 1000.0)
    b.router({"here": [{"person": "kiril"}]}, 1000.0)  # the phone says they are here, out of view
    assert ("set", "screen", False) not in b.tick(2000.0)
    b.router({"here": []}, 2000.0)
    assert ("set", "screen", False) in b.tick(2060.0)


def test_a_screen_someone_turned_off_is_left_off():
    b = brain(screen_off_after=60)
    b.state("screen", False, 0.0)
    assert ("set", "screen", False) not in b.tick(100.0)
    assert ("set", "screen", True) not in b.state("presence", True, 200.0)


def test_never_off_when_turned_off_in_the_profile():
    b = brain(screen_off_after=0)
    b.state("screen", True, 0.0)
    assert ("set", "screen", False) not in b.tick(10_000.0)


def test_settings_are_kept_as_the_profile_has_them():
    b = brain(settings={"person_detection": True, "screen_dashboard": "Drawn on the device"})
    assert b.state("person_detection", False, 0.0) == [("set", "person_detection", True)]
    assert b.state("person_detection", False, 1.0) == []  # sent: not again at once
    assert ("set", "person_detection", True) in b.settings(61.0)  # it didn't take: again
    assert b.state("screen_dashboard", "Drawn on the device", 0.0) == []
    assert b.state("person_detection", True, 62.0) == []


def test_household_phones_in_what_its_bluetooth_hears():
    irk = bytes.fromhex("ec0234a357c8ad05341010a60a397d9b")
    prand = bytes([0x4F, 0x12, 0x34])  # a resolvable private address: top bits 01
    address = int.from_bytes(prand + _ah(irk, prand), "big")
    b = brain()
    b.router({"irks": {"kiril": [irk.hex()]}}, 0.0)
    b.advert(address, -60, b"", 1.0)
    b.advert(0x112233445566, -50, b"", 1.0)  # someone else's
    seen = to_router(b.tick(1.0))
    assert seen == [{"seen": [{"person": "kiril", "rssi": -60}]}]


def test_manufacturer_data_and_addresses():
    data = bytes([2, 0x01, 0x06, 5, 0xFF, 0x4C, 0x00, 0x02, 0x15])
    assert _manufacturer_data(data) == {0x004C: bytes([0x02, 0x15])}
    assert _manufacturer_data(bytes([9, 0xFF, 1])) == {}  # cut short: nothing
    assert mac(0x112233445566) == "11:22:33:44:55:66"


def test_someone_the_router_met_gets_the_home_view_with_their_photos_and_chips():
    # Known to the router at runtime (a tap), not in the profile's people: no
    # view of their own in the dashboard.
    b = brain(profiles={"akadmin": Profile(immich_person="Kiril")})
    outs = b.router({"here": [{"person": "akadmin", "name": "Kiril", "confidence": 0.98}]}, 0.0)
    assert ("call", "dashboard_path", {"path": "lanbat-screen-kitchen"}) in outs
    assert ("set", "immich_person", "Kiril") in outs
    (greet,) = b.router({"act": {"greet": {"name": "Kiril"}}}, 1.0)
    assert greet[2]["message"] == "Hi Kiril!"


def test_a_reconnect_sends_nothing_the_device_already_has():
    # home_glance and calendar_sources make the device drop every client to
    # re-register, Home Assistant's too: sending them on each connect looped.
    b = brain(ha_url="https://ha.example.org", ha_token="t0ken")
    first = b.connected(0.0)
    assert calls(first, "home_assistant") and calls(first, "home_glance") and calls(first, "calendar_sources")
    assert b.connected(5.0) == []
    # Only what changes goes out.
    outs = b.router({"here": [{"person": "maria"}]}, 10.0)
    assert ("call", "home_glance", {"entities": "sensor.lanbat_people_where_kiril"}) in outs
    assert not calls(outs, "calendar_sources")


def test_the_ca_goes_on_once_ssh_is_on_and_again_after_a_failure():
    b = brain(install_ca=True, ssh_keys="ssh-ed25519 AAAA lanbat-techo5-bridge")
    first = b.connected(0.0)
    assert ("call", "ssh_keys", {"keys": "ssh-ed25519 AAAA lanbat-techo5-bridge"}) in first
    assert ("ca",) not in b.state("ssh", False, 1.0)  # not before its SSH is on
    assert ("ca",) in b.state("ssh", True, 2.0)
    assert ("ca",) not in b.tick(3.0)  # running
    b.ca_done(False, 4.0)
    assert ("ca",) not in b.tick(30.0)
    assert ("ca",) in b.tick(70.0)  # tried again
    b.ca_done(True, 71.0)
    assert ("ca",) not in b.tick(500.0)
    # The next connection (the daemon restarted, the device rebooted) checks again.
    b.connected(600.0)
    assert ("ca",) in b.tick(601.0)


def test_no_key_no_ca():
    b = brain(install_ca=True)
    b.connected(0.0)
    assert ("ca",) not in b.state("ssh", True, 1.0)


def test_no_notification_goes_out_without_a_message():
    b = brain()
    outs = b.router({"act": {"greet": {"name": "Kiril"}}}, 0.0) + b.router({"act": {"caption": "Hi"}}, 0.0)
    assert all(o[2]["message"] for o in calls(outs, "notify"))


def test_being_placed_takes_the_sign_in_code_down():
    b = brain()
    b.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    b.state("person", True, 1.0)
    assert calls(b.router({"face_result": {"person": None}}, 2.0), "notify_picture")
    # The phone scanned it: the router places them, and the code goes at once.
    (thanks,) = calls(b.router({"here": [{"person": "akadmin", "name": "Kiril", "confidence": 0.98}]}, 20.0), "notify")
    assert thanks[2]["message"] == "Thanks, Kiril!"
    # Only once, and not after the code's time is up anyway.
    assert not calls(b.router({"here": [{"person": "akadmin", "name": "Kiril"}]}, 21.0), "notify")


def test_a_greeting_replaces_the_code_without_a_thanks_too():
    b = brain()
    b.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    b.state("person", True, 1.0)
    b.router({"face_result": {"person": None}}, 2.0)
    outs = b.router({"here": [{"person": "akadmin", "name": "Kiril"}], "act": {"greet": {"name": "Kiril"}}}, 20.0)
    assert [o[2]["message"] for o in calls(outs, "notify")] == ["Hi Kiril!"]


def test_no_thanks_once_the_code_has_gone():
    b = brain()
    b.router({"tap": {"url": "https://who.example.org/tap/Kitchen?n=abc"}}, 0.0)
    b.state("person", True, 1.0)
    b.router({"face_result": {"person": None}}, 2.0)
    assert not calls(b.router({"here": [{"person": "akadmin", "name": "Kiril"}]}, 200.0), "notify")


def test_someone_still_in_front_is_looked_at_again_when_the_router_loses_them():
    b = brain()
    b.state("person", True, 0.0)
    b.router({"here": [{"person": "akadmin", "name": "Kiril"}]}, 5.0)
    assert ("image", "probe") not in b.tick(100.0)  # named: no frames
    # The router restarted: nobody is placed, but the person never left.
    b.router({"here": []}, 110.0)
    assert ("image", "probe") in b.tick(110.0)


def test_what_it_decided_is_kept_for_the_log():
    b = brain()
    b.state("person", True, 1.0)
    b.router({"face_result": {"person": "Kiril Example", "confidence": 0.94}}, 2.0)
    b.router({"here": [{"person": "kiril", "name": "Kiril Example", "confidence": 0.9}]}, 3.0)
    b.router({"here": [{"person": "kiril", "name": "Kiril Example", "confidence": 0.95}]}, 4.0)  # same: not again
    b.router({"here": [{"person": "kiril", "name": "Kiril Example"}, {"person": "maria", "name": "Maria"}]}, 5.0)
    notes = b.take_notes()
    assert {"present": True, "person": True, "screen": None, "dimmed": False} in notes
    assert {"face_result": "Kiril Example", "confidence": 0.94} in notes
    assert [n["here"] for n in notes if "here" in n] == [["kiril"], ["kiril", "maria"]]
    views = [(n["view"], n["for_"]) for n in notes if "view" in n]
    assert views == [("lanbat-screen-kitchen/kiril", "kiril"), ("lanbat-screen-kitchen", "household")]
    assert b.take_notes() == []


def test_the_screen_going_off_and_on_is_kept_for_the_log():
    b = brain(screen_off_after=60.0)
    b.state("screen", True, 0.0)
    b.tick(0.0)
    b.tick(61.0)
    b.state("person", True, 70.0)
    b.tick(71.0)
    notes = [n for n in b.take_notes() if "screen" in n and "why" in n]
    assert [n["screen"] for n in notes] == ["off", "on"]


def test_one_known_person_gets_a_fetch_at_once():
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex"})
    outs = b.router({"here": [{"person": "alex", "name": "Alex"}]}, 0.0)
    (kind, who, prof, gen) = fetches(outs)[0]
    assert kind == "fetch" and who == "alex" and prof == b.profile()[1] and gen == 0
    who, prof = b.wants_items()
    assert who == "alex" and prof.todo == "todo.alex_todo" and prof.reminders == "todo.alex_reminders"


def test_two_people_get_no_items_and_the_sensors_are_cleared():
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex", "maria": "Maria"})
    b.router({"here": [{"person": "alex", "name": "Alex"}]}, 0.0)
    outs = b.router({"here": [{"person": "alex"}, {"person": "maria"}]}, 1.0)
    assert EMPTY_ITEMS in to_router(outs)
    assert not fetches(outs)


def test_leaving_clears_items():
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex"})
    b.router({"here": [{"person": "alex", "name": "Alex"}]}, 0.0)
    outs = b.router({"here": []}, 5.0)
    assert EMPTY_ITEMS in to_router(outs)


def test_lone_person_leaving_fetches_household_items():
    household = Profile(calendars=["calendar.family"])
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex"}, household=household)
    b.router({"here": [{"person": "alex", "name": "Alex"}]}, 0.0)
    outs = b.router({"here": []}, 5.0)
    assert EMPTY_ITEMS in to_router(outs)
    assert fetches(outs)[0][:3] == ("fetch", None, household)


def test_items_are_refreshed_every_minute_while_alone():
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex"})
    b.router({"here": [{"person": "alex", "name": "Alex"}]}, 100.0)
    assert not fetches(b.tick(100.0 + ITEMS_REFRESH_S - 1))
    assert fetches(b.tick(100.0 + ITEMS_REFRESH_S))[0][:3] == ("fetch", "alex", b.profile()[1])


def test_items_message_shape():
    tz = ZoneInfo("UTC")
    due = datetime(2026, 10, 21, 9, 0, tzinfo=tz)
    start = datetime(2026, 10, 21, 14, 0, tzinfo=tz)
    data = {
        "reminders": [Item("r1", "Pay bill", due, "todo.alex_reminders")],
        "todos": [Item("t1", "Buy milk", None, "todo.alex_todo")],
        "more": 2,
        "events": [Event("Standup", start, False)],
    }
    b = brain(people={"alex": "Alex"})
    (msg,) = to_router(b.items(0.0, data, for_person="alex"))
    assert msg == {
        "items": {
            "person": "alex",
            "reminders": [{"uid": "r1", "title": "Pay bill", "due": due.isoformat(), "list": "todo.alex_reminders"}],
            "todos": [{"title": "Buy milk"}],
            "more": 2,
            "events": [{"title": "Standup", "start": start.isoformat(), "all_day": False}],
        }
    }


def reminder_data(*due_times):
    return {"reminders": [Item(f"r{i}", f"Call {i}", due, "todo.alex_reminders") for i, due in enumerate(due_times)],
            "todos": [], "more": 0, "events": []}


def rings(outs):
    return [o for o in outs if o[0] == "call" and o[1] == "notify" and o[2]["title"] == "Reminder"]


def test_a_reminder_chimes_once_when_it_comes_due_with_its_person_alone():
    tz = ZoneInfo("UTC")
    clock = {"now": datetime(2026, 10, 21, 16, 59, 0, tzinfo=tz)}
    b = brain(people={"alex": "Alex"})
    b.wall = lambda: clock["now"]
    b.router({"here": [{"person": "alex", "name": "Alex"}]}, 0.0)
    due = datetime(2026, 10, 21, 17, 0, tzinfo=tz)
    assert not rings(b.items(1.0, reminder_data(due), for_person="alex"))  # not yet
    assert not rings(b.tick(30.0))
    clock["now"] = datetime(2026, 10, 21, 17, 0, 1, tzinfo=tz)
    (ring,) = rings(b.tick(31.0))
    assert ring[2]["message"] == "Call 0" and ring[2]["chime"] == "on" and ring[2]["speak"] == ""
    assert ring[2]["seconds"] > 0 and ring[2]["tag"] != "lanbat"  # not the greeting's, nor the sign-in code's
    assert not rings(b.tick(32.0))  # once
    # The same reminder read again a minute later does not ring again, until it is gone and back.
    assert not rings(b.items(90.0, reminder_data(due), for_person="alex"))
    b.items(120.0, reminder_data(), for_person="alex")
    assert rings(b.items(150.0, reminder_data(due), for_person="alex"))


def test_what_was_rung_is_kept_across_a_restart_and_forgotten_when_done():
    tz = ZoneInfo("UTC")
    due = datetime(2026, 10, 21, 17, 0, tzinfo=tz)
    b = brain(people={"alex": "Alex"})
    b.wall = lambda: datetime(2026, 10, 21, 17, 30, tzinfo=tz)
    b.router({"here": [{"person": "alex"}]}, 0.0)
    assert len(rings(b.items(1.0, reminder_data(due), for_person="alex"))) == 1
    assert b.rung_changed and b.rung() == [["r0", due.isoformat()]]
    again = brain(people={"alex": "Alex"})  # the bridge restarts
    again.wall = b.wall
    again.load_rung(b.rung())
    again.router({"here": [{"person": "alex"}]}, 0.0)
    assert not rings(again.items(1.0, reminder_data(due), for_person="alex"))
    # The household's read says nothing of Alex's reminders: it forgets nothing.
    again.rung_changed = False
    again.items(2.0, {"reminders": [], "todos": [], "more": 0, "events": []}, for_person=None)
    assert again.rung() == [["r0", due.isoformat()]] and not again.rung_changed
    # Marked done: forgotten.
    again.items(3.0, reminder_data(), for_person="alex")
    assert again.rung() == [] and again.rung_changed
    again.load_rung([["x"], "junk", ["a", "b"]])  # a damaged file is read as far as it makes sense
    assert again.rung() == [["a", "b"]]


def test_a_reminder_that_is_overdue_when_read_rings_at_once():
    tz = ZoneInfo("UTC")
    b = brain(people={"alex": "Alex"})
    b.wall = lambda: datetime(2026, 10, 21, 17, 30, tzinfo=tz)
    b.router({"here": [{"person": "alex"}]}, 0.0)
    got = b.items(1.0, reminder_data(datetime(2026, 10, 21, 17, 0, tzinfo=tz)), for_person="alex")
    assert len(rings(got)) == 1


def test_a_reminder_never_chimes_for_a_bystander_or_with_nobody_there():
    tz = ZoneInfo("UTC")
    b = brain(people={"alex": "Alex", "sam": "Sam"})
    b.wall = lambda: datetime(2026, 10, 21, 17, 30, tzinfo=tz)
    b.router({"here": [{"person": "alex"}]}, 0.0)
    due = datetime(2026, 10, 21, 17, 0, tzinfo=tz)
    b._remember_due("alex", reminder_data(due)["reminders"])  # pylint: disable=protected-access
    b.router({"here": [{"person": "alex"}, {"person": "sam"}]}, 1.0)
    assert not rings(b.tick(2.0))
    b.router({"here": [{"person": "sam"}]}, 3.0)  # someone else alone
    assert not rings(b.tick(4.0))
    b.router({"here": []}, 5.0)
    assert not rings(b.tick(6.0))
    b.router({"here": [{"person": "alex"}]}, 7.0)  # Alex alone again: it waited for them
    assert len(rings(b.tick(8.0))) == 1


def test_items_message_names_the_person():
    b = brain(people={"alex": "Alex"})
    (msg,) = to_router(b.items(0.0, {"reminders": [], "todos": [], "more": 0, "events": []}, for_person="alex"))
    assert msg["items"]["person"] == "alex"
    (clear,) = to_router(b.items(1.0, None, for_person=None))
    assert clear["items"]["person"] is None


def test_household_fetches_events_only():
    household = Profile(calendars=["calendar.family"])
    b = brain(ha_url="https://ha.example.org", ha_token="tok", household=household)
    outs = b.router({"here": []}, 0.0)
    assert fetches(outs)[0][:3] == ("fetch", None, household)
    assert b.profile()[1].todo == "" and b.profile()[1].reminders == ""


def test_a_failed_read_still_sends_events():
    tz = ZoneInfo("UTC")
    start = datetime(2026, 10, 21, 14, 0, tzinfo=tz)
    b = brain()
    data = {"reminders": [], "todos": [], "more": 0, "events": [Event("Standup", start, False)]}
    assert to_router(b.items(0.0, data, for_person="alex")) == [{
        "items": {
            "person": "alex",
            "reminders": [],
            "todos": [],
            "more": 0,
            "events": [{"title": "Standup", "start": start.isoformat(), "all_day": False}],
        }
    }]
    assert to_router(b.items(1.0, data, for_person="alex")) == []
    assert b.merge_fetch(None, None, None) is None


def test_stale_fetch_after_a_second_person_arrives():
    tz = ZoneInfo("UTC")
    start = datetime(2026, 10, 21, 14, 0, tzinfo=tz)
    ev = {"calendar.a": {"events": [{"summary": "Standup", "start": start.isoformat(), "end": start.isoformat()}]}}
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex", "maria": "Maria"},
              household=Profile(calendars=["calendar.a"]))
    gen = fetches(b.router({"here": [{"person": "alex"}]}, 0.0))[0][3]
    b.router({"here": [{"person": "alex"}, {"person": "maria"}]}, 1.0)
    assert to_router(b.apply_fetch("alex", gen, None, None, ev, now=start, mono=2.0)) == []


def test_stale_fetch_after_the_person_leaves():
    tz = ZoneInfo("UTC")
    start = datetime(2026, 10, 21, 14, 0, tzinfo=tz)
    ev = {"calendar.a": {"events": [{"summary": "Standup", "start": start.isoformat(), "end": start.isoformat()}]}}
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex"},
              household=Profile(calendars=["calendar.a"]))
    gen = fetches(b.router({"here": [{"person": "alex"}]}, 0.0))[0][3]
    b.router({"here": []}, 5.0)
    assert to_router(b.apply_fetch("alex", gen, None, None, ev, now=start, mono=6.0)) == []


def test_router_reconnect_fetches_items_at_once():
    b = brain(ha_url="https://ha.example.org", ha_token="tok", people={"alex": "Alex"})
    b.router({"here": [{"person": "alex"}]}, 0.0)
    tz = ZoneInfo("UTC")
    data = {"reminders": [], "todos": [], "more": 0,
            "events": [Event("Standup", datetime(2026, 10, 21, 14, 0, tzinfo=tz), False)]}
    to_router(b.items(1.0, data, for_person="alex"))
    assert to_router(b.items(2.0, data, for_person="alex")) == []
    assert fetches(b.router_connected(10.0))[0][:3] == ("fetch", "alex", b.profile()[1])


def test_items_are_not_fetched_while_the_screen_is_off():
    household = Profile(calendars=["calendar.family"])
    b = brain(ha_url="https://ha.example.org", ha_token="tok", household=household)
    b.router({"here": []}, 0.0)
    b._dimmed = True
    assert not fetches(b.tick(ITEMS_REFRESH_S))
    b._dimmed = False
    b.state("screen", False, 1.0)
    assert not fetches(b.tick(ITEMS_REFRESH_S + 1))
