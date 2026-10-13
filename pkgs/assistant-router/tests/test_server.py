import base64
import json

import pytest
from aiohttp import web
from assistant_router.body import Bodies
from assistant_router.server import Config, make_app
from assistant_router.state import State

CTX = """LANBAT-CONTEXT v1
room: Bedroom 1
device: dev1
time: t
entities:
switch.office_light|Office light|Office|
media_player.kodi_tv|Bedroom 1 TV|Bedroom 1|
end
"""


def req(text, extra=None, conv="c1", history=None):
    msgs = [{"role": "system", "content": CTX}] + (history or []) + [{"role": "user", "content": text}] + (extra or [])
    return {"model": "assistant", "messages": msgs, "user": conv, "tools": [], "temperature": 0.5, "top_p": 1.0}


@pytest.fixture
async def fakes(aiohttp_server):
    seen = {"local": [], "cloud": []}
    local_line = {"v": "act 1 off"}
    cloud_reply = {"v": {"role": "assistant", "content": "It's the capital of the sea."}, "fail": False}

    async def local(request):
        seen["local"].append(await request.json())
        return web.json_response({"choices": [{"message": {"content": local_line["v"]}}]})

    async def cloud(request):
        body = await request.json()
        seen["cloud"].append(body)
        if cloud_reply["fail"]:
            return web.json_response({"error": "down"}, status=503)
        return web.json_response({"choices": [{"index": 0, "finish_reason": "stop", "message": cloud_reply["v"]}]})

    app = web.Application()
    app.router.add_post("/local/v1/chat/completions", local)
    app.router.add_post("/cloud/v1/chat/completions", cloud)
    srv = await aiohttp_server(app)
    base = f"http://{srv.host}:{srv.port}"
    return seen, local_line, cloud_reply, base


async def client_for(aiohttp_client, base, mode="local-first"):
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode=mode)
    return await aiohttp_client(make_app(cfg, State()))


async def post(client, body):
    r = await client.post("/v1/chat/completions", json=body)
    assert r.status == 200
    return (await r.json())["choices"][0]


async def test_local_act_returns_tool_call_then_short_reply(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    c = await client_for(aiohttp_client, base)
    choice = await post(c, req("Turn off the office light."))
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "control_device"
    tool_round = [choice["message"], {"role": "tool", "tool_call_id": call["id"], "name": "control_device", "content": "Success"}]
    final = await post(c, req("Turn off the office light.", tool_round))
    assert final["message"]["content"] == "Office light off."
    assert seen["cloud"] == []


async def test_gate_reject_never_reaches_a_model(aiohttp_client, fakes):
    seen, _, _, base = fakes
    c = await client_for(aiohttp_client, base)
    assert (await post(c, req("Turn on, bye.")))["message"]["content"] == "Sorry, I didn't catch that."
    assert seen["local"] == [] and seen["cloud"] == []


async def test_echo_of_last_reply_rejected(aiohttp_client, fakes):
    seen, _, cloud_reply, base = fakes
    cloud_reply["v"] = {"role": "assistant", "content": "I can't play and stop the radio. Let me know if you need anything else."}
    c = await client_for(aiohttp_client, base)
    await post(c, req("Tell me about radios", conv="a"))
    choice = await post(c, req("I can't play and stop the radios. Let me know if you need anything else.", conv="b"))
    assert choice["message"]["content"] == "Sorry, I didn't catch that."


async def test_escalation_proxies_cloud_and_marks_tier(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    c = await client_for(aiohttp_client, base)
    choice = await post(c, req("Is octopus blood blue"))
    assert choice["message"]["content"] == "It's the capital of the sea."
    assert seen["cloud"][-1]["model"] == "smart"


async def test_tool_round_stays_on_cloud(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    cloud_reply["v"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "control_device",
                                                      "arguments": '{"entity_id": "switch.office_light", "action": "turn_off"}'}}]}
    c = await client_for(aiohttp_client, base)
    first = await post(c, req("Tell me a joke and turn the light off"))
    assert first["finish_reason"] == "tool_calls"
    cloud_reply["v"] = {"role": "assistant", "content": "Turned the office light off."}
    n_local = len(seen["local"])
    final = await post(c, req("Tell me a joke and turn the light off",
                              [first["message"], {"role": "tool", "tool_call_id": "x", "name": "control_device", "content": "Success"}]))
    assert final["message"]["content"] == "Turned the office light off."
    assert len(seen["local"]) == n_local


