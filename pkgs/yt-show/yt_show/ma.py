"""Music Assistant: the audio half of the search.

Radio, tracks, albums, artists, podcasts, audiobooks and playlists come from
Music Assistant's own API (`music-assistant-client`), with the token
music-assistant-setup minted.  Playing one goes to the room's Music Assistant
player, so audio stays audio and the screen is kept for video.
"""
from __future__ import annotations

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import MusicAssistantClientException
from music_assistant_models.errors import MusicAssistantError

# (our kind, the SearchResults attribute), in the order they are interleaved.
FIELDS = (
    ("radio", "radio"),
    ("track", "tracks"),
    ("album", "albums"),
    ("artist", "artists"),
    ("podcast", "podcasts"),
    ("playlist", "playlists"),
    ("audiobook", "audiobooks"),
)


class MaError(Exception):
    """Music Assistant did not answer, or had nowhere to play it."""


def _text(item: object, *attrs: str) -> str:
    for attr in attrs:
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _seconds(item: object) -> int | None:
    value = getattr(item, "duration", None)
    return int(value) if type(value) in (int, float) and value >= 0 else None


def _count(item: object) -> int | None:
    for attr in ("track_count", "item_count"):
        value = getattr(item, attr, None)
        if type(value) is int and value >= 0:
            return value
    return None


def _item(kind: str, item: object) -> dict | None:
    """One Music Assistant media item as a result, or None when unusable."""
    uri, name = getattr(item, "uri", None), getattr(item, "name", None)
    if not isinstance(uri, str) or "://" not in uri:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    subtitle = ""
    if kind in ("track", "album"):
        artists = getattr(item, "artists", None) or []
        subtitle = ", ".join(
            artist.name for artist in artists
            if isinstance(getattr(artist, "name", None), str) and artist.name.strip())
    elif kind == "podcast":
        subtitle = _text(item, "publisher")
    elif kind == "radio":
        subtitle = _text(item, "provider", "description")
    out: dict = {"source": "music-assistant", "kind": kind, "id": uri,
                 "title": name.strip(), "channel": subtitle}
    seconds = _seconds(item)
    if seconds is not None:
        out["seconds"] = seconds
    count = _count(item)
    if count is not None:
        out["count"] = count
    return out


def _details(item: object) -> tuple[str, list[str]]:
    """(description, facts) of one full Music Assistant item: the artist, album,
    year, genre and anything else worth a line on the screen."""
    meta = getattr(item, "metadata", None)
    description = _text(meta, "description") if meta is not None else ""
    facts: list[str] = []
    artists = getattr(item, "artists", None) or []
    names = [artist.name for artist in artists
             if isinstance(getattr(artist, "name", None), str) and artist.name.strip()]
    if names:
        facts.append(", ".join(names[:2]))
    album = getattr(item, "album", None)
    if isinstance(getattr(album, "name", None), str) and album.name.strip():
        facts.append(album.name.strip())
    year = getattr(item, "year", None)
    if type(year) is not int:
        released = getattr(meta, "release_date", None) if meta is not None else None
        year = getattr(released, "year", None) if released is not None else None
    if type(year) is int:
        facts.append(str(year))
    genres = getattr(meta, "genres", None) if meta is not None else None
    if genres:
        picked = sorted({g for g in genres if isinstance(g, str) and g.strip()})[:2]
        if picked:
            facts.append(", ".join(picked))
    count = _count(item)
    if count is not None:
        facts.append(f"{count} tracks")
    if meta is not None and getattr(meta, "explicit", None) is True:
        facts.append("Explicit")
    return description[:240], facts[:5]


class Ma:
    def __init__(self, url: str, token_file: str,
                 session: aiohttp.ClientSession | None = None, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.token_file = token_file
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

    def _token(self) -> str:
        try:
            with open(self.token_file, encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError as exc:
            raise MaError(f"token: {exc}") from exc
        if not token:
            raise MaError("empty token")
        return token

    async def details(self, uris: list[str]) -> dict[str, tuple[str, list[str]]]:
        """Full items for the page: a search gives mappings without artists,
        album, year or a description."""
        if not uris:
            return {}
        session = await self._ensure()
        try:
            async with MusicAssistantClient(self.url, session, token=self._token()) as client:
                out: dict[str, tuple[str, list[str]]] = {}
                for uri in uris:
                    try:
                        item = await client.music.get_item_by_uri(uri)
                    except (MusicAssistantClientException, MusicAssistantError, OSError, TimeoutError):
                        continue
                    out[uri] = _details(item)
                return out
        except (MusicAssistantClientException, MusicAssistantError, OSError, TimeoutError) as exc:
            raise MaError(str(exc)) from exc

    async def search(self, query: str, limit: int = 40) -> list[dict]:
        """Every kind of media, interleaved, up to `limit` of each kind."""
        session = await self._ensure()
        try:
            async with MusicAssistantClient(self.url, session, token=self._token()) as client:
                results = await client.music.search(query, limit=limit)
                pools = [list(getattr(results, attr) or []) for _, attr in FIELDS]
                out: list[dict] = []
                index = 0
                while True:
                    added = False
                    for (kind, _), pool in zip(FIELDS, pools):
                        if index >= len(pool):
                            continue
                        added = True
                        item = _item(kind, pool[index])
                        if item is not None:
                            out.append(item)
                    if not added:
                        break
                    index += 1
                return out
        except (MusicAssistantClientException, MusicAssistantError, OSError, TimeoutError) as exc:
            # A slow or unhappy Music Assistant must not take the search down:
            # the page is then just YouTube and Jellyfin.
            raise MaError(str(exc)) from exc
