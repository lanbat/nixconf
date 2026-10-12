"""voice-id: a Wyoming speech-to-text proxy for Home Assistant's voice pipeline.

Home Assistant sends each utterance here instead of to faster-whisper; this
passes every event straight through to faster-whisper (the upstream) and the
transcript straight back, and reports itself to Home Assistant as "voice-id".

Beside that, it keeps the utterance's audio and, as soon as the audio ends,
works out who it sounds like (voice_id/service.py) in a thread, in parallel
with speech-to-text: the transcript is never held back for it. Each
utterance's log line gives how long the upstream took and how much the
proxy added.
"""
from __future__ import annotations

import asyncio
import logging
import time

from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info
from wyoming.server import AsyncEventHandler

from .embedder import samples

_LOGGER = logging.getLogger("voice-id")

PROGRAM = "voice-id"


async def upstream_info(uri: str) -> Info:
    """The upstream's description, renamed so Home Assistant names the entity
    after this proxy (stt.voice_id), with the upstream's models and languages."""
    async with AsyncClient.from_uri(uri) as client:
        await client.write_event(Describe().event())
        while True:
            event = await client.read_event()
            if event is None:
                raise ConnectionError("upstream closed before describing itself")
            if Info.is_type(event.type):
                info = Info.from_event(event)
                break
    for program in info.asr:
        program.name = PROGRAM
        program.description = f"Speaker identification in front of {program.description or 'speech-to-text'}"
        program.attribution = Attribution(name="lanbat", url="https://github.com/lanbat/nixos")
    return info


class ProxyHandler(AsyncEventHandler):
    def __init__(self, upstream_uri: str, voice, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.upstream_uri = upstream_uri
        self.voice = voice  # voice_id.service.VoiceId, or None for pass-through only
        self.audio = bytearray()
        self.audio_format = (16000, 2, 1)
        self.embedding: asyncio.Task | None = None
        self.upstream: AsyncClient | None = None
        self.pump: asyncio.Task | None = None
        self.audio_bytes = 0
        self.stopped_at: float | None = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event((await upstream_info(self.upstream_uri)).event())
            return True

        if self.upstream is None:
            self.upstream = AsyncClient.from_uri(self.upstream_uri)
            await self.upstream.connect()
            self.pump = asyncio.create_task(self._pump())

        if AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self.audio_format = (start.rate, start.width, start.channels)
            self.audio.clear()
        elif AudioChunk.is_type(event.type):
            self.audio_bytes += len(event.payload or b"")
            if self.voice is not None and len(self.audio) < 60 * 32000:
                self.audio += event.payload or b""
        elif AudioStop.is_type(event.type):
            self.stopped_at = time.monotonic()
            if self.voice is not None and self.audio:
                rate, width, channels = self.audio_format
                signal = samples(bytes(self.audio), width, channels)
                self.embedding = asyncio.create_task(asyncio.to_thread(self.voice.embed, signal, rate))

        await self.upstream.write_event(event)
        return True

    async def _pump(self) -> None:
        """Every upstream event back to Home Assistant, until the transcript."""
        assert self.upstream is not None
        try:
            while True:
                event = await self.upstream.read_event()
                if event is None:
                    break
                received = time.monotonic()
                await self.write_event(event)
                if Transcript.is_type(event.type):
                    sent = time.monotonic()
                    upstream_ms = (received - self.stopped_at) * 1000 if self.stopped_at else -1
                    _LOGGER.info(
                        "transcript: upstream %.0f ms after the audio ended, proxy %.2f ms, %.1f s of audio",
                        upstream_ms,
                        (sent - received) * 1000,
                        self.audio_bytes / 32000,
                    )
                    if self.embedding is not None:
                        asyncio.create_task(self._identify(Transcript.from_event(event).text, self.embedding))
                        self.embedding = None
                    break
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("upstream failed")
        finally:
            await self._close_upstream()

    async def _identify(self, text: str, embedding: asyncio.Task) -> None:
        """After the transcript has gone back: who the utterance sounds like."""
        try:
            await self.voice.utterance(text, await embedding)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("speaker identification failed")

    async def _close_upstream(self) -> None:
        if self.upstream is not None:
            try:
                await self.upstream.disconnect()
            except Exception:  # pylint: disable=broad-except
                pass
            self.upstream = None

    async def disconnect(self) -> None:
        if self.pump is not None:
            self.pump.cancel()
        await self._close_upstream()
