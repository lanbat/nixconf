"""Jellyfin's library: movies, series and episodes for the screen.

Search and episode listing go through Jellyfin's API with the key
jellyfin-bootstrap minted for this helper.  A title plays as a Jellyfin URL on
the server's LAN address that the Show fetches over plain HTTP: the Caddy vhost
asks for a browser login, which the Show cannot give.  A file the Show decodes
itself (MP4, H.264, AAC) is served as the file itself; anything else as a
transcode (see stream()).

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


def _quality(sources: object) -> str:
    """The best resolution/HDR a Jellyfin item's media sources name."""
    if not isinstance(sources, list) or not sources or not isinstance(sources[0], dict):
        return ""
    source = sources[0]
    height = source.get("Height")
    label = ""
    if type(height) is int:
        label = "4K" if height >= 2000 else "1080p" if height >= 1000 else "720p" if height >= 700 else ""
    video_range = source.get("VideoRange") if isinstance(source.get("VideoRange"), str) else ""
    if "DOVI" in video_range.upper():
        label = f"{label} Dolby Vision".strip()
    elif "HDR" in video_range.upper():
        label = f"{label} HDR".strip()
    return label


def _facts(obj: dict) -> list[str]:
    facts = []
    rating = obj.get("CommunityRating")
    if type(rating) in (int, float):
        facts.append(f"★ {rating:.1f}")
    official = obj.get("OfficialRating")
    if isinstance(official, str) and official.strip():
        facts.append(official.strip()[:12])
    year = obj.get("ProductionYear")
    if type(year) is int:
        facts.append(str(year))
    quality = _quality(obj.get("MediaSources"))
    if quality:
        facts.append(quality)
    return facts[:4]


def _text(value: object, limit: int = 240) -> str:
    """A description as the screen reads it: one paragraph, short."""
    if not isinstance(value, str):
        return ""
    plain = " ".join(value.split())
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "…"


def _item(obj: object, thumb_base: str = "", key: str = "") -> dict | None:
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
    description = _text(obj.get("Overview"), limit=240)
    if description:
        out["description"] = description
    facts = _facts(obj)
    if facts:
        out["facts"] = facts
    if thumb_base:
        out["thumb"] = (f"{thumb_base}/Items/{item_id}/Images/Primary"
                        f"?maxWidth=480&quality=90&api_key={key}")
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
            "Fields": "ProductionYear,RunTimeTicks,SeriesName,ChildCount,Overview,CommunityRating,OfficialRating,MediaSources",
        })
        thumb_base, key = self.public_url, self._key()
        out: list[dict] = []
        for obj in data.get("Items") or []:
            item = _item(obj, thumb_base, key)
            if item is not None:
                out.append(item)
        return out

    async def episodes(self, series: str, start: int, limit: int) -> tuple[list[dict], bool]:
        """(episodes, another page follows)."""
        data = await self._get(f"/Shows/{series}/Episodes", {
            "StartIndex": start,
            "Limit": limit,
            "Fields": "RunTimeTicks,SeriesName,ParentIndexNumber,IndexNumber,Overview,CommunityRating,OfficialRating,ProductionYear,MediaSources",
        })
        thumb_base, key = self.public_url, self._key()
        out: list[dict] = []
        for obj in data.get("Items") or []:
            item = _item(obj, thumb_base, key)
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

    async def stream(self, item_id: str) -> str:
        """The MP4 the screen fetches: the file itself when it can play it.

        The file, not a transcode, for an MP4 with H.264 video and AAC audio.
        The screen fetches in chunks and closes the connection between them,
        and Jellyfin kills a progressive transcode whose client has not read
        for ten seconds (its kill timer for progressive jobs), deleting the
        MP4 written so far; the screen then plays out its buffer and stops.
        A static stream is a plain file with Range support, which it plays
        like any downloaded MP4.

        Anything else (HEVC, AV1, another container) still goes through the
        transcode: the screen can't decode it, and that path stalls the same
        way. The lasting fix is a pre-transcoded copy, as for YouTube's remux.
        """
        key = self._key()
        if await self._plays_statically(item_id):
            return (f"{self.public_url}/Videos/{item_id}/stream.mp4"
                    f"?static=true&api_key={key}")
        return (f"{self.public_url}/Videos/{item_id}/stream.mp4"
                "?VideoCodec=h264&AudioCodec=aac&MaxHeight=720"
                "&VideoBitrate=2500000&AudioBitrate=160000"
                "&TranscodingContainer=mp4&TranscodingProtocol=http"
                f"&api_key={key}")

    async def _plays_statically(self, item_id: str) -> bool:
        """MP4 with H.264 video and AAC audio: what the screen decodes itself."""
        data = await self._get("/Items", {
            "Ids": item_id,
            "Recursive": "true",
            "Fields": "MediaSources",
        })
        items = data.get("Items")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return False
        sources = items[0].get("MediaSources")
        if not isinstance(sources, list) or not sources or not isinstance(sources[0], dict):
            return False
        source = sources[0]
        streams = source.get("MediaStreams")
        if not isinstance(streams, list):
            return False
        video = next((s for s in streams if isinstance(s, dict) and s.get("Type") == "Video"), {})
        audio = next((s for s in streams if isinstance(s, dict) and s.get("Type") == "Audio"), {})
        return (str(source.get("Container") or "").lower() == "mp4"
                and str(video.get("Codec") or "").lower() == "h264"
                and str(audio.get("Codec") or "").lower() == "aac")
