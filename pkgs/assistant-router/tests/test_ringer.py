import json
from datetime import datetime, timedelta, timezone

from assistant_router.body import Bodies
from assistant_router.people import People
from assistant_router.ringer import RINGS, REPEAT_S, Ringer

TZ = timezone(timedelta(hours=1))


class Sock:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class FakePersonal:
    def __init__(self, rows):
        self.rows = rows  # person -> list of {"uid", "summary", "due"}

    async def items(self, key, which):
        assert which == "reminders"
        return self.rows.get(key)


def setup(rows, tmp_path=None, start=1000.0):
    clock = [start]
    wall = [datetime(2026, 10, 22, 17, 5, tzinfo=TZ)]
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril", "maria": "Maria"})
    sock = Sock()
    bodies.connect("Bedroom 1", sock)  # a robot: it says nothing about what it can do
    bodies.update("Bedroom 1", {"phase": "idle", "present": True}, start)
    people.observe("Bedroom 1", "seen", "kiril", 0.9, start)
    ringer = Ringer(bodies, people, FakePersonal(rows), lambda: clock[0], lambda: wall[0],
                    str(tmp_path / "rung.json") if tmp_path else None)
    return bodies, people, ringer, sock, clock, wall


def shown(sock):
    return [m["act"] for m in sock.sent if "caption" in m.get("act", {})]


DUE = {"kiril": [{"uid": "u1", "summary": "Call mum", "due": "2026-10-22T17:00:00"}]}


async def test_a_due_reminder_is_shown_on_the_robot_with_a_perk_and_the_light():
    _, _, ringer, sock, _, _ = setup(DUE)
    await ringer.tick()
    (act,) = shown(sock)
    assert act["caption"] == "Call mum" and act["mood"] == "happy" and act["gesture"] == "perk"
    assert act["led"] == {"rgb": [255, 170, 0]}


async def test_it_is_shown_again_a_few_times_and_then_left_alone():
    _, people, ringer, sock, clock, _ = setup(DUE)
    for _ in range(RINGS + 3):
        await ringer.tick()
        clock[0] += REPEAT_S
        people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    assert len(shown(sock)) == RINGS
    await ringer.tick()
    await ringer.tick()  # and not before the repeat has passed
    assert len(shown(sock)) == RINGS


async def test_not_before_the_repeat_and_not_when_it_is_not_due_yet():
    rows = {"kiril": [{"uid": "u1", "summary": "Later", "due": "2026-10-22T18:00:00"}]}
    _, _, ringer, sock, clock, _ = setup(rows)
    await ringer.tick()
    assert shown(sock) == []
    _, _, ringer, sock, clock, _ = setup(DUE)
    await ringer.tick()
    clock[0] += REPEAT_S - 1
    await ringer.tick()
    assert len(shown(sock)) == 1


async def test_done_or_removed_stops_it_and_forgets_it():
    rows = {"kiril": [{"uid": "u1", "summary": "Call mum", "due": "2026-10-22T17:00:00"}]}
    _, people, ringer, sock, clock, _ = setup(rows)
    await ringer.tick()
    rows["kiril"] = []
    clock[0] += REPEAT_S
    people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    await ringer.tick()
    assert len(shown(sock)) == 1 and ringer.shown == {}


async def test_never_with_anyone_else_there_and_it_waits_for_them_alone():
    _, people, ringer, sock, clock, _ = setup(DUE)
    people.observe("Bedroom 1", "seen", "maria", 0.9, clock[0])
    await ringer.tick()
    assert shown(sock) == []
    clock[0] += 400  # Maria's evidence has gone; Kiril's is renewed
    people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    await ringer.tick()
    assert len(shown(sock)) == 1


async def test_not_for_someone_not_sure_not_when_asleep_or_in_a_conversation():
    _, people, ringer, sock, clock, _ = setup(DUE)
    bodies = ringer.bodies
    bodies.update("Bedroom 1", {"phase": "listening", "present": True}, clock[0])
    await ringer.tick()
    assert shown(sock) == []
    bodies.update("Bedroom 1", {"phase": "idle", "asleep": True, "present": True}, clock[0])
    await ringer.tick()
    assert shown(sock) == []
    bodies.update("Bedroom 1", {"phase": "idle", "asleep": False, "present": True}, clock[0])
    await ringer.tick()
    assert len(shown(sock)) == 1


async def test_a_screen_is_not_rung_by_the_router_and_home_assistant_down_is_quiet():
    bodies, people, ringer, sock, clock, _ = setup(DUE)
    screen = Sock()
    bodies.connect("Kitchen", screen, "screen", ["caption", "here", "items"])
    bodies.update("Kitchen", {"phase": "idle", "present": True}, clock[0])
    people.observe("Kitchen", "seen", "kiril", 0.9, clock[0])
    await ringer.tick()
    assert shown(screen) == []  # the Echo's own bridge rings that
    ringer.personal = FakePersonal({})  # its items() is None for someone unknown: Home Assistant down
    await ringer.tick()


async def test_what_was_shown_survives_a_restart(tmp_path):
    _, people, ringer, sock, clock, wall = setup(DUE, tmp_path)
    for _ in range(RINGS):
        await ringer.tick()
        clock[0] += REPEAT_S
        people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    assert json.loads((tmp_path / "rung.json").read_text()) == {"kiril u1 2026-10-22T17:00:00+01:00": RINGS}
    again = Ringer(ringer.bodies, people, FakePersonal(DUE), lambda: clock[0], lambda: wall[0],
                   str(tmp_path / "rung.json"))
    n = len(shown(sock))
    await again.tick()
    assert len(shown(sock)) == n  # not started over