async def test_escalation_failure_is_honest(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["fail"] = True
    c = await client_for(aiohttp_client, base)
    assert (await post(c, req("Tell me a joke")))["message"]["content"] == "I can't reach the online assistant right now."


async def test_unbacked_cloud_claim_is_replaced(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = {"role": "assistant", "content": "Okay, it's on."}
    c = await client_for(aiohttp_client, base)
    assert (await post(c, req("Make it cosy")))["message"]["content"] == "I didn't change anything."


async def test_local_only_mode_never_calls_cloud(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    c = await client_for(aiohttp_client, base, mode="local-only")
    choice = await post(c, req("Tell me a joke"))
    assert choice["message"]["content"].startswith("I can't do that offline.")
    assert seen["cloud"] == []


async def test_cloud_first_skips_local(aiohttp_client, fakes):
    seen, _, _, base = fakes
    c = await client_for(aiohttp_client, base, mode="cloud-first")
    await post(c, req("Turn off the office light."))
    assert seen["local"] == [] and len(seen["cloud"]) == 1


async def test_stop_is_acknowledged_without_models(aiohttp_client, fakes):
    seen, _, _, base = fakes
    c = await client_for(aiohttp_client, base)
    assert (await post(c, req("Stop.")))["message"]["content"] == "Okay."
    assert seen["local"] == []


async def test_without_context_block_everything_goes_to_cloud(aiohttp_client, fakes):
    seen, _, _, base = fakes
    c = await client_for(aiohttp_client, base)
    body = {"model": "assistant", "messages": [{"role": "user", "content": "hello there friend"}], "user": "z"}
    await post(c, body)
    assert len(seen["cloud"]) == 1


async def test_rejections_do_not_ask_a_question(aiohttp_client, fakes):
    # A question keeps the microphone open (Home Assistant's continue_conversation).
    seen, _, _, base = fakes
    c = await client_for(aiohttp_client, base)
    assert not (await post(c, req("Done.")))["message"]["content"].endswith("?")


async def test_knowledge_answer_with_action_words_reaches_the_user(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = {"role": "assistant", "content": "World War Two started in 1939."}
    c = await client_for(aiohttp_client, base)
    assert (await post(c, req("When did the war start")))["message"]["content"] == "World War Two started in 1939."


async def test_cloud_gets_no_sampling_parameters(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    c = await client_for(aiohttp_client, base)
    await post(c, req("Tell me a joke"))
    sent = seen["cloud"][-1]
    assert "top_p" not in sent and "temperature" not in sent and "user" not in sent


@pytest.mark.parametrize("call", [
    {"name": "control_device", "arguments": '{"entity_id": "cover.garage", "action": "open"}'},
    {"name": "control_device", "arguments": '{"entity_id": "switch.office_light", "action": "restart"}'},
    {"name": "shell", "arguments": "{}"},
    {"name": "media_control", "arguments": "not json"},
])
async def test_cloud_tool_calls_outside_the_house_are_refused(aiohttp_client, fakes, call):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "x", "type": "function", "function": call}]}
    c = await client_for(aiohttp_client, base)
    choice = await post(c, req("Make it cosy"))
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "I can't do that."


async def test_a_valid_cloud_tool_call_passes(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = {"role": "assistant", "content": None, "tool_calls": [{"id": "x", "type": "function",
        "function": {"name": "media_control", "arguments": '{"entity_id": "media_player.kodi_tv", "action": "volume_set", "value": 20}'}}]}
    c = await client_for(aiohttp_client, base)
    assert (await post(c, req("Make it quieter in here")))["finish_reason"] == "tool_calls"


async def test_answer_to_clarify_is_read_with_the_request(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    c = await client_for(aiohttp_client, base)
    history = [{"role": "user", "content": "Turn off the light"},
               {"role": "assistant", "content": "Which one do you mean?"}]
    await post(c, req("the office one", history=history))
    asked = seen["local"][-1]["messages"][-1]["content"]
    assert "Turn off the light" in asked and "the office one" in asked


async def test_stale_local_act_does_not_answer_a_cloud_round(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    c = await client_for(aiohttp_client, base)
    first = await post(c, req("Turn off the office light."))  # local act; HA's tool then fails, no result round
    assert first["finish_reason"] == "tool_calls"
    cloud_reply["v"] = {"role": "assistant", "content": "Here is your joke."}
    later = await post(c, req("Tell me a joke", [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "cloud1", "type": "function",
                                                                 "function": {"name": "control_device", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "cloud1", "name": "control_device", "content": "Success"}]))
    assert later["message"]["content"] == "Here is your joke."


async def test_request_log_rotates_daily_and_keeps_n_days(aiohttp_client, fakes, tmp_path):
    import datetime
    from assistant_router.server import Config, make_app
    from assistant_router.state import State
    seen, _, _, base = fakes
    old = tmp_path / "requests-2020-01-01.jsonl"
    old.write_text("{}\n")
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="m",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="local-first",
                 log_dir=str(tmp_path), log_days=14, log_text=False)
    c = await aiohttp_client(make_app(cfg, State()))
    await post(c, req("Done."))
    today = tmp_path / f"requests-{datetime.date.today().isoformat()}.jsonl"
    assert today.exists() and not old.exists()
    entry = json.loads(today.read_text().splitlines()[-1])
    assert "text" not in entry and entry["route"] == "reject"


def test_cli_takes_the_local_time_limit():
    from assistant_router import __main__ as cli
    cfg, _ = cli.config(["--local-url", "l", "--local-model", "m", "--cloud-url", "c", "--local-timeout", "2.5"])
    assert cfg.local_timeout == 2.5


def test_cli_takes_the_body_listener():
    from assistant_router import __main__ as cli
    _, a = cli.config(["--local-url", "l", "--local-model", "m", "--cloud-url", "c",
                       "--body-host", "0.0.0.0", "--body-port", "8770", "--persona-file", "p.txt"])
    assert (a.body_host, a.body_port, a.persona_file) == ("0.0.0.0", 8770, "p.txt")
    _, a = cli.config(["--local-url", "l", "--local-model", "m", "--cloud-url", "c"])
    assert a.body_port == 0  # no bodies unless asked


async def test_a_local_act_the_words_dont_back_goes_to_the_cloud(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "act 0 on"  # the model's answer to "Pause." on the server
    c = await client_for(aiohttp_client, base)
    choice = await post(c, req("Pause."))
    assert choice["finish_reason"] == "stop" and len(seen["cloud"]) == 1


# ── a body in the room (assistant_router/body.py) ─────────────────────────────
from assistant_router.body import Bodies  # noqa: E402
from assistant_router.server import make_body_app  # noqa: E402


class Sock:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


async def body_client(aiohttp_client, base, room="Bedroom 1"):
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="local-first")
    bodies = Bodies("You are Nabu, a little robot.")
    sock = Sock()
    if room:
        bodies.connect(room, sock)
    return await aiohttp_client(make_app(cfg, State(), bodies=bodies)), bodies, sock


async def test_body_command_is_answered_here(aiohttp_client, fakes):
    seen, _, _, base = fakes
    c, _, sock = await body_client(aiohttp_client, base)
    choice = await post(c, req("Do a little dance!"))
    assert choice["message"]["content"] == "Here I go!"
    assert sock.sent == [{"act": {"mood": "excited", "gesture": "dance"}}]
    assert seen["local"] == [] and seen["cloud"] == []


async def test_body_command_without_a_body_takes_the_normal_path(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    c, _, sock = await body_client(aiohttp_client, base, room="Kitchen")
    await post(c, req("Do a little dance!"))
    assert sock.sent == []
    assert len(seen["cloud"]) == 1


async def test_cloud_gets_the_persona_and_its_tag_drives_the_body(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = {"role": "assistant", "content": "[excited nod] Good morning! What a lovely day."}
    c, _, sock = await body_client(aiohttp_client, base)
    choice = await post(c, req("Good morning"))
    assert choice["message"]["content"] == "Good morning! What a lovely day."
    assert sock.sent == [{"act": {"mood": "excited", "gesture": "nod"}}]
    system = seen["cloud"][-1]["messages"][0]["content"]
    assert system.startswith(CTX)  # Home Assistant's block untouched, the body's after it
    assert "You are Nabu" in system and "Nobody is in front of you" in system


async def test_without_a_body_the_cloud_request_is_unchanged(aiohttp_client, fakes):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    c, _, _ = await body_client(aiohttp_client, base, room="Kitchen")
    await post(c, req("Good morning"))
    assert seen["cloud"][-1]["messages"][0]["content"] == CTX


async def test_a_local_act_makes_the_body_nod(aiohttp_client, fakes):
    _, _, _, base = fakes
    c, _, sock = await body_client(aiohttp_client, base)
    choice = await post(c, req("Turn off the office light."))
    call = choice["message"]["tool_calls"][0]
    await post(c, req("Turn off the office light.", [choice["message"], {
        "role": "tool", "tool_call_id": call["id"], "name": "control_device", "content": "Success"}]))
    assert sock.sent == [{"act": {"mood": "happy", "gesture": "nod"}}]


async def test_a_body_connects_and_reports_over_its_socket(aiohttp_client):
    bodies = Bodies("You are Nabu.")
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 1000.0))
    ws = await c.ws_connect("/v1/body")
    await ws.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await ws.send_json({"state": {"present": True, "present_since_s": 0, "asleep": False}})
    await ws.send_json({"ping": 1})  # anything else is ignored
    for _ in range(50):
        if bodies.has("Kitchen") and bodies.state("Kitchen").present:
            break
        import asyncio
        await asyncio.sleep(0.01)
    assert bodies.state("Kitchen").present
    assert (await ws.receive_json()) == {"body_hello": {"faces": False}}  # the body's capabilities
    assert await bodies.act("Kitchen", {"gesture": "nod"})
    assert (await ws.receive_json()) == {"act": {"gesture": "nod"}}
    await ws.close()


class FakeRecognizer:
    def __init__(self, hit=None):
        self.hit = hit
        self.calls = []

    async def recognize(self, image, filename="probe.jpg"):
        self.calls.append(image)
        return self.hit or {"success": False, "name": None, "confidence": None}


async def test_body_hello_reports_whether_faces_are_recognised(aiohttp_client):
    c = await aiohttp_client(make_body_app(Bodies("You are Nabu."), clock=lambda: 1.0,
                                           frigate=FakeRecognizer()))
    ws = await c.ws_connect("/v1/body")
    await ws.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    assert (await ws.receive_json()) == {"body_hello": {"faces": True}}
    await ws.close()


async def test_a_face_probe_is_recognised_and_answered(aiohttp_client):
    import asyncio
    hit = {"success": True, "name": "kiril", "confidence": 0.91}
    people = People({"kiril": "Kiril"})
    frigate = FakeRecognizer(hit)
    c = await aiohttp_client(make_body_app(Bodies("You are Nabu."), clock=lambda: 100.0,
                                           people=people, frigate=frigate))
    ws = await c.ws_connect("/v1/body")
    await ws.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await ws.receive_json()  # body_hello
    import base64
    await ws.send_json({"face_probe": base64.b64encode(b"\xff\xd8face").decode()})
    result = None
    for _ in range(50):
        result = await ws.receive_json()
        if "face_result" in result:
            break
        await asyncio.sleep(0.01)
    assert result == {"face_result": {"person": "Kiril", "confidence": 0.91}}
    assert frigate.calls == [b"\xff\xd8face"]
    # The recognised speaker is remembered for the room.
    assert "talking to Kiril" in people.line("Kitchen", now=100.0)
    await ws.close()


async def test_a_face_probe_with_no_match_or_no_frigate_is_said_so(aiohttp_client):
    import asyncio, base64
    frigate = FakeRecognizer({"success": False, "name": None, "confidence": None})
    c = await aiohttp_client(make_body_app(Bodies("You are Nabu."), clock=lambda: 1.0,
                                           people=People({}), frigate=frigate))
    ws = await c.ws_connect("/v1/body")
    await ws.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await ws.receive_json()  # body_hello
    await ws.send_json({"face_probe": base64.b64encode(b"stranger").decode()})
    result = None
    for _ in range(50):
        result = await ws.receive_json()
        if "face_result" in result:
            break
        await asyncio.sleep(0.01)
    assert result == {"face_result": {"person": None, "confidence": None}}
    await ws.close()
    # No face library at all: a probe is answered with no person, nothing is recognised.
    c2 = await aiohttp_client(make_body_app(Bodies("You are Nabu."), clock=lambda: 1.0,
                                            people=People({})))
    ws2 = await c2.ws_connect("/v1/body")
    await ws2.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    assert (await ws2.receive_json()) == {"body_hello": {"faces": False}}
    await ws2.close()


# ── who is there (assistant_router/people.py) ─────────────────────────────────
from assistant_router.people import People  # noqa: E402


async def test_the_cloud_hears_who_is_there(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="local-first")
    people = People({"kiril": "Kiril"})
    people.observe("Bedroom 1", "phone", "kiril", 1.0, now=0.0)
    c = await aiohttp_client(make_app(cfg, State(), people=people, clock=lambda: 10.0))
    await post(c, req("Good morning"))
    system = seen["cloud"][-1]["messages"][0]["content"]
    assert system.startswith(CTX)
    assert "Kiril is nearby" in system


async def test_who_is_there_never_changes_a_device_action(aiohttp_client, fakes):
    seen, _, _, base = fakes
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="local-first")
    people = People({"kiril": "Kiril"})
    people.observe("Bedroom 1", "phone", "kiril", 1.0, now=0.0)
    c = await aiohttp_client(make_app(cfg, State(), people=people, clock=lambda: 10.0))
    choice = await post(c, req("Turn off the office light."))
    call = choice["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"entity_id": "switch.office_light", "action": "turn_off"}
    assert "Kiril" not in json.dumps(seen["local"])  # the local model never sees it


async def test_a_robot_reports_faces_and_a_sensor_reports_phones(aiohttp_client):
    import asyncio
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril", "maria": "Maria"})
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=people))
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await robot.send_json({"state": {"present": True, "faces": [{"person": "kiril", "confidence": 0.9},
                                                                  {"person": "nobody", "confidence": 0.9}]}})
    sensor = await c.ws_connect("/v1/body")
    await sensor.send_json({"hello": {"room": "Kitchen", "kind": "room-sensor", "proto": 1}})
    await sensor.send_json({"seen": [{"person": "maria", "rssi": -70}, {"person": "maria", "rssi": "loud"}]})
    for _ in range(50):
        line = people.line("Kitchen", now=100.0)
        if line and "Maria" in line and "Kiril" in line:
            break
        await asyncio.sleep(0.01)
    assert "talking to Kiril" in line and "Maria is nearby" in line
    # A sensor is not a body: no persona, no acts for it.
    assert bodies.has("Kitchen")
    await sensor.close()
    assert bodies.has("Kitchen")
    await robot.close()


async def test_a_face_source_reports_central_faces_without_becoming_a_body(aiohttp_client):
    import asyncio
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril"})
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=people))
    src = await c.ws_connect("/v1/body")
    await src.send_json({"hello": {"room": "Kitchen", "kind": "face-source", "proto": 1}})
    # A name outside the registry is dropped, exactly as for a robot.
    await src.send_json({"faces": [{"person": "kiril", "confidence": 0.9},
                                   {"person": "ghost", "confidence": 0.9}]})
    line = None
    for _ in range(50):
        line = people.line("Kitchen", now=100.0)
        if line and "Kiril" in line:
            break
        await asyncio.sleep(0.01)
    # The cameras say who is present, not who is speaking.
    assert "Kiril is nearby" in line and "talking to" not in line
    # A face source is not a body: no persona, no acts for it.
    assert not bodies.has("Kitchen")
    # It reports the room's full current set, so an empty list clears it.
    await src.send_json({"faces": []})
    for _ in range(50):
        if people.line("Kitchen", now=100.0) is None:
            break
        await asyncio.sleep(0.01)
    assert people.line("Kitchen", now=100.0) is None
    await src.close()


