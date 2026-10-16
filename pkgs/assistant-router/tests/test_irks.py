import json
import os
import stat

from assistant_router.body import Bodies
from assistant_router.irks import Irks
from assistant_router.people import People
from assistant_router.server import make_body_app

IRK = "ec0234a357c8ad05341010a60a397d9b"


def test_keys_are_kept_privately_and_checked(tmp_path):
    path = str(tmp_path / "irks.json")
    irks = Irks(path)
    assert irks.add("kiril", IRK.upper()) and not irks.add("kiril", IRK)  # once
    assert not irks.add("kiril", "not a key") and not irks.add("", IRK)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Irks(path).all() == {"kiril": [IRK]}
    for i in range(5):
        irks.add("kiril", f"{i:032x}")
    assert len(irks.all()["kiril"]) == 4 and irks.all()["kiril"][-1] == f"{4:032x}"
    assert irks.forget("kiril") and not irks.forget("kiril") and Irks(path).all() == {}


async def test_room_sensors_get_the_keys_on_connecting_and_on_each_change(aiohttp_client, tmp_path):
    import asyncio
    irks = Irks(str(tmp_path / "irks.json"))
    irks.add("kiril", IRK)
    irks.add("stranger", "0" * 32)  # not a person the router knows: not sent
    c = await aiohttp_client(make_body_app(Bodies(""), clock=lambda: 1.0, people=People({"kiril": "Kiril"}),
                                           irks=irks))
    ws = await c.ws_connect("/v1/body")
    await ws.send_json({"hello": {"room": "Office", "kind": "room-sensor", "proto": 1}})
    assert await ws.receive_json() == {"irks": {"kiril": [IRK]}}
    irks.forget("kiril")
    assert await asyncio.wait_for(ws.receive_json(), 2) == {"irks": {}}
    # A robot isn't sent them.
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Office", "kind": "stackchan", "proto": 1}})
    assert "irks" not in await robot.receive_json()
    await ws.close()
    await robot.close()


def test_forgetting_the_phones_forgets_the_keys(tmp_path):
    from assistant_router.tap import Taps
    irks = Irks(str(tmp_path / "irks.json"))
    irks.add("kiril", IRK)
    taps = Taps("https://who.example.org", People({"kiril": "Kiril"}), lambda: 1.0,
                phones_path=str(tmp_path / "phones.json"))
    taps.forgetters.append(irks.forget)
    taps.forget_phones("kiril")
    assert irks.all() == {}
