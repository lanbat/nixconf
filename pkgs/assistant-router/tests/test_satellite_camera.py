"""A satellite's camera as a face source of its own (pkgs/satellite-camera, server.py, body.py)."""
import asyncio
import base64

from assistant_router.body import Bodies
from assistant_router.people import People
from assistant_router.server import make_body_app
from assistant_router.sightings import Sightings

from test_server import FakeRecognizer


async def hello(c, room, kind, **more):
    ws = await c.ws_connect("/v1/body")
    await ws.send_json({"hello": {"room": room, "kind": kind, "proto": 1, **more}})
    return ws


async def probe(ws, picture=b"\xff\xd8eye"):
    await ws.send_json({"face_probe": base64.b64encode(picture).decode(), "why": "seen"})
    for _ in range(50):
        got = await ws.receive_json()
        if "face_result" in got:
            return got["face_result"]


async def test_a_camera_is_not_a_body_and_a_robot_in_its_room_stays(aiohttp_client):
    bodies = Bodies("You are Nabu.")
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=People({}),
                                           frigate=FakeRecognizer()))
    robot = await hello(c, "Kitchen", "stackchan")
    assert "body_hello" in await robot.receive_json()
    eye = await hello(c, "Kitchen", "satellite-camera")
    assert await eye.receive_json() == {"body_hello": {"faces": True}}  # told it may send faces
    assert bodies.has("Kitchen") and bodies.is_robot("Kitchen") and bodies.has_camera("Kitchen")
    await eye.close()
    for _ in range(50):
        if not bodies.has_camera("Kitchen"):
            break
        await asyncio.sleep(0.01)
    assert not bodies.has_camera("Kitchen") and bodies.has("Kitchen")  # the robot is still there
    await robot.close()


async def test_a_camera_alone_makes_no_body(aiohttp_client):
    bodies = Bodies("You are Nabu.")
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=People({}),
                                           frigate=FakeRecognizer()))
    eye = await hello(c, "Study", "satellite-camera")
    await eye.receive_json()
    assert not bodies.has("Study") and bodies.has_camera("Study") and bodies.can_capture("Study")
    assert not bodies.can("Study", "capture") and "Study" not in bodies.rooms()
    await eye.close()


async def test_its_faces_are_recognised_as_seen_and_kept_as_satellite_sightings(aiohttp_client, tmp_path):
    (tmp_path / "pictures").mkdir()
    sightings = Sightings(str(tmp_path / "s.db"), str(tmp_path / "pictures"))
    people = People({"kiril": "Kiril"})
    frigate = FakeRecognizer({"success": True, "name": "kiril", "confidence": 0.9})
    c = await aiohttp_client(make_body_app(Bodies("You are Nabu."), clock=lambda: 100.0, people=people,
                                           frigate=frigate, sightings=sightings))
    eye = await hello(c, "Study", "satellite-camera")
    await eye.receive_json()
    assert await probe(eye) == {"person": "Kiril", "confidence": 0.9}
    (found,) = sightings.search()
    assert (found["place"], found["person"], found["source"], found["picture"]) == ("Study", "kiril", "satellite", True)
    assert list(people.here("Study", 100.0)) == ["kiril"]  # someone is in the room: "seen", not aimed
    assert people.evidence("Study", 100.0).get("robot") and not people.evidence("Study", 100.0).get("aimed")
    await eye.close()


async def test_the_router_can_ask_a_room_with_only_a_camera_for_a_face(aiohttp_client):
    bodies = Bodies("You are Nabu.")
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=People({}),
                                           frigate=FakeRecognizer()))
    eye = await hello(c, "Study", "satellite-camera")
    await eye.receive_json()
    ask = asyncio.create_task(bodies.capture("Study", timeout=2))
    assert await eye.receive_json() == {"capture": True}
    await eye.send_json({"face_image": base64.b64encode(b"face").decode()})
    assert await ask == b"face"
    # A second ask while none is in flight works; a camera that goes away ends one in flight.
    ask = asyncio.create_task(bodies.capture("Study", timeout=2))
    await eye.receive_json()
    await eye.close()
    assert await ask is None
    assert await bodies.capture("Study", timeout=0.1) is None  # no camera now


async def test_a_body_that_can_capture_is_asked_before_the_camera(aiohttp_client):
    bodies = Bodies("You are Nabu.")
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=People({}),
                                           frigate=FakeRecognizer()))
    robot = await hello(c, "Kitchen", "stackchan")
    await robot.receive_json()
    eye = await hello(c, "Kitchen", "satellite-camera")
    await eye.receive_json()
    ask = asyncio.create_task(bodies.capture("Kitchen", timeout=2))
    assert await robot.receive_json() == {"capture": True}
    await robot.send_json({"face_image": base64.b64encode(b"robot").decode()})
    assert await ask == b"robot"
    await eye.close()
    await robot.close()