# ── enrolment (assistant_router/frigate.py, people_store.py) ──────────────────
from assistant_router.frigate import FrigateError  # noqa: E402
from assistant_router.people_store import PeopleStore  # noqa: E402


class FakeFrigate:
    def __init__(self, fail=False):
        self.enrolled = []
        self.fail = fail

    async def enroll(self, name, image, filename="capture.jpg"):
        if self.fail:
            raise FrigateError("POST /api/faces/{}/register".format(name))
        self.enrolled.append((name, image, filename))
        return {"success": True}


async def capture_ok(room):
    return b"jpeg"


async def capture_none(room):
    return None


class _BodySocket:
    """A body socket that only records what the router sends."""

    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class _CaptureSocket:
    """A body socket that answers a capture with a face (or None)."""

    def __init__(self, bodies, room, image=b"jpeg"):
        self.bodies = bodies
        self.room = room
        self.image = image
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)
        if data.get("capture"):
            self.bodies.complete_capture(self.room,
                                         base64.b64encode(self.image).decode() if self.image else None)


def _enroll_call(name):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "e1", "type": "function",
         "function": {"name": "enroll_person", "arguments": json.dumps({"name": name})}}]}


async def enroll_client(aiohttp_client, base, people=None, store=None, frigate=None, capture=None, bodies=None):
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="local-first")
    people = people or People({})
    return await aiohttp_client(make_app(cfg, State(), people=people, people_store=store,
                                         frigate=frigate, capture_face=capture, bodies=bodies))


