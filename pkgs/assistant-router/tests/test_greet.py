import asyncio

from assistant_router.body import Bodies
from assistant_router.greet import ARRIVAL_S, GREET_AGAIN_S, Greeter, first_name
from assistant_router.people import Assessment, People


class Sock:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def setup(start=1000.0):
    clock = [start]
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril Momchilov", "maria": "Maria"})
    bodies.events = asyncio.Queue()
    sock = Sock()
    bodies.connect("Bedroom 1", sock)
    bodies.update("Bedroom 1", {"phase": "idle"}, start)
    return bodies, people, Greeter(bodies, people, lambda: clock[0]), sock, clock


def greets(sock):
    return [m["act"]["greet"] for m in sock.sent if "greet" in m.get("act", {})]


async def test_someone_arriving_is_greeted_once_by_their_first_name():
    bodies, people, greeter, sock, clock = setup()
    clock[0] += ARRIVAL_S  # the router has been up a while
    people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    await greeter.tick()
    assert greets(sock) == [{"name": "Kiril"}]
    assert bodies.events.get_nowait() == ("Bedroom 1", {"recognized": "Kiril Momchilov", "how": ["robot"]})
    for _ in range(10):  # still there: not again
        clock[0] += 5
        people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
        await greeter.tick()
    assert len(greets(sock)) == 1


async def test_coming_back_is_arriving_but_not_twice_in_half_an_hour():
    _, people, greeter, sock, clock = setup()
    clock[0] += ARRIVAL_S
    people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    await greeter.tick()
    clock[0] += 600  # back after ten minutes away
    people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    await greeter.tick()
    assert len(greets(sock)) == 1
    clock[0] += GREET_AGAIN_S  # and again after a long while
    people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
    await greeter.tick()
    assert len(greets(sock)) == 2


async def test_someone_already_there_when_the_router_starts_is_not_arriving():
    _, people, greeter, sock, clock = setup()
    for _ in range(40):
        people.observe("Bedroom 1", "seen", "kiril", 0.9, clock[0])
        await greeter.tick()
        clock[0] += 5
    assert greets(sock) == []


async def test_a_greeting_waits_for_the_end_of_a_conversation_and_not_at_night():
    bodies, people, greeter, sock, clock = setup()
    clock[0] += ARRIVAL_S
    bodies.update("Bedroom 1", {"phase": "thinking"}, clock[0])
    people.observe("Bedroom 1", "tap", "maria", 1.0, clock[0])
    await greeter.tick()
    assert greets(sock) == []
    clock[0] += 5
    bodies.update("Bedroom 1", {"phase": "idle"}, clock[0])
    await greeter.tick()
    assert greets(sock) == [{"name": "Maria"}]
    bodies.update("Bedroom 1", {"night": True}, clock[0])
    clock[0] += ARRIVAL_S
    people.observe("Bedroom 1", "seen", "kiril", 0.95, clock[0])
    await greeter.tick()
    assert len(greets(sock)) == 1


async def test_someone_who_just_spoke_to_it_is_not_greeted():
    _, people, greeter, sock, clock = setup()
    clock[0] += ARRIVAL_S
    people.remember("Bedroom 1", Assessment(speaker="kiril", posterior=0.95, how="voice"), clock[0])
    people.note_voice("Bedroom 1", {"kiril": 0.9, "maria": 0.1}, clock[0])  # he is here by his voice
    await greeter.tick()
    assert greets(sock) == []


async def test_only_someone_it_is_sure_of_by_one_source():
    _, people, greeter, sock, clock = setup()
    clock[0] += ARRIVAL_S
    people.observe("Bedroom 1", "seen", "kiril", 0.7, clock[0])  # a doubtful match
    await greeter.tick()
    assert greets(sock) == []
    people.observe_phone("Bedroom 1", "maria", -50, clock[0], via="ble")  # her phone in the room
    await greeter.tick()
    assert greets(sock) == [{"name": "Maria"}]


def test_first_names():
    assert first_name("Kiril Momchilov") == "Kiril" and first_name("Maria") == "Maria" and first_name("") == ""
