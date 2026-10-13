"""The router's HTTP side: Home Assistant's agent calls it as its LLM.

A request is one round of the agent's tool loop: the context block, the
conversation so far, and, after a tool call, the tool's results. The first
round of a turn is routed (gate, local triage, cloud); a later round answers
a local act from its result, and goes back to the cloud otherwise.
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import glob
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web

from . import actions, body as body_mod, gate as gate_mod, honesty, triage
from .body import Bodies
from .context import Context, parse_context
from .frigate import Frigate, FrigateError
from .people import People
from .voice import VoiceIdClient, VoiceReports
from .people_store import PeopleStore
from .state import State

LOG = logging.getLogger("assistant-router")

# No question mark: Home Assistant keeps the microphone open after a reply
# that asks one, and a rejection heard back would loop.
SORRY = "Sorry, I didn't catch that."
STOPPED = "Okay."
CLARIFY = "Which one do you mean?"
CLOUD_DOWN = "I can't reach the online assistant right now."
OFFLINE = "I can't do that offline. I can control devices and media."
REFUSED = "I can't do that."

# What a cloud model may ask Home Assistant to run: the agent's two functions,
# with the actions their specs list (setup-ha.sh).
TOOL_ACTIONS = {
    "control_device": set(actions.SERVICE.values()),
    "media_control": set(actions.MEDIA_SERVICE.values()),
}

# A tool the router runs itself, not Home Assistant: it registers the person
# speaking now against Frigate's face library and the runtime people. Offered
# to the cloud model only when a camera can capture a face (the capture is the
# presence check: no face in front, no enrolment).
ENROLL_TOOL = {
    "type": "function",
    "function": {
        "name": "enroll_person",
        "description": ("Register the person speaking right now so the assistant can recognise "
                        "them later. Use only when they ask to be enrolled or remembered by "
                        "name. Call it with the name they want to be called."),
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The name to call the person."}},
            "required": ["name"],
        },
    },
}

# Forget the person speaking now: their voiceprint, their faces, and, if they
# enrolled themselves, their entry. Offered wherever there is something to
# forget (voice-id or Frigate); acts only on a recognised speaker.
FORGET_TOOL = {
    "type": "function",
    "function": {
        "name": "forget_person",
        "description": ("Forget the person speaking right now: their voice, their face and their "
                        "name. Use only when they ask to be forgotten."),
        "parameters": {"type": "object", "properties": {}},
    },
}

SPEAKER_SECONDS = 300.0   # the speaker of a device's conversation, carried this long
ENROL_SECONDS = 120.0     # a voice enrolment waits this long for the next sentence

ENROLL_TAKEN = "That name is already taken."
ENROLL_BAD_NAME = "I didn't catch a good name for you."
ENROLL_NO_FACE = "I couldn't get a clear look at your face."
ENROLL_NO_FRIGATE = "Face registration isn't available right now."
ENROLL_FAILED = "I couldn't save your face right now."


def _person_key(name: str) -> str | None:
    """A person key from a display name, or None when it has nothing to make one of.

    The key is a face-library name (frigate.safe_name): lowercase, letters,
    digits and underscores. Two people must not share a key, so the caller
    checks it is unused before enrolling."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or None


@dataclass
class Config:
    local_url: str
    local_model: str
    cloud_url: str
    cloud_model: str
    mode: str  # local-first | cloud-first | local-only
    local_timeout: float = 4.0
    cloud_timeout: float = 20.0
    log_dir: str | None = None
    log_days: int = 14
    log_text: bool = True


