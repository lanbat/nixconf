"""Jellyfin's library: movies, series and episodes for the screen.

Search and episode listing go through Jellyfin's API with the key
jellyfin-bootstrap minted for this helper.  A title plays as a Jellyfin
transcode URL (H.264/AAC, 720p) that the Show fetches over plain HTTP from the
server's LAN address: the Caddy vhost asks for a browser login, which the Show
cannot give.

Only the video libraries are searched; music and audiobooks stay with Music
Assistant.
"""
from __future__ import annotations

import aiohttp

TYPES = "Movie,Series,Episode,Video,MusicVideo"


class JellyfinError(Exception):
    """Jellyfin did not answer, or its key is missing."""


def _seconds(ticks: object) -> int | None:
    if type(ticks) is not int or ticks <= 0:
        return None
    return int(ticks / 10_000_000)


def _item(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    item_id, name = obj.get("Id"), obj.get("Name")
    if not isinstance(item_id, str) or not item_id.strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    kind = {"Series": "series", "Episode": "episode"}.get(obj.get("Type"), "movie")
    channel = obj.get("SeriesName") if isinstance(obj.get("SeriesName"), str) else ""
    out: dict = {"source": "jellyfin", "kind": kind, "id": item_id,
                 "title": name.strip(), "channel": channel if kind == "episode" else ""}
    seconds = _seconds(obj.get("RunTimeTicks"))
    if seconds is not None:
        out["seconds"] = seconds
    if kind == "series":
        count = obj.get("ChildCount", obj.get("RecursiveItemCount"))
        if type(count) is int and count >= 0:
            out["count"] = count
    return out


class Jellyfin:
    def __init__(self, url: str, public_url: str, key_file: str,
                 session: aiohttp.ClientSession | None = None, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        # Where the Show fetches a stream: the server on the LAN, plain HTTP.
        self.public_url = public_url.rstrip("/")
        self.key_file = key_file
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

    def _key(self) -> str:
        try:
            with open(self.key_file, encoding="utf-8") as handle:
                key = handle.read().strip()
        except OSError as exc:
            raise JellyfinError(f"key: {exc}") from exc
        if not key:
            raise JellyfinError("empty key")
        return key

    async def _get(self, path: str, params: dict | None = None) -> dict:
        query = dict(params or {})
        query["api_key"] = self._key()
        session = await self._ensure()
        try:
            async with session.get(self.url + path, params=query) as resp:
                if resp.status != 200:
                    raise JellyfinError(f"{path}: HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise JellyfinError(f"{path}: {exc}") from exc
        return data if isinstance(data, dict) else {}

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        data = await self._get("/Items", {
            "searchTerm": query,
            "IncludeItemTypes": TYPES,
            "Recursive": "true",
            "Limit": limit,
            "Fields": "ProductionYear,RunTimeTicks,SeriesName,ChildCount",
        })
        out: list[dict] = []
        for obj in data.get("Items") or []:
            item = _item(obj)
            if item is not None:
                out.append(item)
        return out

    async def episodes(self, series: str, start: int, limit: int) -> tuple[list[dict], bool]:
        """(episodes, another page follows)."""
        data = await self._get(f"/Shows/{series}/Episodes", {
            "StartIndex": start,
            "Limit": limit,
            "Fields": "RunTimeTicks,SeriesName,ParentIndexNumber,IndexNumber",
        })
        out: list[dict] = []
        for obj in data.get("Items") or []:
            item = _item(obj)
            if item is None:
                continue
            if isinstance(obj, dict):
                season, episode = obj.get("ParentIndexNumber"), obj.get("IndexNumber")
                if type(season) is int and type(episode) is int:
                    where = f"S{season}E{episode}"
                    item["channel"] = f"{where} · {item['channel']}" if item["channel"] else where
            out.append(item)
        total = data.get("TotalRecordCount")
        more = type(total) is int and total > start + len(out)
        return out, more

    def stream(self, item_id: str) -> str:
        """The transcoded MP4 the screen fetches (H.264/AAC, 720p at most)."""
        key = self._key()
        return (f"{self.public_url}/Videos/{item_id}/stream.mp4"
                "?VideoCodec=h264&AudioCodec=aac&MaxHeight=720"
                "&VideoBitrate=2500000&AudioBitrate=160000"
                "&TranscodingContainer=mp4&TranscodingProtocol=http"
                f"&api_key={key}")
