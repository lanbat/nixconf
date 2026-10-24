"""yt-show — YouTube for the Echo Show, as the assistant router asks for it.

  yt-show serve --port 8102 [--invidious URL] [--ytdlp PATH] [--page-size N]
  yt-show search 'query' [--page N] [--invidious URL]
  yt-show resolve 'URL or id' [--invidious URL]

`serve` is what services/yt-show.nix runs. The other two are for trying it on a
host without the router (or for a person who wants the address).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from aiohttp import web

from .core import PAGE_SIZE, page as page_of
from .invidious import Invidious, InvidiousError
from .server import INVIDIOUS_PAGE, Config, make_app, make_media_app
from .ytdlp import YtDlp, YtDlpError


def _options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--invidious", help="Invidious' base URL (services/invidious.nix); unset: yt-dlp only")
    p.add_argument("--invidious-public", help="the address the Show fetches a stream from: Invidious' "
                   "own https domain (default: --invidious)")
    p.add_argument("--ytdlp", default="yt-dlp", help="the yt-dlp to run (default: on PATH)")
    p.add_argument("--ffmpeg", default="ffmpeg", help="the ffmpeg to run for a 720p remux")


def _config(a: argparse.Namespace) -> Config:
    return Config(invidious_url=a.invidious, invidious_public_url=a.invidious_public,
                  ytdlp=a.ytdlp, ffmpeg=a.ffmpeg, page_size=getattr(a, "page_size", PAGE_SIZE),
                  cache_dir=getattr(a, "cache_dir", None),
                  media_url_base=getattr(a, "media_url_base", None),
                  remux=not getattr(a, "no_remux", False),
                  max_minutes=getattr(a, "max_minutes", 15),
                  remux_seconds=getattr(a, "remux_seconds", 30),
                  cache_days=getattr(a, "cache_days", 7),
                  max_cache_mb=getattr(a, "max_cache_mb", 4096))


async def _search(a: argparse.Namespace) -> int:
    cfg = _config(a)
    invidious = Invidious(cfg.invidious_url, cfg.invidious_public_url) if cfg.invidious_url else None
    ytdlp = YtDlp(cfg.ytdlp)
    want = (a.page - 1) * cfg.page_size
    try:
        # The same order server.py uses, without the HTTP in between.
        if invidious is not None:
            try:
                items = await invidious.search(a.query, want // INVIDIOUS_PAGE + 1)
                offset = want % INVIDIOUS_PAGE
                numbered, _ = page_of(items[offset : offset + cfg.page_size], 1, cfg.page_size)
                print(json.dumps({"items": [r.as_dict() for r in numbered], "source": "invidious"}))
                return 0
            except InvidiousError as exc:
                print(f"invidious: {exc}", file=sys.stderr)
        items = await ytdlp.search(a.query, a.page, cfg.page_size)
        numbered, _ = page_of(items[want : want + cfg.page_size], 1, cfg.page_size)
        print(json.dumps({"items": [r.as_dict() for r in numbered], "source": "yt-dlp"}))
        return 0
    except (InvidiousError, YtDlpError) as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if invidious is not None:
            await invidious.aclose()


async def _resolve(a: argparse.Namespace) -> int:
    from .core import video_id

    vid = video_id(a.video)
    if vid is None:
        print("not a YouTube video address or id", file=sys.stderr)
        return 2
    cfg = _config(a)
    invidious = Invidious(cfg.invidious_url, cfg.invidious_public_url) if cfg.invidious_url else None
    ytdlp = YtDlp(cfg.ytdlp)
    try:
        if invidious is not None:
            try:
                title, url, _itag, _seconds = await invidious.stream(vid)
                print(json.dumps({"url": url, "title": title, "source": "invidious"}))
                return 0
            except InvidiousError as exc:
                print(f"invidious: {exc}", file=sys.stderr)
        title, url = await ytdlp.stream(vid)
        print(json.dumps({"url": url, "title": title, "source": "yt-dlp"}))
        return 0
    except (InvidiousError, YtDlpError) as exc:
        print(f"resolve failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if invidious is not None:
            await invidious.aclose()


async def _serve(a: argparse.Namespace) -> None:
    cfg = _config(a)
    runner = web.AppRunner(make_app(cfg))
    await runner.setup()
    await web.TCPSite(runner, a.host, a.port).start()
    media = None
    if cfg.remuxer() is not None:
        media = web.AppRunner(make_media_app(cfg))
        await media.setup()
        await web.TCPSite(media, a.media_host, a.media_port).start()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        if media is not None:
            await media.cleanup()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="yt-show")
    sub = p.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="the HTTP helper the router calls")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8102)
    serve.add_argument("--page-size", type=int, default=PAGE_SIZE)
    # A 720p remux (remux.py): kept in --cache-dir, fetched by the screen at
    # --media-url-base, and served by the second app below.
    serve.add_argument("--cache-dir", help="where the 720p remuxes are kept; unset: no remux")
    serve.add_argument("--media-url-base", help="the screen-facing base URL of the media app, "
                       "https://yt.<domain> (services/yt-show.nix); unset: no remux")
    serve.add_argument("--media-host", default="127.0.0.1")
    serve.add_argument("--media-port", type=int, default=8103)
    serve.add_argument("--no-remux", action="store_true")
    serve.add_argument("--max-minutes", type=float, default=15.0,
                       help="longer videos play as 360p rather than keep the screen waiting")
    serve.add_argument("--remux-seconds", type=float, default=30.0,
                       help="how long a remux may take before the 360p address is played")
    serve.add_argument("--cache-days", type=float, default=7.0)
    serve.add_argument("--max-cache-mb", type=float, default=4096.0)
    _options(serve)

    search = sub.add_parser("search", help="print one page of search results as JSON")
    search.add_argument("query")
    search.add_argument("--page", type=int, default=1)
    _options(search)

    resolve = sub.add_parser("resolve", help="print the playable address of one video as JSON")
    resolve.add_argument("video")
    _options(resolve)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if a.command == "serve":
        asyncio.run(_serve(a))
    elif a.command == "search":
        raise SystemExit(asyncio.run(_search(a)))
    else:
        raise SystemExit(asyncio.run(_resolve(a)))


if __name__ == "__main__":
    main()
