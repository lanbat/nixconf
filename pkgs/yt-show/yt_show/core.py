"""What a YouTube address is, and which of its streams an Echo Show plays.

Pure: no network, no subprocess, so the tests need neither. The two clients
build on it — invidious.py (the LAN's Invidious) and ytdlp.py (the fallback) —
and server.py puts them in order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The Echo Show's player (docs/techo5.md, "Playing Jellyfin on the screen" and
# TECHO5's video.md) takes one MP4 with H.264 and AAC: 720p best, 360p when a
# video has no 720p. DASH pairs (video-only plus audio) are not it.
PROGRESSIVE_ITAGS = ("22", "18")

PAGE_SIZE = 5

_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")

# The forms a link to one video takes: a share, a watch link, a short, a live
# or an embed. A playlist or channel link names no single video and is not one.
_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:^|[?&])v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch/([A-Za-z0-9_-]{11})",
    )
)


def video_id(text: str) -> str | None:
    """The 11-character id in a YouTube address, or in the id itself."""
    text = (text or "").strip()
    if _ID.match(text):
        return text
    for pattern in _PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(1)
    return None


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


@dataclass(frozen=True)
class Result:
    n: int          # 1-based on the page: what the person says ("play 2")
    id: str
    title: str
    channel: str
    duration: str   # "12:34", empty when the source has none

    def as_dict(self) -> dict:
        return {"n": self.n, "id": self.id, "title": self.title,
                "channel": self.channel, "duration": self.duration}


def duration(seconds: object) -> str:
    """A YouTube duration as people read it, or "" when it is not a number."""
    try:
        total = int(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def page(items: list[dict], number: int, size: int = PAGE_SIZE) -> tuple[list[Result], bool]:
    """One page of results, numbered from 1 on the first page, and whether
    another page follows. Each item is a video: id, title, channel and
    seconds, any of which may be missing."""
    start = max(number - 1, 0) * size
    chosen = items[start : start + size]
    out: list[Result] = []
    for position, item in enumerate(chosen, start=start + 1):
        if not isinstance(item, dict):
            continue
        vid, title = item.get("id"), item.get("title")
        if not isinstance(vid, str) or not _ID.match(vid) or not isinstance(title, str):
            continue
        out.append(Result(position, vid, title.strip(), str(item.get("channel") or "").strip(),
                          duration(item.get("seconds"))))
    return out, len(items) > start + size


def pick_stream(formats: list[dict] | None) -> tuple[str, str] | None:
    """(itag, address) of the best stream the Show plays, or None when the
    video has neither 720p nor 360p in one MP4."""
    found: dict[str, str] = {}
    for fmt in formats or []:
        if not isinstance(fmt, dict):
            continue
        itag, url = str(fmt.get("itag") or ""), fmt.get("url")
        if itag in PROGRESSIVE_ITAGS and isinstance(url, str) and url and itag not in found:
            found[itag] = url
    for itag in PROGRESSIVE_ITAGS:
        if itag in found:
            return itag, found[itag]
    return None
