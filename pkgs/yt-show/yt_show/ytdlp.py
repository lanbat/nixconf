"""yt-dlp, when Invidious is off, unreachable or cannot play a video.

It reaches YouTube itself, so it needs the internet; the service that runs it
(services/yt-show.nix) is the only part of the path that does. `-g` prints the
address without downloading, and 22/18 are the single-file H.264 MP4s the Show
plays (core.py).
"""
from __future__ import annotations

import asyncio
import json

from .core import watch_url


class YtDlpError(Exception):
    """yt-dlp failed, timed out, or answered with something unusable."""


class YtDlp:
    def __init__(self, exe: str = "yt-dlp", timeout: float = 45.0) -> None:
        self.exe = exe
        self.timeout = timeout

    async def _run(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.exe, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise YtDlpError("timed out") from None
        if proc.returncode != 0:
            detail = (err or b"").decode("utf-8", "replace").strip()[:200]
            raise YtDlpError(detail or f"exit {proc.returncode}")
        return (out or b"").decode("utf-8", "replace").strip()

    async def search(self, query: str, page: int, size: int) -> list[dict]:
        """The first `page * size` results (ytsearch screens by count, not by
        page), so the caller can take the page it wants."""
        count = page * size
        raw = await self._run("--dump-single-json", "--flat-playlist", "--no-warnings",
                              "--no-cache-dir", f"ytsearch{count}:{query}")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise YtDlpError("search: not JSON") from exc
        entries = data.get("entries") if isinstance(data, dict) else None
        out: list[dict] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            out.append({
                "kind": "video",
                "id": entry["id"],
                "title": entry.get("title") if isinstance(entry.get("title"), str) else "",
                "channel": entry.get("channel") or entry.get("uploader") or "",
                "seconds": entry.get("duration"),
            })
        return out

    async def stream(self, vid: str) -> tuple[str, str]:
        """(title, address) of the video's best muxed stream."""
        url = watch_url(vid)
        title = await self._run("--print", "title", "--no-warnings", "--no-cache-dir", url)
        printed = await self._run("-f", "22/18", "-g", "--no-warnings", "--no-cache-dir", url)
        addresses = [line.strip() for line in printed.splitlines() if line.strip()]
        if not addresses:
            raise YtDlpError("video: no 720p or 360p MP4")
        return (title.splitlines()[0].strip() if title else ""), addresses[0]
