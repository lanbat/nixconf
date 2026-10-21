import os
import stat

from assistant_router.sightings import MAX_PICTURE, Sightings


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def store(tmp_path, pictures=True):
    d = tmp_path / "workload"
    if pictures:
        d.mkdir()
    clock = Clock()
    return Sightings(str(tmp_path / "sightings.db"), str(d), days=30, picture_days=7, clock=clock), clock, d


def test_a_sighting_is_kept_with_its_picture(tmp_path):
    s, clock, d = store(tmp_path)
    first = s.record("Bedroom 1", "akadmin", "robot", 0.8234, jpeg=b"\xff\xd8kiril")
    s.record("Kitchen", None, "screen", None, jpeg=b"\xff\xd8someone")
    clock.t += 60
    s.record("Driveway", "akadmin", "camera", 0.7, event="1792518459.451025-ab12cd")
    found = s.search()
    assert [f["place"] for f in found] == ["Driveway", "Kitchen", "Bedroom 1"]  # newest first
    assert found[2] == {**found[2], "person": "akadmin", "source": "robot", "confidence": 0.823, "picture": True,
                        "event": None}
    assert found[0]["event"] == "1792518459.451025-ab12cd" and not found[0]["picture"]
    assert s.picture(first) == b"\xff\xd8kiril"
    assert len(os.listdir(d)) == 2
    assert stat.S_IMODE(os.stat(tmp_path / "sightings.db").st_mode) == 0o600


def test_searching_by_person_place_and_time(tmp_path):
    s, clock, _ = store(tmp_path)
    s.record("Bedroom 1", "akadmin", "robot", 0.8)
    clock.t += 3 * 86400
    s.record("Kitchen", None, "screen", None)
    s.record("Kitchen", "maria", "screen", 0.9)
    assert [f["person"] for f in s.search(person="akadmin")] == ["akadmin"]
    assert [f["person"] for f in s.search(person="")] == [None]  # nobody known
    assert {f["person"] for f in s.search(place="Kitchen")} == {None, "maria"}
    assert len(s.search(days=1)) == 2
    assert s.places() == ["Bedroom 1", "Kitchen"]


def test_without_the_workload_layer_no_picture_is_kept(tmp_path):
    s, _, d = store(tmp_path, pictures=False)
    sid = s.record("Bedroom 1", "akadmin", "robot", 0.8, jpeg=b"\xff\xd8kiril")
    assert sid and s.search()[0]["picture"] is False and s.picture(sid) is None
    assert not d.exists()


def test_pictures_go_after_a_week_and_sightings_after_a_month(tmp_path):
    s, clock, d = store(tmp_path)
    old = s.record("Bedroom 1", "akadmin", "robot", 0.8, jpeg=b"\xff\xd8old")
    clock.t += 8 * 86400
    new = s.record("Bedroom 1", "akadmin", "robot", 0.8, jpeg=b"\xff\xd8new")
    s.prune()
    assert s.picture(old) is None and s.picture(new) == b"\xff\xd8new" and len(os.listdir(d)) == 1
    assert len(s.search()) == 2
    clock.t += 31 * 86400
    s.prune()
    assert s.search() == [] and os.listdir(d) == []


def test_forgetting_and_linking_someone(tmp_path):
    s, _, d = store(tmp_path)
    s.record("Kitchen", "jaine", "robot", 0.8, jpeg=b"\xff\xd8j")
    s.record("Kitchen", "maria", "robot", 0.8, jpeg=b"\xff\xd8m")
    assert s.rekey("jaine", "akadmin") == 1
    assert {f["person"] for f in s.search()} == {"akadmin", "maria"}
    assert s.forget("maria") == 1 and [f["person"] for f in s.search()] == ["akadmin"]
    assert len(os.listdir(d)) == 1


def test_what_is_never_kept(tmp_path):
    s, _, d = store(tmp_path)
    assert s.record("Kitchen", "maria", "elsewhere", 0.5) == 0  # not a source
    assert s.record("", "maria", "robot", 0.5) == 0
    big = s.record("Kitchen", "maria", "robot", 0.5, jpeg=b"x" * (MAX_PICTURE + 1))
    odd = s.record("Driveway", "maria", "camera", 0.5, event="../../etc/passwd")
    assert s.picture(big) is None and s.event(odd) is None and os.listdir(d) == []
