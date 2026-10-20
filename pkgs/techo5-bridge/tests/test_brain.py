import base64

from room_presence import _ah
from techo5_bridge.brain import CAN, PROBE_S, Brain, DeviceConfig, Profile, _manufacturer_data, mac

PEOPLE = {"kiril": "Kiril Example", "maria": "Maria"}


def brain(**extra) -> Brain:
    cfg = DeviceConfig(name="kitchen", room="Kitchen", dashboard="lanbat-screen-kitchen",
                       code_url="http://192.0.2.10:8775/code/x.png", **extra)
    return Brain(cfg, PEOPLE)


def calls(outs, action=None):
    return [o for o in outs if o[0] == "call" and (action is None or o[1] == action)]


def to_router(outs):
    return [o[1] for o in outs if o[0] == "router"]


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