async def test_enroll_registers_a_new_person(aiohttp_client, fakes, tmp_path):
    seen, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Bob")
    people = People({})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    fr = FakeFrigate()
    c = await enroll_client(aiohttp_client, base, people=people, store=store, frigate=fr, capture=capture_ok)
    choice = await post(c, req("Enroll me as Bob"))
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "Okay, I'll remember you as Bob."
    assert fr.enrolled == [("bob", b"jpeg", "capture.jpg")]
    assert people.names == {"bob": "Bob"}
    assert json.loads((tmp_path / "people.json").read_text()) == {"bob": "Bob"}
    assert seen["local"] and seen["cloud"]  # gate -> local(escalate) -> cloud -> router runs it


async def test_enroll_derives_a_key_from_the_name(aiohttp_client, fakes):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Mary Jane")
    fr = FakeFrigate()
    c = await enroll_client(aiohttp_client, base, frigate=fr, capture=capture_ok)
    await post(c, req("Enroll me as Mary Jane"))
    assert fr.enrolled == [("mary_jane", b"jpeg", "capture.jpg")]


async def test_enroll_refuses_a_taken_name(aiohttp_client, fakes, tmp_path):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Bob")
    people = People({"bob": "Bobby"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    fr = FakeFrigate()
    c = await enroll_client(aiohttp_client, base, people=people, store=store, frigate=fr, capture=capture_ok)
    choice = await post(c, req("Enroll me as Bob"))
    assert choice["message"]["content"] == "That name is already taken."
    assert fr.enrolled == []  # Frigate is never told about a taken key


async def test_enroll_reports_when_it_cannot_see(aiohttp_client, fakes):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Bob")
    fr = FakeFrigate()
    c = await enroll_client(aiohttp_client, base, frigate=fr, capture=capture_none)
    choice = await post(c, req("Enroll me as Bob"))
    assert choice["message"]["content"] == "I couldn't get a clear look at your face."
    assert fr.enrolled == []


async def test_enroll_without_frigate_says_it_is_unavailable(aiohttp_client, fakes):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Bob")
    c = await enroll_client(aiohttp_client, base, capture=capture_ok)  # no frigate
    choice = await post(c, req("Enroll me as Bob"))
    assert choice["message"]["content"] == "Face registration isn't available right now."


async def test_enroll_reports_a_failed_save(aiohttp_client, fakes):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Bob")
    fr = FakeFrigate(fail=True)
    people = People({})
    c = await enroll_client(aiohttp_client, base, people=people, frigate=fr, capture=capture_ok)
    choice = await post(c, req("Enroll me as Bob"))
    assert choice["message"]["content"] == "I couldn't save your face right now."
    assert people.names == {}  # not remembered when the save failed


async def test_enroll_without_a_usable_name(aiohttp_client, fakes):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("")
    fr = FakeFrigate()
    c = await enroll_client(aiohttp_client, base, frigate=fr, capture=capture_ok)
    choice = await post(c, req("Enroll me"))
    assert choice["message"]["content"] == "I didn't catch a good name for you."
    assert fr.enrolled == []


async def test_enroll_tool_is_offered_to_the_cloud_when_a_camera_is_wired(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    bodies = Bodies("")
    bodies.connect("Bedroom 1", _BodySocket())
    c = await enroll_client(aiohttp_client, base, frigate=FakeFrigate(), capture=capture_ok, bodies=bodies)
    await post(c, req("Tell me a joke"))
    names = [t["function"]["name"] for t in seen["cloud"][-1].get("tools", [])]
    assert "enroll_person" in names


async def test_enroll_tool_is_not_offered_without_a_camera(aiohttp_client, fakes):
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    c = await client_for(aiohttp_client, base)  # no camera, no frigate
    await post(c, req("Tell me a joke"))
    names = [t["function"]["name"] for t in seen["cloud"][-1].get("tools", [])]
    assert "enroll_person" not in names


async def test_enroll_tool_is_not_offered_when_the_room_has_no_body(aiohttp_client, fakes):
    # Frigate is wired, so a face *could* be registered, but there is no body
    # in the room to capture one: the tool is not offered.
    seen, local_line, _, base = fakes
    local_line["v"] = "escalate"
    c = await enroll_client(aiohttp_client, base, frigate=FakeFrigate(), capture=capture_ok)
    await post(c, req("Tell me a joke"))
    names = [t["function"]["name"] for t in seen["cloud"][-1].get("tools", [])]
    assert "enroll_person" not in names


async def test_enroll_asks_the_body_to_capture_and_saves_the_image(aiohttp_client, fakes):
    _, local_line, cloud_reply, base = fakes
    local_line["v"] = "escalate"
    cloud_reply["v"] = _enroll_call("Bob")
    img = b"\xff\xd8fake-jpeg"
    bodies = Bodies("")
    sock = _CaptureSocket(bodies, "Bedroom 1", img)
    bodies.connect("Bedroom 1", sock)
    fr = FakeFrigate()
    c = await enroll_client(aiohttp_client, base, frigate=fr, capture=bodies.capture, bodies=bodies)
    choice = await post(c, req("Enroll me as Bob"))
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "Okay, I'll remember you as Bob."
    assert sock.sent == [{"capture": True}]               # the router asked the body
    assert fr.enrolled == [("bob", img, "capture.jpg")]    # and Frigate got the face


def test_person_key_from_a_name():
    from assistant_router.server import _person_key
    assert _person_key("Bob") == "bob"
    assert _person_key("Mary Jane") == "mary_jane"
    assert _person_key("Bob22") == "bob22"
    assert _person_key("O'Brien") == "o_brien"
    assert _person_key("  Padded  ") == "padded"
    assert _person_key("!!!") is None
    assert _person_key("") is None


# ── voice (assistant_router/voice.py, voice-id) ───────────────────────────────
class FakeVoiceId:
    """voice-id's API: enrols once it has three utterances from this conversation."""

    def __init__(self, needed=3, available=True):
        self.needed, self.available = needed, available
        self.texts, self.learned, self.forgotten = {}, [], []

    async def enrol(self, key, texts):
        got = self.texts.setdefault(key, [])
        got += [t for t in texts if t not in got]
        enrolled = self.available and len(got) >= self.needed
        return {"enrolled": enrolled, "utterances": len(got), "needed": self.needed, "available": self.available}

    async def learn(self, key, text):
        self.learned.append((key, text))
        return True

    async def forget(self, key):
        self.forgotten.append(key)
        return True


class ForgettingFrigate(FakeFrigate):
    def __init__(self, faces=None):
        super().__init__()
        self.faces = faces or {}
        self.deleted = []

    async def list(self):
        return self.faces

    async def delete(self, name, ids):
        self.deleted.append((name, list(ids)))
        return {"success": True}


def _tool_call(name, args=None):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}


async def voice_client(aiohttp_client, base, people, voice, store=None, frigate=None, bodies=None, capture=None):
    cfg = Config(local_url=f"{base}/local/v1/chat/completions", local_model="qwen3-4b",
                 cloud_url=f"{base}/cloud/v1/chat/completions", cloud_model="smart", mode="cloud-first")
    return await aiohttp_client(make_app(cfg, State(), people=people, people_store=store, frigate=frigate,
                                         bodies=bodies, capture_face=capture, voice=voice))


async def test_a_voice_report_names_the_speaker_for_the_cloud(aiohttp_client, fakes):
    seen, _, _, base = fakes
    people = People({"kiril": "Kiril", "maria": "Maria"})
    c = await voice_client(aiohttp_client, base, people, FakeVoiceId())
    await c.post("/v1/voice", json={"text": "Good morning!", "scores": {"kiril": 0.78, "maria": 0.1}, "t": 0})
    await post(c, req("Good morning"))
    assert "talking to Kiril (recognised by voice)" in seen["cloud"][-1]["messages"][0]["content"]


async def test_a_voice_report_a_moment_late_still_counts(aiohttp_client, fakes):
    import asyncio
    seen, _, _, base = fakes
    people = People({"kiril": "Kiril"})
    c = await voice_client(aiohttp_client, base, people, FakeVoiceId())

    async def late():
        await asyncio.sleep(0.1)
        await c.post("/v1/voice", json={"text": "good morning", "scores": {"kiril": 0.8}})

    task = asyncio.create_task(late())
    await post(c, req("Good morning"))
    await task
    assert "talking to Kiril" in seen["cloud"][-1]["messages"][0]["content"]


async def test_a_bad_voice_report_is_refused(aiohttp_client, fakes):
    _, _, _, base = fakes
    c = await voice_client(aiohttp_client, base, People({"kiril": "Kiril"}), FakeVoiceId())
    assert (await c.post("/v1/voice", json={"text": 3, "scores": "high"})).status == 400


async def test_a_clear_voice_refines_its_own_voiceprint(aiohttp_client, fakes):
    _, _, _, base = fakes
    voice = FakeVoiceId()
    c = await voice_client(aiohttp_client, base, People({"kiril": "Kiril", "maria": "Maria"}), voice)
    await c.post("/v1/voice", json={"text": "what's the weather", "scores": {"kiril": 0.86, "maria": 0.2}})
    await post(c, req("What's the weather"))
    assert voice.learned == [("kiril", "What's the weather")]


async def test_a_middling_voice_teaches_nothing(aiohttp_client, fakes):
    _, _, _, base = fakes
    voice = FakeVoiceId()
    c = await voice_client(aiohttp_client, base, People({"kiril": "Kiril", "maria": "Maria"}), voice)
    await c.post("/v1/voice", json={"text": "hello", "scores": {"kiril": 0.7, "maria": 0.6}})
    await post(c, req("Hello"))
    assert voice.learned == []


async def test_remember_me_by_voice_takes_a_short_conversation(aiohttp_client, fakes, tmp_path):
    seen, _, cloud_reply, base = fakes
    people = People({})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    voice = FakeVoiceId()
    c = await voice_client(aiohttp_client, base, people, voice, store=store)
    # No camera here: the tool is offered for the voice.
    cloud_reply["v"] = _enroll_call("Jane")
    first = await post(c, req("Please remember me, I'm Jane"))
    said = first["message"]["content"]
    assert "Jane" in said and said.endswith("?")      # asks for more, keeping the microphone open
    assert "jane" not in people.names                  # not yet
    second = await post(c, req("I went for a long walk by the river today"))
    assert second["message"]["content"].endswith("?")
    third = await post(c, req("And then I cooked dinner for my family"))
    assert "Jane" in third["message"]["content"] and "voice" in third["message"]["content"]
    assert people.names == {"jane": "Jane"}
    assert voice.texts["jane"][0] == "Please remember me, I'm Jane"
    names = [t["function"]["name"] for t in seen["cloud"][0].get("tools", [])]
    assert "enroll_person" in names and "forget_person" in names


async def test_remember_me_while_the_vault_is_locked(aiohttp_client, fakes):
    _, _, cloud_reply, base = fakes
    people = People({})
    c = await voice_client(aiohttp_client, base, people, FakeVoiceId(available=False))
    cloud_reply["v"] = _enroll_call("Jane")
    said = (await post(c, req("Remember me, I'm Jane")))["message"]["content"]
    assert "can't learn voices right now" in said
    assert people.names == {}


async def test_stop_ends_a_voice_enrolment(aiohttp_client, fakes):
    _, _, cloud_reply, base = fakes
    people = People({})
    voice = FakeVoiceId()
    c = await voice_client(aiohttp_client, base, people, voice)
    cloud_reply["v"] = _enroll_call("Jane")
    await post(c, req("Remember me, I'm Jane"))
    await post(c, req("Stop."))
    cloud_reply["v"] = {"role": "assistant", "content": "It's sunny."}
    assert (await post(c, req("What's the weather")))["message"]["content"] == "It's sunny."


async def test_forget_me_forgets_the_recognised_speaker(aiohttp_client, fakes, tmp_path):
    _, _, cloud_reply, base = fakes
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    store.add("jane", "Jane")
    voice = FakeVoiceId()
    frigate = ForgettingFrigate({"jane": ["a.jpg", "b.jpg"]})
    c = await voice_client(aiohttp_client, base, people, voice, store=store, frigate=frigate)
    await c.post("/v1/voice", json={"text": "forget me please", "scores": {"jane": 0.85, "kiril": 0.1}})
    cloud_reply["v"] = _tool_call("forget_person")
    said = (await post(c, req("Forget me please")))["message"]["content"]
    assert "Jane" in said and "forgotten" in said
    assert voice.forgotten == ["jane"]
    assert frigate.deleted == [("jane", ["a.jpg", "b.jpg"])]
    assert "jane" not in people.names and "kiril" in people.names


async def test_forget_me_keeps_people_from_the_profile(aiohttp_client, fakes, tmp_path):
    _, _, cloud_reply, base = fakes
    people = People({"kiril": "Kiril"})
    store = PeopleStore(people, str(tmp_path / "people.json"))
    voice = FakeVoiceId()
    c = await voice_client(aiohttp_client, base, people, voice, store=store, frigate=ForgettingFrigate())
    await c.post("/v1/voice", json={"text": "forget me", "scores": {"kiril": 0.85}})
    cloud_reply["v"] = _tool_call("forget_person")
    said = (await post(c, req("Forget me")))["message"]["content"]
    assert voice.forgotten == ["kiril"] and "kiril" in people.names
    assert "recordings" in said


async def test_forget_me_needs_to_know_who_you_are(aiohttp_client, fakes):
    _, _, cloud_reply, base = fakes
    voice = FakeVoiceId()
    c = await voice_client(aiohttp_client, base, People({"kiril": "Kiril"}), voice)
    cloud_reply["v"] = _tool_call("forget_person")
    said = (await post(c, req("Forget me")))["message"]["content"]
    assert "not sure who you are" in said and voice.forgotten == []


async def test_a_house_sensor_reports_who_is_home(aiohttp_client):
    import asyncio
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril", "maria": "Maria"})
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=people))
    house = await c.ws_connect("/v1/body")
    await house.send_json({"hello": {"room": "house", "kind": "house-sensor", "proto": 1}})
    await house.send_json({"home": [{"person": "maria"}, {"person": "ghost"}, "kiril"]})
    for _ in range(50):
        if people.home(100.0):
            break
        await asyncio.sleep(0.01)
    assert people.home(100.0) == {"maria"}
    # Not a body: no room gets a persona from it.
    assert not bodies.has("house")
    await house.close()


