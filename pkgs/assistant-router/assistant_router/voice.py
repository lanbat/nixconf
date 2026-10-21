"""The voice side of who is speaking: voice-id's reports and its API.

voice-id (services/voice-id.nix) scores each utterance against every
voiceprint and POSTs {"text", "scores", "t"} to /v1/voice here; the speech-to-
text session it sits in has no satellite id, so a report is matched to a
request by its transcript (normalised the same way on both sides), within
REPORT_SECONDS. A report may arrive a moment after Home Assistant's request,
so a request waits up to WAIT_SECONDS for it.

A room whose robot has an ear sends the sentence a second time, as the robot
heard it (the body socket's voice_sample, scored by voice-id's /embed): the
request then waits up to EAR_WAIT_SECONDS for both, and the report that
matches someone better is kept.

The router calls back into voice-id to enrol, refine and forget voiceprints
(VoiceIdClient). Only person keys and transcripts travel; never audio.
"""
from __future__ import annotations

import asyncio
import re
import time

import aiohttp

REPORT_SECONDS = 10.0
WAIT_SECONDS = 0.4
EAR_WAIT_SECONDS = 0.8


def normalise_text(text: str) -> str:
    """As voice-id normalises it (voice_id/voiceprints.py)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", (text or "").lower())).strip()


class VoiceReports:
    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._reports: dict[str, tuple[float, dict[str, float]]] = {}
        self._counts: dict[str, int] = {}
        self._arrived = asyncio.Event()

    def add(self, text: str, scores: dict) -> None:
        now = self.clock()
        self._reports = {t: v for t, v in self._reports.items() if now - v[0] <= REPORT_SECONDS}
        self._counts = {t: n for t, n in self._counts.items() if t in self._reports}
        clean = {str(k): float(v) for k, v in (scores or {}).items()
                 if isinstance(k, str) and type(v) in (int, float) and -1 <= v <= 1}
        key = normalise_text(text)
        if key:
            # An empty report (too little speech, nobody enrolled) still
            # arrived: the request stops waiting for it.
            before = self._reports.get(key)
            if before is None or max(clean.values(), default=-2.0) > max(before[1].values(), default=-2.0):
                self._reports[key] = (now, clean)
            self._counts[key] = self._counts.get(key, 0) + 1
            self._arrived.set()

    def get(self, text: str) -> dict[str, float] | None:
        hit = self._reports.get(normalise_text(text))
        return hit[1] if hit and self.clock() - hit[0] <= REPORT_SECONDS else None

    def count(self, text: str) -> int:
        return self._counts.get(normalise_text(text), 0) if self.get(text) is not None else 0

    async def wait(self, text: str, timeout: float = WAIT_SECONDS, expect: int = 1) -> dict[str, float] | None:
        """The scores for `text`, waiting briefly for `expect` reports still on
        their way (two where a robot's ear sends its own)."""
        deadline = time.monotonic() + timeout
        while True:
            found = self.get(text)
            remaining = deadline - time.monotonic()
            if (found is not None and self.count(text) >= expect) or remaining <= 0:
                return found
            self._arrived.clear()
            try:
                await asyncio.wait_for(self._arrived.wait(), remaining)
            except asyncio.TimeoutError:
                return self.get(text)


class VoiceIdClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        try:
            async with self._session.request(method, self.base + path, json=payload) as r:
                data = await r.json(content_type=None)
                return data if r.status < 400 and isinstance(data, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {}

    async def enrol(self, key: str, texts: list[str]) -> dict:
        return await self._call("POST", "/enrol", {"key": key, "texts": texts})

    async def learn(self, key: str, text: str) -> bool:
        return bool((await self._call("POST", "/learn", {"key": key, "text": text})).get("learned"))

    async def people(self) -> list[str] | None:
        """Who has a voiceprint; None when voice-id can't say."""
        found = (await self._call("GET", "/status")).get("people")
        return found if isinstance(found, list) else None

    async def embed(self, text: str, pcm: str) -> bool:
        """A robot's recording of a sentence (base64 s16le, 16 kHz mono):
        voice-id scores it and reports to /v1/voice like any other."""
        return bool((await self._call("POST", "/embed", {"text": text, "pcm": pcm})).get("reported"))

    async def forget(self, key: str) -> bool:
        return bool((await self._call("DELETE", f"/people/{key}")).get("forgotten"))

    async def merge(self, key: str, into: str) -> bool:
        """`key`'s voiceprint becomes part of `into`'s (an admin linked them)."""
        return bool((await self._call("POST", f"/people/{key}/merge", {"into": into})).get("merged"))

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
