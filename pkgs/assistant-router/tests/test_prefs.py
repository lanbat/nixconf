import json

from assistant_router.prefs import Prefs, clean


def test_only_well_formed_settings_are_kept():
    assert clean({"calendars": ["calendar.a", "sensor.x", "calendar.a", "calendar/../x"],
                  "reminder_calendar": "todo.a", "todo": "todo.kiril_shopping",
                  "immich_person": "  Kiril   M ", "glance": ["sensor.x"], "other": 1}) == {
        "calendars": ["calendar.a"], "todo": "todo.kiril_shopping", "immich_person": "Kiril M"}
    assert clean("nonsense") == {}
    assert clean({"calendars": "calendar.a"}) == {}


def test_settings_are_kept_and_a_screen_gets_its_share(tmp_path):
    path = tmp_path / "prefs.json"
    told = []
    p = Prefs(str(path))
    p.listeners.append(told.append)
    p.set("kiril", {"calendars": ["calendar.kiril"], "reminder_calendar": "calendar.kiril"})
    assert told == ["kiril"]
    p.set("kiril", {"calendars": ["calendar.kiril"], "reminder_calendar": "calendar.kiril"})
    assert told == ["kiril"]  # unchanged: nobody told
    assert Prefs(str(path)).get("kiril") == {"calendars": ["calendar.kiril"], "reminder_calendar": "calendar.kiril"}
    assert p.for_screen("kiril") == {"calendars": ["calendar.kiril"]}
    assert p.for_screen("maria") == {}
    p.set("kiril", {})
    assert json.loads(path.read_text()) == {}


def test_forgetting_and_linking(tmp_path):
    p = Prefs(str(tmp_path / "prefs.json"))
    p.set("guest_jo", {"todo": "todo.jo"})
    p.set("maria", {"todo": "todo.maria"})
    assert p.rekey("guest_jo", "kiril") and p.get("kiril") == {"todo": "todo.jo"}
    p.set("guest_al", {"todo": "todo.al"})
    assert p.rekey("guest_al", "maria") and p.get("maria") == {"todo": "todo.maria"}  # theirs stays
    assert not p.rekey("nobody", "maria")
    assert p.forget("maria") and not p.forget("maria")
    assert p.get("maria") == {} and p.get("guest_al") == {}