def _reply(text: str) -> dict:
    return {"model": "assistant", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def _tool_reply(calls: list[dict]) -> dict:
    return {"model": "assistant", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": None, "tool_calls": calls}}]}


def _turn(messages: list[dict]) -> tuple[str, str, list[dict]]:
    """The last user text, the request it answers when the assistant had
    asked which device was meant, and the messages after it (this turn's
    rounds)."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            text = messages[i].get("content") or ""
            asked = text
            if i >= 2 and messages[i - 1].get("role") == "assistant" \
                    and messages[i - 1].get("content") == CLARIFY and messages[i - 2].get("role") == "user":
                asked = f"{messages[i - 2].get('content') or ''} ({text})"
            return text, asked, messages[i + 1:]
    return "", "", []


def _allowed(calls: list[dict], ctx: Context | None) -> bool:
    """Every call is one of the agent's functions, on an exposed entity, with
    an action its spec lists."""
    ids = {e.entity_id for e in ctx.entities} if ctx else set()
    for call in calls:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "")
        except (TypeError, ValueError):
            return False
        if not isinstance(args, dict) or fn.get("name") not in TOOL_ACTIONS:
            return False
        if args.get("entity_id") not in ids or args.get("action") not in TOOL_ACTIONS[fn["name"]]:
            return False
    return True


def make_app(cfg: Config, state: State | None = None, clock=time.monotonic,
             bodies: Bodies | None = None, people: People | None = None,
             people_store: PeopleStore | None = None, frigate: Frigate | None = None,
             capture_face: Callable[[str], Awaitable[bytes | None]] | None = None,
             voice: VoiceIdClient | None = None) -> web.Application:
    state = state or State()
    bodies = bodies or Bodies("")
    people = people or People({})
    enrollable = capture_face is not None
    reports = VoiceReports()
    speakers: dict[str, tuple[str, float]] = {}       # device -> (person, when)
    enrolling: dict[str, tuple[str, str, float]] = {}  # device -> (key, name, when)

    def last_speaker(device: str, now: float) -> str | None:
        hit = speakers.get(device)
        return hit[0] if hit and now - hit[1] <= SPEAKER_SECONDS and hit[0] in people.names else None

    def remember(key: str, name: str) -> None:
        if people_store is not None:
            people_store.add(key, name)
        else:
            people.add(key, name)
    log_day = {"v": ""}

    def log(entry: dict) -> None:
        if not cfg.log_text:
            entry.pop("text", None)
        LOG.info(json.dumps(entry))
        if not cfg.log_dir:
            return
        today = datetime.date.today()
        if log_day["v"] != today.isoformat():
            log_day["v"] = today.isoformat()
            oldest = (today - datetime.timedelta(days=cfg.log_days)).isoformat()
            for path in glob.glob(os.path.join(cfg.log_dir, "requests-*.jsonl")):
                if os.path.basename(path)[len("requests-"):-len(".jsonl")] < oldest:
                    os.remove(path)
        with open(os.path.join(cfg.log_dir, f"requests-{today.isoformat()}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    async def cloud(session: aiohttp.ClientSession, body: dict, room: str = "",
                    voice_scores: dict | None = None, last: str | None = None) -> dict | None:
        # Home Assistant always sends temperature and top_p; current Claude
        # models refuse both together, so the provider's defaults apply.
        out = {k: v for k, v in body.items() if k not in ("temperature", "top_p", "user")}
        out["model"] = cfg.cloud_model
        # The enrolment tool is offered only where a body can actually capture
        # a face: Frigate is wired and this room has a body connected.
        # With voice-id, a voice can be enrolled anywhere.
        if (enrollable and bodies.has(room)) or voice is not None:
            out["tools"] = list(out.get("tools") or []) + [ENROLL_TOOL]
        if voice is not None or frigate is not None:
            out["tools"] = list(out.get("tools") or []) + [FORGET_TOOL]
        # A body in the room: its persona and what it senses go after Home
        # Assistant's block, so the start of the prompt stays the same.
        now = clock()
        block = "\n".join(b for b in (bodies.prompt_block(room, now),
                                      people.line(room, now, voice_scores, last)) if b)
        msgs = out.get("messages") or []
        if block and msgs and msgs[0].get("role") == "system":
            out["messages"] = [dict(msgs[0], content=(msgs[0].get("content") or "") + "\n" + block)] + msgs[1:]
        try:
            async with session.post(cfg.cloud_url, json=out,
                                    timeout=aiohttp.ClientTimeout(total=cfg.cloud_timeout)) as r:
                if r.status != 200:
                    return None
                return await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    def finish(device: str, text: str, tool_results: list[str]) -> dict:
        said = honesty.check(honesty.speakable(text), tool_results)
        state.remember_reply(device, said, clock())
        return _reply(said)

    async def _enroll(device: str, ctx: Context | None, call: dict, tool_results: list[str],
                      texts: list[str]) -> dict:
        # Run by the router itself, not Home Assistant: take the name, make a
        # key, check it is unused, capture a face, register it with Frigate and
        # remember the person. Each failure is a plain, honest reply.
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "")
        except (TypeError, ValueError):
            args = {}
        name = args.get("name") if isinstance(args, dict) else None
        key = _person_key(name) if isinstance(name, str) else None
        if not key:
            return finish(device, ENROLL_BAD_NAME, tool_results)
        if key in people.names:
            return finish(device, ENROLL_TAKEN, tool_results)
        if voice is not None:
            return await _enroll_with_voice(device, ctx, key, name, tool_results, texts)
        if frigate is None:
            return finish(device, ENROLL_NO_FRIGATE, tool_results)
        room = ctx.room if ctx else ""
        try:
            img = await capture_face(room)
        except Exception:
            img = None
        if not img:
            return finish(device, ENROLL_NO_FACE, tool_results)
        try:
            await frigate.enroll(key, img)
        except FrigateError:
            return finish(device, ENROLL_FAILED, tool_results)
        if people_store is not None:
            people_store.add(key, name)
        else:
            people.add(key, name)
        return finish(device, f"Okay, I'll remember you as {name}.", tool_results)

    async def _enroll_with_voice(device: str, ctx: Context | None, key: str, name: str,
                                 tool_results: list[str], texts: list[str]) -> dict:
        # The face too, where a robot can see and Frigate can keep it; the
        # voice from this conversation, and more sentences if it needs them.
        room = ctx.room if ctx else ""
        face = False
        if frigate is not None and capture_face is not None and bodies.has(room):
            try:
                img = await capture_face(room)
                if img:
                    await frigate.enroll(key, img)
                    face = True
            except Exception:  # pylint: disable=broad-except
                face = False
        result = await voice.enrol(key, texts)
        if face or result.get("enrolled"):
            remember(key, name)
        if result.get("enrolled"):
            return finish(device, f"Okay, I'll remember you as {name}"
                                  + (", your face and your voice." if face else ", and your voice."), tool_results)
        if result.get("available") is False or not result:
            if face:
                return finish(device, f"Okay, I'll remember your face, {name}. "
                                      "I can't learn voices right now.", tool_results)
            return finish(device, "Sorry, I can't learn voices right now.", tool_results)
        enrolling[device] = (key, name, clock())
        if face:
            return finish(device, f"I'll remember your face, {name}. Now tell me a little about your day, "
                                  "so I learn your voice too?", tool_results)
        return finish(device, f"Nice to meet you, {name}! Tell me a little about your day, "
                              "so I learn your voice?", tool_results)

    async def _keep_enrolling(device: str, text: str, now: float) -> dict | None:
        """The next sentence of a voice enrolment, or None if none is going on."""
        pending = enrolling.get(device)
        if pending is None or voice is None:
            return None
        key, name, at = pending
        if now - at > ENROL_SECONDS:
            enrolling.pop(device, None)
            return None
        if gate_mod.gate(text, "") == "stop":
            enrolling.pop(device, None)
            return finish(device, STOPPED, [])
        result = await voice.enrol(key, [text])
        if result.get("enrolled"):
            enrolling.pop(device, None)
            remember(key, name)
            return finish(device, f"Got it, {name}, I'll know your voice now.", [])
        if result.get("available") is False or not result:
            enrolling.pop(device, None)
            return finish(device, "Sorry, I can't learn voices right now.", [])
        enrolling[device] = (key, name, now)
        return finish(device, "Lovely! One more sentence?", [])

    async def _forget(device: str, tool_results: list[str]) -> dict:
        key = last_speaker(device, clock())
        if key is None:
            return finish(device, "I'm not sure who you are, so I can't forget you.", tool_results)
        name = people.names.get(key, key)
        if voice is not None:
            await voice.forget(key)
        if frigate is not None:
            try:
                faces = await frigate.list()
                if faces.get(key):
                    await frigate.delete(key, faces[key])
            except FrigateError:
                pass
        speakers.pop(device, None)
        if people_store is not None and people_store.has(key):
            people_store.remove(key)
            return finish(device, f"Okay, {name}, I've forgotten you: your face and your voice.", tool_results)
        return finish(device, f"Okay, {name}, I've forgotten your recordings. "
                              "Your name is in the house settings, so it stays.", tool_results)

    async def from_cloud(device: str, ctx: Context | None, data: dict | None, tool_results: list[str],
                         texts: list[str] | None = None) -> dict:
        if data is None:
            return finish(device, CLOUD_DOWN, tool_results)
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls")
        if calls:
            enroll = next((c for c in calls if (c.get("function") or {}).get("name") == "enroll_person"), None)
            if enroll is not None:
                return await _enroll(device, ctx, enroll, tool_results, texts or [])
            if any((c.get("function") or {}).get("name") == "forget_person" for c in calls):
                return await _forget(device, tool_results)
            return _tool_reply(calls) if _allowed(calls, ctx) else finish(device, REFUSED, tool_results)
        text = msg.get("content") or ""
        room = ctx.room if ctx else ""
        if bodies.has(room):
            mood, gesture, text = body_mod.split_tag(text)
            act = {k: v for k, v in (("mood", mood), ("gesture", gesture)) if v}
            await bodies.act(room, act)
        return finish(device, text, tool_results)

    async def completions(request: web.Request) -> web.Response:
        body = await request.json()
        messages = body.get("messages") or []
        conv = body.get("user") or ""
        system = messages[0].get("content") or "" if messages and messages[0].get("role") == "system" else ""
        ctx = parse_context(system)
        device = ctx.device_id if ctx else ""
        text, asked, rounds = _turn(messages)
        tool_results = [m.get("content") or "" for m in rounds if m.get("role") in ("tool", "function")]
        texts = [m.get("content") or "" for m in messages if m.get("role") == "user"]
        now = clock()
        session: aiohttp.ClientSession = request.app["session"]
        started = time.monotonic()

        # A later round of this turn: a local act's result, or the cloud's.
        if rounds:
            call_ids = [m.get("tool_call_id") or "" for m in rounds if m.get("role") == "tool"]
            t = state.take_pending(call_ids, now)
            if t is not None:
                ok = "Success" in tool_results
                said = actions.done_phrase(t) if ok else "That didn't work."
                if ok and ctx:
                    await bodies.act(ctx.room, {"mood": "happy", "gesture": "nod"})
                log({"conv": conv, "tier": "local", "round": "result", "results": tool_results})
                return web.json_response(finish(device, said, tool_results))
            room = ctx.room if ctx else ""
            last = last_speaker(device, now)
            return web.json_response(await from_cloud(
                device, ctx, await cloud(session, body, room, None, last), tool_results, texts))

        room = ctx.room if ctx else ""
        # A voice enrolment going on takes the next sentences.
        enrol_reply = await _keep_enrolling(device, text, now)
        if enrol_reply is not None:
            log({"conv": conv, "device": device, "room": room, "tier": "enrol", "route": "voice",
                 "ms": int((time.monotonic() - started) * 1000)})
            return web.json_response(enrol_reply)

        # Who is speaking: voice-id's scores for this sentence (it may still be
        # on its way), with who is in the room. A sure answer refines the voice.
        voice_scores = (await reports.wait(text) or None) if voice is not None else None
        last = last_speaker(device, now)
        assessment = people.assess(room, now, voice_scores, last)
        if assessment.speaker:
            speakers[device] = (assessment.speaker, now)
            last = assessment.speaker
        if assessment.learn and voice_scores and voice is not None:
            await voice.learn(assessment.learn, text)

        # A command to the room's body ("nod", "dance", "go to sleep") is
        # answered here, before the gate: "Nod." alone is a fragment to it.
        cmd = body_mod.command(text) if bodies.has(room) else None
        if cmd is not None:
            await bodies.act(room, cmd[0])
            log({"conv": conv, "device": device, "room": room, "text": text, "tier": "body", "route": "command",
                 "ms": int((time.monotonic() - started) * 1000)})
            return web.json_response(finish(device, cmd[1], []))

        verdict = gate_mod.gate(text, state.last_reply(device, now)) if ctx else "escalate"
        tier, route = "gate", verdict
        if verdict == "reject":
            resp = finish(device, SORRY, [])
        elif verdict == "stop":
            resp = finish(device, STOPPED, [])
        else:
            t = triage.Triage("escalate")
            if verdict == "pass" and cfg.mode != "cloud-first":
                t = await triage.classify(session, cfg.local_url, cfg.local_model, ctx, asked, cfg.local_timeout)
                tier, route = "local", t.route
            if t.route == "act":
                calls = actions.tool_calls(t)
                state.add_pending(calls[0]["id"], t, now)
                resp = _tool_reply(calls)
            elif t.route == "reject":
                resp = finish(device, SORRY, [])
            elif t.route == "clarify":
                resp = finish(device, CLARIFY, [])
            elif cfg.mode == "local-only":
                resp = finish(device, OFFLINE, [])
            else:
                tier, route = "cloud", "escalate"
                resp = await from_cloud(device, ctx, await cloud(session, body, room, voice_scores, last), [], texts)
        log({"conv": conv, "device": device, "room": ctx.room if ctx else "", "text": text,
             "tier": tier, "route": route, "ms": int((time.monotonic() - started) * 1000)})
        return web.json_response(resp)

    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def on_startup(app: web.Application) -> None:
        app["session"] = aiohttp.ClientSession()

    async def on_cleanup(app: web.Application) -> None:
        await app["session"].close()

    async def voice_report(request: web.Request) -> web.Response:
        # voice-id, on the loopback: this sentence's score for every voiceprint.
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "json"}, status=400)
        if not isinstance(data, dict) or not isinstance(data.get("text"), str) \
                or not isinstance(data.get("scores"), dict):
            return web.json_response({"error": "text and scores"}, status=400)
        reports.add(data["text"], {k: v for k, v in data["scores"].items() if k in people.names})
        return web.json_response({})

    app = web.Application()
    app["people_store"] = people_store
    app["frigate"] = frigate
    app.router.add_post("/v1/chat/completions", completions)
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/v1/voice", voice_report)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def make_body_app(bodies: Bodies, clock=time.monotonic, people: People | None = None,
                  frigate: Frigate | None = None) -> web.Application:
    """The bodies' side (assistant_router/body.py): one WebSocket each, on its
    own listener, so the LAN reaches this and not the agent's endpoint.

    A robot ("kind": anything but "room-sensor" and "face-source") is the
    room's body; it may report the faces it recognises, and may send a frame
    of the person it is looking at ({"face_probe": base64}), which is
    recognised here and remembered as the speaker of that room. A room sensor
    (pkgs/room-presence) is not a body: it only reports the phones it sees, and
    gets nothing back. A face source (the frigate person mapper) is also not a
    body: it reports the recognised people for the room ({"faces": [...]}), the
    full current set each time it changes. A house sensor
    (pkgs/wifi-presence) reports whose phones are on the home network
    ({"home": [...]}), the full set every minute."""
    people = people or People({})
    non_bodies = ("room-sensor", "face-source", "house-sensor")

    def _b64(value) -> bytes | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return base64.b64decode(value, validate=True)
        except ValueError:
            return None

    def observe_faces(room: str, faces, now: float, source: str = "face") -> None:
        # The sender reports the room's full current set, so it replaces.
        # A room's cameras say who is present ("face"); a robot's own camera
        # looks at whoever it faces, likely the speaker ("aimed").
        if isinstance(faces, list):
            if source == "face":
                people.forget_faces(room)
            for face in faces[:8]:
                if isinstance(face, dict):
                    people.observe(room, source, face.get("person"), face.get("confidence"), now)

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        room, kind = "", ""
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                hello = data.get("hello")
                if isinstance(hello, dict) and isinstance(hello.get("room"), str) and hello["room"].strip():
                    if room and kind not in non_bodies:
                        bodies.disconnect(room, ws)
                    room = hello["room"].strip()[:64]
                    kind = hello.get("kind") or ""
                    if kind not in non_bodies:
                        bodies.connect(room, ws)
                        # Tell the body what it may send back: whether this
                        # router has a face library to probe against.
                        try:
                            await ws.send_json({"body_hello": {"faces": frigate is not None}})
                        except Exception:  # pylint: disable=broad-except
                            pass
                    LOG.info(json.dumps({"body": "connected", "room": room, "kind": str(kind)[:32]}))
                elif room and kind not in non_bodies and isinstance(data.get("state"), dict):
                    bodies.update(room, data["state"], clock())
                    observe_faces(room, data["state"].get("faces"), clock(), source="aimed")
                elif room and kind not in non_bodies and "face_image" in data:
                    bodies.complete_capture(room, data["face_image"])
                elif room and kind not in non_bodies and "face_probe" in data:
                    img = _b64(data.get("face_probe"))
                    if img and frigate is not None:
                        try:
                            hit = await frigate.recognize(img)
                        except FrigateError:
                            hit = None
                        person = hit.get("name") if hit and hit.get("success") else None
                        known = person in people.names if person else False
                        if known:
                            # The robot turned to the voice first: likely the speaker.
                            people.observe(room, "aimed", person, hit.get("confidence"), clock())
                        await ws.send_json({"face_result": {
                            "person": people.names[person] if known else None,
                            "confidence": hit.get("confidence") if hit else None}})
                elif room and kind == "room-sensor" and isinstance(data.get("seen"), list):
                    for seen in data["seen"][:32]:
                        if isinstance(seen, dict) and type(seen.get("rssi")) is int and seen["rssi"] >= -85:
                            via = seen.get("via") if seen.get("via") in ("ble", "wifi") else "ble"
                            people.observe_phone(room, seen.get("person"), seen["rssi"], clock(), via=via)
                elif room and kind == "face-source" and isinstance(data.get("faces"), list):
                    observe_faces(room, data["faces"], clock())
                elif room and kind == "house-sensor" and isinstance(data.get("home"), list):
                    people.observe_home([h.get("person") for h in data["home"][:64] if isinstance(h, dict)],
                                        clock())
        finally:
            if room and kind not in non_bodies:
                bodies.disconnect(room, ws)
                LOG.info(json.dumps({"body": "gone", "room": room}))
        return ws

    app = web.Application()
    app.router.add_get("/v1/body", socket)
    return app
