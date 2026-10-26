"""The helper's HTTP face, on the loopback (services/yt-show.nix).

The assistant router asks it what to show and what to play; it touches neither
Home Assistant nor the screen, and keeps no state: the list a person is
choosing from is the conversation's, and lives in the router.

Invidious first, yt-dlp second, decided per call and not at boot: Invidious can
be up but unable to play a particular video (services/invidious.nix).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from .assets import BAT_SVG
from .core import PAGE_SIZE, is_id, page as page_of, video_id
from .invidious import Invidious, InvidiousError
from .jellyfin import Jellyfin, JellyfinError
from .ma import Ma, MaError
from .remux import NAME, Remux, RemuxError
from .ytdlp import YtDlp, YtDlpError

LOG = logging.getLogger("yt-show")

# Invidious answers 20 search results a page; the screen shows page_size of them,
# so a request for our page N lands inside its page and is sliced. A playlist is
# 100 videos a page.
INVIDIOUS_PAGE = 20
INVIDIOUS_PLAYLIST_PAGE = 100
MAX_PAGE = 50
# A search shows five at a time for six pages: thirty fits a conversation, and
# past that a narrower request is more use than a seventh page.
MAX_SEARCH_PAGES = 6


@dataclass
class Config:
    invidious_url: str | None = None
    invidious_public_url: str | None = None
    ytdlp: str = "yt-dlp"
    ffmpeg: str = "ffmpeg"
    page_size: int = PAGE_SIZE
    # A 720p remux (remux.py) is made only when there is somewhere to keep it
    # and an address the screen fetches it from (services/yt-show.nix).
    cache_dir: str | None = None
    media_url_base: str | None = None
    remux: bool = True
    max_minutes: float = 15.0
    remux_seconds: float = 30.0
    cache_days: float = 7.0
    max_cache_mb: float = 4096.0
    # Music Assistant (ma.py): audio results and playback on the room's player.
    ma_url: str | None = None
    ma_token_file: str | None = None
    ma_limit: int = 40
    # Jellyfin (jellyfin.py): its video library, played through a transcode URL.
    jellyfin_url: str | None = None
    jellyfin_public_url: str | None = None
    jellyfin_key_file: str | None = None
    jellyfin_limit: int = 20

    def remuxer(self) -> Remux | None:
        if not (self.remux and self.cache_dir and self.media_url_base):
            return None
        return Remux(self.cache_dir, self.ffmpeg, timeout=self.remux_seconds,
                     max_minutes=self.max_minutes, cache_days=self.cache_days,
                     max_cache_mb=self.max_cache_mb)


def make_app(cfg: Config, invidious: Invidious | None = None,
             ytdlp: YtDlp | None = None, remux: Remux | None = None,
             ma: Ma | None = None, jellyfin: Jellyfin | None = None) -> web.Application:
    invidious = invidious if invidious is not None else (
        Invidious(cfg.invidious_url, cfg.invidious_public_url) if cfg.invidious_url else None)
    ytdlp = ytdlp if ytdlp is not None else YtDlp(cfg.ytdlp)
    remux = remux if remux is not None else cfg.remuxer()
    ma = ma if ma is not None else (
        Ma(cfg.ma_url, cfg.ma_token_file) if cfg.ma_url and cfg.ma_token_file else None)
    jellyfin = jellyfin if jellyfin is not None else (
        Jellyfin(cfg.jellyfin_url, cfg.jellyfin_public_url, cfg.jellyfin_key_file)
        if cfg.jellyfin_url and cfg.jellyfin_public_url and cfg.jellyfin_key_file else None)

    def media_url(path: Path) -> str:
        return f"{cfg.media_url_base.rstrip('/')}/media/{path.name}"

    def bad(what: str) -> web.Response:
        return web.json_response({"error": what}, status=400)

    def decorate(items: list[dict]) -> list[dict]:
        """Every result gets a picture and a description field: the source's
        own, or the bat placeholder on the domain the screen trusts."""
        base = (cfg.media_url_base or "").rstrip("/")
        bat = f"{base}/media/bat.svg" if base else ""
        for item in items:
            if bat and not item.get("thumb"):
                item["thumb"] = bat
            item.setdefault("description", "")
        return items

    async def search(query: str, want: int) -> tuple[list[dict], bool, str] | None:
        """(items numbered from 1, another page follows, source), or None when
        neither source could answer.

        A page mixes YouTube, Music Assistant and Jellyfin: up to two videos,
        one playlist, one audio item and one Jellyfin title.  A source that is
        not configured or did not answer gives its slots to the videos."""
        block = want // cfg.page_size
        music: list[dict] = []
        if ma is not None:
            try:
                music = await ma.search(query, cfg.ma_limit)
            except MaError as exc:
                LOG.warning("music assistant search failed: %s", exc)
        films: list[dict] = []
        if jellyfin is not None:
            try:
                films = await jellyfin.search(query, cfg.jellyfin_limit)
            except JellyfinError as exc:
                LOG.warning("jellyfin search failed: %s", exc)
        film_slots = 1 if films else 0
        music_slots = (1 if film_slots else 2) if music else 0
        video_slots = max(cfg.page_size - 1 - music_slots - film_slots, 0)
        if invidious is not None:
            try:
                # `type=all` returns videos and channels, not playlists, so the
                # two are asked for separately.
                video_want = block * video_slots
                videos = await invidious.search(query, video_want // INVIDIOUS_PAGE + 1, "video")
                offset = video_want % INVIDIOUS_PAGE
                chunk_v = videos[offset : offset + video_slots]
                playlists = await invidious.search(query, block // INVIDIOUS_PAGE + 1, "playlist")
                playlist_offset = block % INVIDIOUS_PAGE
                chunk_p = playlists[playlist_offset : playlist_offset + 1]
                music_offset = block * music_slots
                chunk_m = music[music_offset : music_offset + music_slots]
                film_offset = block * film_slots
                chunk_f = films[film_offset : film_offset + film_slots]
                mixed = chunk_m + chunk_f
                chunk: list[dict] = []
                for i in range(max(len(chunk_v), len(mixed))):
                    if i < len(chunk_v):
                        chunk.append(chunk_v[i])
                    if i < len(mixed):
                        chunk.append(mixed[i])
                chunk += chunk_p
                more = (
                    len(videos) > offset + video_slots
                    or len(videos) >= INVIDIOUS_PAGE
                    or len(playlists) > playlist_offset + 1
                    or len(playlists) >= INVIDIOUS_PAGE
                    or len(music) > music_offset + music_slots
                    or len(films) > film_offset + film_slots
                )
                numbered, _ = page_of(chunk, 1, cfg.page_size)
                return decorate([r.as_dict() for r in numbered]), more, "invidious"
            except InvidiousError as exc:
                LOG.warning("invidious search failed, trying yt-dlp: %s", exc)
        try:
            items = await ytdlp.search(query, want // cfg.page_size + 1, cfg.page_size)
        except YtDlpError as exc:
            LOG.warning("yt-dlp search failed: %s", exc)
            return None
        chunk = items[want : want + cfg.page_size]
        numbered, _ = page_of(chunk, 1, cfg.page_size)
        return decorate([r.as_dict() for r in numbered]), len(items) >= want + cfg.page_size, "yt-dlp"

    async def contents(plid: str, want: int) -> tuple[str, list[dict], bool, str] | None:
        """(title, videos numbered from 1, another page follows, source) of a
        playlist, or None when it cannot be listed."""
        if invidious is None:
            return None
        try:
            title, items = await invidious.playlist(plid, want // INVIDIOUS_PLAYLIST_PAGE + 1)
        except InvidiousError as exc:
            LOG.warning("invidious could not list %s: %s", plid, exc)
            return None
        offset = want % INVIDIOUS_PLAYLIST_PAGE
        chunk = items[offset : offset + cfg.page_size]
        more = len(items) > offset + cfg.page_size or len(items) >= INVIDIOUS_PLAYLIST_PAGE
        numbered, _ = page_of(chunk, 1, cfg.page_size)
        return title, [r.as_dict() for r in numbered], more, "invidious"

    async def resolve(vid: str) -> tuple[str, str, str] | None:
        """(title, address, source), or None when neither source could play it.

        720p first when Invidious has the single-file one; otherwise a 720p
        remux if it can be made in time, and the 360p address if not."""
        if invidious is not None:
            try:
                title, url, itag, seconds = await invidious.stream(vid)
                if itag == "22" or remux is None or cfg.media_url_base is None:
                    return title, url, "invidious"
                if seconds is not None and seconds > remux.max_seconds:
                    LOG.info("not remuxing %s: %s s is past the limit", vid, seconds)
                    return title, url, "invidious"
                hit = remux.cached(vid)
                if hit is not None:
                    return title, media_url(hit), "remux-cache"
                pair = await invidious.dash(vid)
                if pair is None:
                    LOG.info("no 720p DASH pair for %s, playing 360p", vid)
                    return title, url, "invidious"
                try:
                    hit = await remux.make(vid, *pair)
                except RemuxError as exc:
                    LOG.info("no 720p for %s, playing 360p: %s", vid, exc)
                    return title, url, "invidious"
                return title, media_url(hit), "remux"
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
        if type(page) is not int or not 1 <= page <= MAX_SEARCH_PAGES:
            return bad("page")
        found = await search(query.strip()[:200], (page - 1) * cfg.page_size)
        if found is None:
            return web.json_response({"error": "neither Invidious nor yt-dlp could search"}, status=502)
        items, more, source = found
        # has_next stays inside the six pages; "more" says the search had more
        # than they show, so the last page can ask for a narrower request.
        return web.json_response({"items": items, "has_next": more and page < MAX_SEARCH_PAGES,
                                  "more": more, "page": page, "source": source})

    async def on_contents(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return bad("not JSON")
        if not isinstance(body, dict):
            return bad("not an object")
        raw = body.get("playlist_id")
        if not is_id(raw, "playlist"):
            return bad("playlist_id")
        page = body.get("page", 1)
        if type(page) is not int or not 1 <= page <= MAX_PAGE:
            return bad("page")
        found = await contents(raw, (page - 1) * cfg.page_size)
        if found is None:
            return web.json_response({"error": "the playlist could not be listed"}, status=502)
        title, items, more, source = found
        return web.json_response({"title": title, "items": items, "has_next": more,
                                  "page": page, "source": source})

    async def on_jelly_resolve(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return bad("not JSON")
        if not isinstance(body, dict):
            return bad("not an object")
        item_id = body.get("item_id")
        if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", item_id):
            return bad("item_id")
        if jellyfin is None:
            return web.json_response({"error": "Jellyfin is not configured"}, status=502)
        try:
            url = jellyfin.stream(item_id)
        except JellyfinError as exc:
            LOG.warning("jellyfin could not stream %s: %s", item_id, exc)
            return web.json_response({"error": "Jellyfin could not stream it"}, status=502)
        return web.json_response({"url": url})

    async def on_jelly_contents(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return bad("not JSON")
        if not isinstance(body, dict):
            return bad("not an object")
        series = body.get("series_id")
        if not isinstance(series, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", series):
            return bad("series_id")
        page = body.get("page", 1)
        if type(page) is not int or not 1 <= page <= MAX_PAGE:
            return bad("page")
        if jellyfin is None:
            return web.json_response({"error": "Jellyfin is not configured"}, status=502)
        try:
            items, more = await jellyfin.episodes(series, (page - 1) * cfg.page_size, cfg.page_size)
        except JellyfinError as exc:
            LOG.warning("jellyfin could not list %s: %s", series, exc)
            return web.json_response({"error": "Jellyfin could not list it"}, status=502)
        numbered, _ = page_of(items, 1, cfg.page_size)
        title = body.get("title") if isinstance(body.get("title"), str) else ""
        return web.json_response({"title": title, "items": decorate([r.as_dict() for r in numbered]),
                                  "has_next": more, "page": page, "source": "jellyfin"})

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
        return web.json_response({
            "ok": True,
            "source": "invidious" if invidious is not None else "yt-dlp",
            "music": ma is not None,
            "jellyfin": jellyfin is not None,
        })

    app = web.Application()
    app.router.add_get("/healthz", on_health)
    app.router.add_post("/search", on_search)
    app.router.add_post("/contents", on_contents)
    app.router.add_post("/jellyfin/resolve", on_jelly_resolve)
    app.router.add_post("/jellyfin/contents", on_jelly_contents)
    app.router.add_post("/resolve", on_resolve)
    return app


def make_media_app(cfg: Config) -> web.Application:
    """The remuxed files, for the screen only (services/yt-show.nix, Caddy).

    A file that exists is served with ranges, so the screen can seek; nothing
    else on the cache directory is reachable. The API is a separate app on the
    loopback, and never proxied."""
    cache = Path(cfg.cache_dir) if cfg.cache_dir else None

    async def media(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        if cache is None or not NAME.match(name):
            raise web.HTTPNotFound()
        path = cache / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def bat(_request: web.Request) -> web.Response:
        return web.Response(text=BAT_SVG, content_type="image/svg+xml")

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "cache": bool(cache)})

    app = web.Application()
    app.router.add_get("/media/bat.svg", bat)
    app.router.add_get("/media/{name}", media)
    app.router.add_get("/healthz", health)
    return app
