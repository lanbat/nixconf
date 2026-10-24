"""The helper's HTTP face, on the loopback (services/yt-show.nix).

The assistant router asks it what to show and what to play; it touches neither
Home Assistant nor the screen, and keeps no state: the list a person is
choosing from is the conversation's, and lives in the router.

Invidious first, yt-dlp second, decided per call and not at boot: Invidious can
be up but unable to play a particular video (services/invidious.nix).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aiohttp import web

from .core import PAGE_SIZE, page as page_of, video_id
from .invidious import Invidious, InvidiousError
from .ytdlp import YtDlp, YtDlpError

LOG = logging.getLogger("yt-show")

# Invidious answers 20 results a page; the screen shows page_size of them, so a
# request for our page N lands inside its page and is sliced.
INVIDIOUS_PAGE = 20
MAX_PAGE = 50


@dataclass
class Config:
    invidious_url: str | None = None
    invidious_public_url: str | None = None
    ytdlp: str = "yt-dlp"
    page_size: int = PAGE_SIZE


def make_app(cfg: Config, invidious: Invidious | None = None,
             ytdlp: YtDlp | None = None) -> web.Application:
    invidious = invidious if invidious is not None else (
        Invidious(cfg.invidious_url, cfg.invidious_public_url) if cfg.invidious_url else None)
    ytdlp = ytdlp if ytdlp is not None else YtDlp(cfg.ytdlp)

    def bad(what: str) -> web.Response:
        return web.json_response({"error": what}, status=400)

    async def search(query: str, want: int) -> tuple[list[dict], bool, str] | None:
        """(items numbered from 1, another page follows, source), or None when
        neither source could answer."""
        if invidious is not None:
            try:
                items = await invidious.search(query, want // INVIDIOUS_PAGE + 1)
                offset = want % INVIDIOUS_PAGE
                chunk = items[offset : offset + cfg.page_size]
                # A short last page is the end; a full one means there may be more.
                more = len(items) > offset + cfg.page_size or len(items) >= INVIDIOUS_PAGE
                numbered, _ = page_of(chunk, 1, cfg.page_size)
                return [r.as_dict() for r in numbered], more, "invidious"
            except InvidiousError as exc:
                LOG.warning("invidious search failed, trying yt-dlp: %s", exc)
        try:
            items = await ytdlp.search(query, want // cfg.page_size + 1, cfg.page_size)
        except YtDlpError as exc:
            LOG.warning("yt-dlp search failed: %s", exc)
            return None
        chunk = items[want : want + cfg.page_size]
        numbered, _ = page_of(chunk, 1, cfg.page_size)
        return [r.as_dict() for r in numbered], len(items) >= want + cfg.page_size, "yt-dlp"

    async def resolve(vid: str) -> tuple[str, str, str] | None:
        """(title, address, source), or None when neither source could play it."""
        if invidious is not None:
            try:
                title, url = await invidious.stream(vid)
                return title, url, "invidious"
            except InvidiousError as exc:
                LOG.warning("invidious could not play %s, trying yt-dlp: %s", vid, exc)
        try:
            title, url = await ytdlp.stream(vid)
            return title, url, "yt-dlp"
        except YtDlpError as exc:
            LOG.warning("yt-dlp could not play %s: %s", vid, exc)
            return None

    async def on_search(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return bad("not JSON")
        if not isinstance(body, dict):
            return bad("not an object")
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            return bad("query")
        page = body.get("page", 1)
        if type(page) is not int or not 1 <= page <= MAX_PAGE:
            return bad("page")
        found = await search(query.strip()[:200], (page - 1) * cfg.page_size)
        if found is None:
            return web.json_response({"error": "neither Invidious nor yt-dlp could search"}, status=502)
        items, more, source = found
        return web.json_response({"items": items, "has_next": more, "page": page, "source": source})

    async def on_resolve(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return bad("not JSON")
        if not isinstance(body, dict):
            return bad("not an object")
        raw = body.get("video_id")
        vid = video_id(raw) if isinstance(raw, str) else None
        if vid is None:
            return bad("video_id")
        found = await resolve(vid)
        if found is None:
            return web.json_response({"error": "neither Invidious nor yt-dlp could play it"}, status=502)
        title, url, source = found
        return web.json_response({"url": url, "title": title, "source": source})

    async def on_health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "source": "invidious" if invidious is not None else "yt-dlp"})

    app = web.Application()
    app.router.add_get("/healthz", on_health)
    app.router.add_post("/search", on_search)
    app.router.add_post("/resolve", on_resolve)
    return app