async def test_a_robots_ear_goes_to_voice_id(aiohttp_client):
    import asyncio

    class FakeVoice:
        def __init__(self):
            self.samples = []

        async def embed(self, text, pcm):
            self.samples.append((text, pcm))
            return True

    voice = FakeVoice()
    bodies = Bodies("You are Nabu.")
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, voice=voice))
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await robot.send_json({"state": {"present": True, "ear": True}})
    await robot.send_json({"voice_sample": {"text": "good morning", "pcm": "AAAA"}})
    await robot.send_json({"voice_sample": {"text": 3, "pcm": "AAAA"}})  # malformed: dropped
    for _ in range(50):
        if voice.samples:
            break
        await asyncio.sleep(0.01)
    assert voice.samples == [("good morning", "AAAA")]
    assert bodies.state("Kitchen").ear
    # A room sensor's sample is not a body's: ignored.
    sensor = await c.ws_connect("/v1/body")
    await sensor.send_json({"hello": {"room": "Kitchen", "kind": "room-sensor", "proto": 1}})
    await sensor.send_json({"voice_sample": {"text": "hi", "pcm": "AAAA"}})
    await asyncio.sleep(0.05)
    assert len(voice.samples) == 1
    await sensor.close()
    await robot.close()


async def test_a_robot_reports_phones_like_a_room_sensor(aiohttp_client):
    import asyncio
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril"})
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=people))
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await robot.send_json({"seen": [{"person": "kiril", "rssi": -70, "via": "ble"}]})
    line = None
    for _ in range(50):
        line = people.line("Kitchen", now=100.0)
        if line:
            break
        await asyncio.sleep(0.01)
    assert "Kiril is nearby" in line
    await robot.close()


async def test_a_persons_card_tapped_on_a_robot_names_them_there(aiohttp_client):
    import asyncio
    bodies, people = Bodies("You are Nabu."), People({"kiril": "Kiril"})
    from assistant_router.cards import Cards
    cards = Cards()
    cards.cards = {"0435249AD11290": "kiril"}
    c = await aiohttp_client(make_body_app(bodies, clock=lambda: 100.0, people=people, cards=cards))
    robot = await c.ws_connect("/v1/body")
    await robot.send_json({"hello": {"room": "Kitchen", "kind": "stackchan", "proto": 1}})
    await robot.send_json({"event": {"nfc": "0435249ad11290"}})
    for _ in range(50):
        if people.assess("Kitchen", 100.0).speaker:
            break
        await asyncio.sleep(0.01)
    assert people.assess("Kitchen", 100.0).speaker == "kiril"
    await robot.close()
