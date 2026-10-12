"""voice-id --uri tcp://127.0.0.1:10303 --upstream tcp://127.0.0.1:10301
          [--model campplus.onnx --store-dir DIR --router-url URL --api-port N]

Without --model it is a pass-through proxy (phase A)."""
from __future__ import annotations

import argparse
import asyncio
import logging
from functools import partial

from aiohttp import web
from wyoming.server import AsyncServer

from .proxy import ProxyHandler
from .service import VoiceId, make_api
from .voiceprints import Store

_LOGGER = logging.getLogger("voice-id")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="voice-id", description="speaker identification in front of speech-to-text")
    parser.add_argument("--uri", required=True, help="where Home Assistant connects, e.g. tcp://127.0.0.1:10303")
    parser.add_argument("--upstream", required=True, help="the speech-to-text server, e.g. tcp://127.0.0.1:10301")
    parser.add_argument("--model", help="a sherpa-onnx speaker-embedding model (CAM++, 16 kHz)")
    parser.add_argument("--store-dir", help="where voiceprints.json lives (the encrypted workload layer)")
    parser.add_argument("--router-url", default="", help="the assistant router's /v1/voice")
    parser.add_argument("--api-port", type=int, default=0, help="loopback port for enrol/learn/forget")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(message)s")

    voice = None
    if args.model and args.store_dir:
        from .embedder import Embedder

        embedder = Embedder(args.model)
        voice = VoiceId(Store(args.store_dir), embedder.embed, args.router_url)
        if args.api_port:
            runner = web.AppRunner(make_api(voice))
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", args.api_port).start()
        _LOGGER.info("speaker identification on; voiceprints %s",
                     "available" if voice.store.available() else "locked (unknown until unlock)")

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("listening on %s, passing through to %s", args.uri, args.upstream)
    await server.run(partial(ProxyHandler, args.upstream, voice))


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
