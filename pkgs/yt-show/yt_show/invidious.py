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

from typing import Any

import aiohttp

from .core import pick_stream


class InvidiousError(Exception):
    """Invidious did not answer, or answered with something unusable."""


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

    async def search(self, query: str, page: int) -> list[dict]:
        """One Invidious page (20 videos) of a search."""
        data = await self._get("/api/v1/search", {"q": query, "page": page, "type": "video"})
        out: list[dict] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or item.get("type") not in (None, "video"):
                continue
            vid = item.get("videoId")
            if not isinstance(vid, str):
                continue
            out.append({
                "id": vid,
                "title": item.get("title") if isinstance(item.get("title"), str) else "",
                "channel": item.get("author") if isinstance(item.get("author"), str) else "",
                "seconds": item.get("lengthSeconds"),
            })
        return out

    async def stream(self, vid: str) -> tuple[str, str]:
        """(title, address) of the video's best muxed stream."""
        data = await self._get(f"/api/v1/videos/{vid}", {"fields": "title,formatStreams"})
        if not isinstance(data, dict):
            raise InvidiousError("video: not an object")
        picked = pick_stream(data.get("formatStreams"))
        if picked is None:
            raise InvidiousError("video: no 720p or 360p MP4")
        title = data.get("title") if isinstance(data.get("title"), str) else ""
        return title, self.url(vid, picked[0])

    def url(self, vid: str, itag: str) -> str:
        """The proxied stream (local=true), on the address the Show fetches: its
        own domain, so the certificate check passes and nothing expires."""
        return f"{self.public}/latest_version?id={vid}&itag={itag}&local=true"
