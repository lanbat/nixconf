"""voice-id's own side: voiceprints, the router, and a small loopback API.

For each utterance the proxy (voice_id/proxy.py) hands over the transcript and
the speaker embedding; this scores it against every voiceprint and tells the
assistant router, which weighs the scores with who else is in the room:

    POST <router>/v1/voice  {"text": "...", "scores": {"alex": 0.71, "sam": 0.12}, "t": ...}

The router calls back here (loopback only):

    POST   /enrol   {"key": "alex", "texts": [...]}  -> {"enrolled": bool, "utterances": n, "needed": 3}
    POST   /learn   {"key": "alex", "text": "..."}   -> {"learned": bool}
    DELETE /people/<key>                              -> {"forgotten": bool}
    POST   /people/<key>/merge {"into": "sam"}        -> {"merged": bool}
    POST   /embed   {"text": "...", "pcm": "<base64>"} -> {"reported": bool}
    GET    /status                                    -> {"available": bool, "people": [...]}

/embed is a robot's second ear (docs/people.md): the same sentence as the
robot heard it, 16-bit 16 kHz mono, sent by the router. It is scored and
reported like the pipeline's own audio, and then dropped; the router keeps
the better of the two reports. It is not kept for enrolment or learning,
which use the pipeline's audio, so every voiceprint comes from one kind of
microphone.

Enrolment uses the utterances of the conversation the person just had, from
memory (Recent); if there are too few yet, they wait (in memory, ten minutes)
for the next call. Nothing about a voice that isn't enrolled is ever written.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import time

import aiohttp
from aiohttp import web

from .embedder import RATE, levelled, samples
from .voiceprints import Recent, Store

_LOGGER = logging.getLogger("voice-id")
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_EAR_SECONDS = 15  # the robot records no longer than this


class VoiceId:
    def __init__(self, store: Store, embed, router_url: str = "", clock=time.time) -> None:
        self.store = store
        self.embed = embed  # signal -> embedding | None (blocking; run in a thread)
        self.router_url = router_url
        self.clock = clock
        self.recent = Recent()
        self._pending: dict[str, tuple[float, list[list[float]]]] = {}
        self._session: aiohttp.ClientSession | None = None

    async def ear(self, text: str, pcm: bytes) -> dict | None:
        """A robot's recording of a sentence: scored and reported, not kept."""
        if self.embed is None or not text.strip():
            return None
        signal = levelled(samples(pcm[: MAX_EAR_SECONDS * RATE * 2]))
        embedding = await asyncio.to_thread(self.embed, signal, RATE) if signal is not None else None
        return await self.utterance(text, embedding, remember=False, source="robot")

    async def utterance(self, text: str, embedding: list[float] | None, remember: bool = True,
                        source: str = "pipeline") -> dict | None:
        """Score one utterance and tell the router; returns what was sent.

        The router waits briefly for each sentence's report, so one is always
        sent, with no scores when there is nothing to say (too little speech,
        nobody enrolled, the layer locked)."""
        if not text.strip():
            return None
        now = self.clock()
        scores: dict[str, float] = {}
        if embedding is not None:
            if remember:
                self.recent.add(text, embedding, now)
            if not self.store.available() or not self.store.people():
                self.store.reload()  # the layer was just unlocked
            scores = self.store.scores(embedding)
        report = {"text": text, "scores": scores, "t": now}
        if scores:
            best = max(scores, key=scores.get)
            _LOGGER.info("voice (%s): closest %s (%.2f) of %d", source, best, scores[best], len(scores))
        if self.router_url:
            try:
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                async with self._session.post(self.router_url, json=report,
                                              timeout=aiohttp.ClientTimeout(total=2)) as r:
                    await r.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.warning("router unreachable: %s", err)
        return report

    def enrol(self, key: str, texts: list[str]) -> dict:
        now = self.clock()
        found = self.recent.find(texts, now)
        at, pending = self._pending.get(key, (now, []))
        if now - at > self.recent.seconds:
            pending = []
        merged = pending + [e for e in found if e not in pending]
        enrolled = len(merged) >= Store.MIN_ENROL and self.store.available()
        if enrolled:
            self.store.enrol(key, merged)
            self._pending.pop(key, None)
            _LOGGER.info("voice: enrolled %s from %d utterances", key, len(merged))
        else:
            self._pending[key] = (now, merged)
        return {"enrolled": enrolled, "utterances": len(merged), "needed": Store.MIN_ENROL,
                "available": self.store.available()}

    def learn(self, key: str, text: str) -> bool:
        embedding = self.recent.get(text, self.clock())
        return embedding is not None and self.store.learn(key, embedding)

    def forget(self, key: str) -> bool:
        self._pending.pop(key, None)
        return self.store.forget(key)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


def make_api(voice: VoiceId) -> web.Application:
    def key_of(value) -> str | None:
        return value if isinstance(value, str) and _KEY.match(value) else None

    async def enrol(request: web.Request) -> web.Response:
        body = await request.json()
        key, texts = key_of(body.get("key")), body.get("texts")
        if key is None or not isinstance(texts, list):
            return web.json_response({"error": "key and texts"}, status=400)
        return web.json_response(voice.enrol(key, [t for t in texts if isinstance(t, str)][:50]))

    async def learn(request: web.Request) -> web.Response:
        body = await request.json()
        key, text = key_of(body.get("key")), body.get("text")
        if key is None or not isinstance(text, str):
            return web.json_response({"error": "key and text"}, status=400)
        return web.json_response({"learned": voice.learn(key, text)})

    async def forget(request: web.Request) -> web.Response:
        key = key_of(request.match_info["key"])
        if key is None:
            return web.json_response({"error": "key"}, status=400)
        return web.json_response({"forgotten": voice.forget(key)})

    async def merge(request: web.Request) -> web.Response:
        key = key_of(request.match_info["key"])
        try:
            body = await request.json()
        except ValueError:
            body = None
        into = key_of(body.get("into")) if isinstance(body, dict) else None
        if key is None or into is None:
            return web.json_response({"error": "key and into"}, status=400)
        voice._pending.pop(key, None)
        return web.json_response({"merged": voice.store.merge(key, into)})

    async def embed(request: web.Request) -> web.Response:
        body = await request.json()
        text, pcm = body.get("text"), body.get("pcm")
        if not isinstance(text, str) or not isinstance(pcm, str):
            return web.json_response({"error": "text and pcm"}, status=400)
        try:
            audio = base64.b64decode(pcm, validate=True)
        except (binascii.Error, ValueError):
            return web.json_response({"error": "pcm"}, status=400)
        return web.json_response({"reported": await voice.ear(text, audio) is not None})

    async def status(_request: web.Request) -> web.Response:
        return web.json_response({"available": voice.store.available(), "people": voice.store.people()})

    # A robot's sentence is 15 s at most: 480 KB, 640 KB in base64.
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/embed", embed)
    app.router.add_post("/enrol", enrol)
    app.router.add_post("/learn", learn)
    app.router.add_delete("/people/{key}", forget)
    app.router.add_post("/people/{key}/merge", merge)
    app.router.add_get("/status", status)
    return app
