"""YouTube on a room's screen: the list a person picks from, and the two tools
the cloud model calls to fill it or play from it.

The helper (pkgs/yt-show) knows YouTube; this knows the conversation and Home
Assistant: which room's list is open, what "play 2" and "next page" mean, and
the play_video action on the room's Echo Show (docs/techo5.md). The list is
kept here, in memory: it is the conversation's state, not the helper's, and it
outlives neither the router nor `LIST_SECONDS`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import aiohttp

from .hass import Hass, HassError

PAGE_SIZE = 5
LIST_SECONDS = 600.0

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_media",
        "description": (
            "Find something to play and show it, numbered, on a room's Echo Show: videos and "
            "playlists from YouTube, and radio stations, tracks, albums, artists, podcasts and "
            "playlists from Music Assistant. Use it when someone asks to watch, play or listen "
            "to something they name. The person then says which number to play."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "page": {"type": "integer",
                         "description": "1 for a new search, 2 and up for the next pages of the same search."},
                "room": {"type": "string",
                         "description": "The room whose screen shows it. Leave it out for the "
                                        "speaker's room, or when there is only one screen."},
            },
            "required": ["query"],
        },
    },
}

PLAY_TOOL = {
    "type": "function",
    "function": {
        "name": "play_media",
        "description": (
            "Play the item someone chose from search_media: a video or a playlist on the Echo "
            "Show's screen, or music, radio, a podcast or an audiobook on the room's Music "
            "Assistant speaker. Also for \"play two\", \"the second one\", \"next page\", "
            "\"go back\", and for a YouTube address they gave."),
        "parameters": {
            "type": "object",
            "properties": {
                "selection": {"type": "string",
                              "description": "The result's number (1-5), \"first\", \"last\", "
                                             "\"next page\", \"previous page\", \"back\", or a "
                                             "YouTube address or id."},
                "room": {"type": "string",
                         "description": "The room whose screen or speaker plays it. Leave it out "
                                        "for the speaker's room, or when there is only one screen."},
            },
            "required": ["selection"],
        },
    },
}

TOOL_NAMES = (SEARCH_TOOL["function"]["name"], PLAY_TOOL["function"]["name"])


def _key(room: str) -> str:
    return (room or "").strip().casefold()


@dataclass(frozen=True)
class Results:
    query: str
    page: int
    items: tuple[dict, ...]  # as yt-show numbered them: n, kind, id, title, duration/count
    has_next: bool
    at: float
    playlist: str | None = None  # set when this list is a playlist's videos

    def item(self, n: int) -> dict | None:
        """The nth result, counting from 1; -1 is the last one."""
        if n == -1 and self.items:
            return self.items[-1]
        return self.items[n - 1] if 1 <= n <= len(self.items) else None


class Sessions:
    """The open list in each room — a stack, so a playlist's videos sit over
    the search that found it — forgotten after a while."""

    def __init__(self, seconds: float = LIST_SECONDS, clock=time.monotonic) -> None:
        self._seconds = seconds
        self._clock = clock
        self._rooms: dict[str, list[Results]] = {}

    def set(self, room: str, results: Results) -> None:
        """A new search: it replaces whatever was open."""
        self._rooms[_key(room)] = [results]

    def push(self, room: str, results: Results) -> None:
        """A list opened from the current one (a playlist's videos)."""
        self._rooms.setdefault(_key(room), []).append(results)

    def replace(self, room: str, results: Results) -> None:
        """Another page of the list already open."""
        stack = self._rooms.get(_key(room))
        if stack:
            stack[-1] = results
        else:
            self._rooms[_key(room)] = [results]

    def get(self, room: str) -> Results | None:
        stack = self._rooms.get(_key(room))
        if not stack:
            return None
        hit = stack[-1]
        if self._clock() - hit.at > self._seconds:
            del self._rooms[_key(room)]
            return None
        return hit

    def pop(self, room: str) -> bool:
        """Back to the list below, if there is one."""
        stack = self._rooms.get(_key(room))
        if not stack or len(stack) < 2:
            return False
        stack.pop()
        return True

    def has(self, room: str) -> bool:
        return self.get(room) is not None

    def clear(self, room: str) -> None:
        self._rooms.pop(_key(room), None)


class YtShow:
    """The yt-show helper on the loopback (services/yt-show.nix)."""

    def __init__(self, base: str, session: aiohttp.ClientSession | None = None,
                 timeout: float = 60.0) -> None:
        self.base = base.rstrip("/")
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

    async def _post(self, path: str, payload: dict) -> dict | None:
        session = await self._ensure()
        try:
            async with session.post(self.base + path, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def search(self, query: str, page: int) -> dict | None:
        data = await self._post("/search", {"query": query, "page": page})
        if data is None or not isinstance(data.get("items"), list):
            return None
        return data

    async def contents(self, playlist: str, page: int) -> dict | None:
        """A playlist's videos, numbered from 1 on each page."""
        data = await self._post("/contents", {"playlist_id": playlist, "page": page})
        if data is None or not isinstance(data.get("items"), list):
            return None
        return data

    async def resolve(self, video: str) -> dict | None:
        """The playable address of a video, by id or link."""
        data = await self._post("/resolve", {"video_id": video})
        if data is None or not isinstance(data.get("url"), str) or not data["url"]:
            return None
        return data



def _clip(text: object, width: int) -> str:
    said = " ".join(str(text or "").split())
    return said if len(said) <= width else said[: max(width - 3, 0)].rstrip() + "..."


def _tag(item: dict) -> str:
    kind = item.get("kind")
    if kind in (None, "video"):
        return ""
    if kind == "playlist" and item.get("source") == "music-assistant":
        return "[music playlist] "
    return f"[{kind}] "


def _line(item: dict, width: int) -> str:
    count = item.get("count")
    more = f" ({count})" if isinstance(count, int) else ""
    return f"{item['n']}. {_tag(item)}{_clip(item['title'], width)}{more}"


def caption(results: Results) -> str:
    """The list on the screen, in one caption (the bridge truncates at 200,
    pkgs/techo5-bridge/brain.py). Narrower titles until it fits. Plain ASCII:
    the screen draws its own font, which need not carry a middle dot."""
    hint = "   say 'next page'" if results.has_next else ""
    width = 30
    while width >= 10:
        text = "   ".join(_line(item, width) for item in results.items) + hint
        if len(text) <= 200:
            return text
        width -= 4
    return ("   ".join(_line(item, 10) for item in results.items) + hint)[:200]


def spoken(results: Results) -> str:
    """What the assistant reads out: the list, and what to say next."""
    parts = []
    for item in results.items:
        title = _clip(item["title"], 60)
        kind = item.get("kind") or "video"
        if kind == "video":
            parts.append(f"{item['n']}, {title}")
        elif kind == "playlist":
            label = "music playlist" if item.get("source") == "music-assistant" else "playlist"
            unit = "items" if item.get("source") == "music-assistant" else "videos"
            count = item.get("count")
            parts.append(f"{item['n']}, {label}, {title}"
                         + (f", {count} {unit}" if isinstance(count, int) else ""))
        else:
            parts.append(f"{item['n']}, {kind}, {title}")
    said = "; ".join(parts)
    next_step = "Say the number to play, or next page." if results.has_next else "Which number?"
    return f"Here are {len(results.items)} for {_clip(results.query, 40)}: {said}. {next_step}"


_WORD_NUMBERS = {
    "first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3, "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5, "last": -1,
}
_WHAT = (r"first|second|third|fourth|fifth|last|one|two|three|four|five"
         r"|1st|2nd|3rd|4th|5th|\d+")
_PLAY = re.compile(
    r"^(?:please\s+)?(?:play|put on|watch|start|show)\s+"
    r"(?:(?:number|option|result|the)\s+)*"
    rf"(?P<what>{_WHAT})(?:\s+(?:one|video|clip))?$"
)
_NUMBER = re.compile(
    r"^(?:the\s+)?(?:(?:number|option|result)\s+)?"
    rf"(?P<what>{_WHAT})(?:\s+(?:one|video|clip))?$"
)
_NEXT = re.compile(r"^(?:next|more)(?:\s+(?:page|results))?$|^page\s+down$")
_PREV = re.compile(r"^(?:previous|back)(?:\s+(?:a\s+)?page)?$|^page\s+up$")
_BACK = re.compile(r"^(?:back to (?:the )?(?:results|list|search)|go back|back out)$")
_PAGE = re.compile(r"^(?:page|results)\s+(?P<n>\d+)$")
_CANCEL = re.compile(r"^(?:cancel|never\s+mind|forget\s+it|stop|close)$")
_RAW = re.compile(r"(?:youtu\.be/|youtube\.com/|[A-Za-z0-9_-]{11}$)")


def control(text: str) -> tuple[str, int | None] | None:
    """What can be said next about an open list: ("play", n), ("page", n),
    ("next", None), ("prev", None) or ("cancel", None). None when it is
    something else, which then goes the usual way."""
    said = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())
    said = re.sub(r"\s+", " ", said).strip()
    if not said:
        return None
    if _CANCEL.match(said):
        return ("cancel", None)
    if _NEXT.match(said):
        return ("next", None)
    if _BACK.match(said):
        return ("back", None)
    if _PREV.match(said):
        return ("prev", None)
    found = _PAGE.match(said)
    if found:
        return ("page", int(found.group("n")))
    found = _PLAY.match(said) or _NUMBER.match(said)
    if found:
        what = found.group("what")
        return ("play", _WORD_NUMBERS.get(what, int(what) if what.isdigit() else 0))
    return None


def interpret(value: str) -> tuple[str, int | str | None] | None:
    """A tool argument: a control, or a link or id to play as it is."""
    hit = control(value)
    if hit is not None:
        return hit
    text = (value or "").strip()
    return ("raw", text) if text and _RAW.search(text) else None


_EMPTY = {"it", "that", "this", "again", "please", "now", "the", "a", "some", "on", "screen"}
_MUSIC = {"music", "radio", "song", "songs", "playlist", "album", "albums", "podcast",
          "podcasts", "station", "stations"}
_VERB = re.compile(r"\b(?:play|watch|put on)\b")


def plays_something(text: str) -> bool:
    """Whether "play", "watch" or "put on" names something to look for, rather
    than resuming what was playing or asking for music. A screen in the house
    makes the cloud decide (it has the search tool); without one, the local
    model resumes."""
    found = _VERB.search((text or "").lower())
    if found is None:
        return False
    rest = [word for word in re.findall(r"[a-z0-9']+", (text or "").lower()[found.end():])
            if word not in _EMPTY]
    return bool(rest) and not all(word in _MUSIC for word in rest)


async def audio_player(hass: Hass, room: str, players: dict[str, str]) -> str | None:
    """The room's Music Assistant speaker, by the name its profile gave it."""
    name = players.get((room or "").strip().casefold())
    if not name:
        return None
    try:
        states = await hass.states("media_player")
    except HassError:
        return None
    for state in states:
        if state["name"].casefold() == name.casefold():
            return state["entity_id"]
    return None


async def play_service(hass: Hass, room: str, override: str | None = None) -> tuple[str, str] | None:
    """The esphome.<node>_play_video action of the room's Show, if it can be
    told apart: one play_video service on Home Assistant, or one whose name
    holds the room."""
    if override:
        domain, _, service = override.partition(".")
        return (domain, service) if domain and service else None
    try:
        keys = await hass.services("esphome")
    except HassError:
        return None
    plays = [key for key in keys if key.endswith("_play_video")]
    if len(plays) == 1:
        return "esphome", plays[0]
    slug = re.sub(r"[^a-z0-9]+", "_", (room or "").casefold()).strip("_")
    matched = [key for key in plays if slug and slug in key]
    return ("esphome", matched[0]) if len(matched) == 1 else None
