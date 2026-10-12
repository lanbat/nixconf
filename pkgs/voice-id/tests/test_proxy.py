"""The proxy passes everything through and identifies on the side."""
import asyncio
from functools import partial

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.server import AsyncEventHandler, AsyncServer, AsyncTcpServer

from voice_id.proxy import ProxyHandler


class FakeWhisper(AsyncEventHandler):
    async def handle_event(self, event):
        if AudioStop.is_type(event.type):
            await self.write_event(Transcript(text="Good morning, Nabu.").event())
        return True


class FakeVoice:
    def __init__(self):
        self.calls = []

    def embed(self, signal, rate):
        return [len(signal) / rate]  # seconds of audio: enough to check it all arrived

    async def utterance(self, text, embedding):
        self.calls.append((text, embedding))


async def test_transcript_unchanged_and_speaker_checked_beside_it(unused_tcp_port_factory):
    up_port, proxy_port = unused_tcp_port_factory(), unused_tcp_port_factory()
    upstream = AsyncTcpServer("127.0.0.1", up_port)
    up_task = asyncio.create_task(upstream.run(FakeWhisper))
    voice = FakeVoice()
    proxy = AsyncServer.from_uri(f"tcp://127.0.0.1:{proxy_port}")
    proxy_task = asyncio.create_task(proxy.run(partial(ProxyHandler, f"tcp://127.0.0.1:{up_port}", voice)))
    await asyncio.sleep(0.2)
    async with AsyncTcpClient("127.0.0.1", proxy_port) as client:
        await client.write_event(Transcribe().event())
        await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())
        for _ in range(10):
            await client.write_event(AudioChunk(rate=16000, width=2, channels=1, audio=bytes(3200)).event())
        await client.write_event(AudioStop().event())
        event = await asyncio.wait_for(client.read_event(), 5)
        assert Transcript.from_event(event).text == "Good morning, Nabu."
    for _ in range(50):
        if voice.calls:
            break
        await asyncio.sleep(0.02)
    assert voice.calls == [("Good morning, Nabu.", [1.0])]  # 10 x 0.1 s of audio
    proxy_task.cancel()
    up_task.cancel()
