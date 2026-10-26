"""Invidious on the LAN: search, and the muxed MP4 for one video.

Invidious is preferred over yt-dlp because the address it hands out is on our
own domain (services/invidious.nix): the Echo Show already trusts that
certificate, the address does not expire like a googlevideo one, and the
traffic never leaves the house. Its API is the YouTube Data API's shape.

It is not reliable on its own — YouTube changes its client checks and Invidious
falls behind (see the comment at the top of services/invidious.nix) — so every
call here may raise InvidiousError and the caller falls back to ytdlp.py.
"""
from __future__ import annotations

import re
from typing import Any

import aiohttp

from .core import pick_stream


class InvidiousError(Exception):
    """Invidious did not answer, or answered with something unusable."""


def _short(value: object) -> str:
    """A count as people read it: 6241603 -> 6.2M."""
    if type(value) is not int or value < 0:
        return ""
    if value >= 1_000_000_000:
        text = f"{value / 1_000_000_000:.1f}B"
    elif value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}M"
    elif value >= 1_000:
        text = f"{value / 1_000:.1f}K"
    else:
        return str(value)
    return text[:-2] if text.endswith(".0M") or text.endswith(".0K") or text.endswith(".0B") else text


def _plain(text: object, limit: int = 240) -> str:
    """A description as the screen reads it: no tags, one paragraph, short."""
    if not isinstance(text, str):
        return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "…"


class Invidious:
    def __init__(self, base: str, public: str | None = None,
                 session: aiohttp.ClientSession | None = None, timeout: float = 10.0) -> None:
        # base: where this host reaches Invidious (the loopback, when it is
        # local). public: where the Show reaches it — its own domain, which the
        # Show trusts and can resolve; the stream address must use that one.
        self.base = base.rstrip("/")
        self.public = (public or base).rstrip("/")
        self._session = session
        self._owns = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def aclose(self) -> None:
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, params: dict | None = None) -> Any:
        session = await self._ensure()
        try:
            async with session.get(self.base + path, params=params) as resp:
                if resp.status != 200:
                    raise InvidiousError(f"{path}: HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise InvidiousError(f"{path}: {exc}") from exc

    async def search(self, query: str, page: int, kind: str = "video") -> list[dict]:
        """One Invidious page (20) of one kind: videos or playlists.

        `type=all` returns videos and channels, not playlists, so the two are
        asked for separately (server.search interleaves them)."""
        data = await self._get("/api/v1/search", {
            "q": query, "page": page, "type": "playlist" if kind == "playlist" else "video"})
        out: list[dict] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            if kind == "playlist":
                if item.get("type") != "playlist":
                    continue
                plid = item.get("playlistId")
                if not isinstance(plid, str):
                    continue
                count = item.get("videoCount")
                out.append({
                    "source": "youtube",
                    "kind": "playlist",
                    "id": plid,
                    "title": item.get("title") if isinstance(item.get("title"), str) else "",
                    "channel": item.get("author") if isinstance(item.get("author"), str) else "",
                    "count": count if type(count) is int and count >= 0 else None,
                })
            else:
                if item.get("type") not in (None, "video"):
                    continue
                vid = item.get("videoId")
                if not isinstance(vid, str):
                    continue
                out.append({
                    "source": "youtube",
                    "kind": "video",
                    "id": vid,
                    "title": item.get("title") if isinstance(item.get("title"), str) else "",
                    "channel": item.get("author") if isinstance(item.get("author"), str) else "",
                    "seconds": item.get("lengthSeconds"),
                    # Invidious serves the thumbnail itself, on the domain the
                    # Show trusts, rather than i.ytimg.com.
                    "thumb": f"{self.public}/vi/{vid}/mqdefault.jpg",
                    "description": _plain(item.get("description")),
                })
        return out

    async def details(self, vid: str) -> tuple[str, list[str]]:
        """(description, facts) of one video: views, likes and when it went up."""
        try:
            data = await self._get(f"/api/v1/videos/{vid}")
        except InvidiousError:
            return "", []
        if not isinstance(data, dict):
            return "", []
        facts = []
        for key, label in (("viewCount", "views"), ("likeCount", "likes")):
            short = _short(data.get(key))
            if short:
                facts.append(f"{short} {label}")
        published = data.get("publishedText")
        if isinstance(published, str) and published.strip():
            facts.append(published.strip())
        return _plain(data.get("description")), facts

    async def playlist(self, plid: str, page: int) -> tuple[str, list[dict]]:
        """(title, videos) of one Invidious page (100 videos) of a playlist."""
        data = await self._get(f"/api/v1/playlists/{plid}", {"page": page})
        if not isinstance(data, dict):
            raise InvidiousError("playlist: not an object")
        title = data.get("title") if isinstance(data.get("title"), str) else ""
        out: list[dict] = []
        for item in data.get("videos") or []:
            if not isinstance(item, dict):
                continue
            vid = item.get("videoId")
            if not isinstance(vid, str):
                continue
            out.append({
                "source": "youtube",
                "kind": "video",
                "id": vid,
                "title": item.get("title") if isinstance(item.get("title"), str) else "",
                "channel": item.get("author") if isinstance(item.get("author"), str) else "",
                "seconds": item.get("lengthSeconds"),
                "thumb": f"{self.public}/vi/{vid}/mqdefault.jpg",
                "description": _plain(item.get("description")),
            })
        return title, out

    async def stream(self, vid: str) -> tuple[str, str, str, int | None]:
        """(title, address, itag, seconds) of the video's best muxed stream.

        The itag says whether it is the 720p one (22) or the 360p fallback (18),
        so the caller can try to remux a better one (remux.py)."""
        data = await self._get(f"/api/v1/videos/{vid}",
                               {"fields": "title,lengthSeconds,formatStreams"})
        if not isinstance(data, dict):
            raise InvidiousError("video: not an object")
        picked = pick_stream(data.get("formatStreams"))
        if picked is None:
            raise InvidiousError("video: no 720p or 360p MP4")
        title = data.get("title") if isinstance(data.get("title"), str) else ""
        seconds = data.get("lengthSeconds")
        return (title, self.url(vid, picked[0]), picked[0],
                seconds if type(seconds) is int and seconds >= 0 else None)

    async def dash(self, vid: str) -> tuple[str, str] | None:
        """(video address, audio address) of a 720p AVC + m4a pair, or None.

        Both are Invidious' proxied addresses, so Invidious companion mints the
        PO token: a direct googlevideo download is refused (403) without one.
        The pair is remuxed into one MP4 by remux.py."""
        data = await self._get(f"/api/v1/videos/{vid}", {"fields": "adaptiveFormats"})
        if not isinstance(data, dict):
            raise InvidiousError("video: not an object")
        formats = {
            str(fmt.get("itag")): fmt
            for fmt in (data.get("adaptiveFormats") or [])
            if isinstance(fmt, dict) and fmt.get("itag") is not None
        }
        # 720p AVC, then 480p; m4a audio, then its low-bitrate sibling. The Show
        # plays AVC/AAC in MP4 and nothing here is re-encoded.
        video = next((itag for itag in ("136", "135") if itag in formats), None)
        audio = next((itag for itag in ("140", "139") if itag in formats), None)
        if video is None or audio is None:
            return None
        return self.url(vid, video), self.url(vid, audio)

    def url(self, vid: str, itag: str) -> str:
        """The proxied stream (local=true), on the address the Show fetches: its
        own domain, so the certificate check passes and nothing expires."""
        return f"{self.public}/latest_version?id={vid}&itag={itag}&local=true"
