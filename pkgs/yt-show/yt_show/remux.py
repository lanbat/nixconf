"""A 720p MP4 for a video Invidious only has as 360p.

YouTube's single-file 720p stream (itag 22) is missing from many videos now;
Invidious then offers the 360p one (itag 18). The 720p AVC video (itag 136)
and m4a audio (itag 140) are still there as DASH streams, and Invidious
companion proxies them with the PO token a direct download needs. ffmpeg joins
the two into one MP4 without re-encoding; this keeps that file in a cache the
media app (server.make_media_app) serves to the screen.

Bounded on purpose: a long video is not worth a minute of silence at the
screen, and the cache is not allowed to grow without limit. Every failure is
RemuxError, and the caller plays the 360p address instead.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9_-]{11}\.(mp4|mkv)$")


class RemuxError(Exception):
    """The 720p file could not be made: a timeout, or ffmpeg failed."""


class Remux:
    def __init__(self, cache_dir: str, ffmpeg: str, timeout: float = 30.0,
                 max_minutes: float = 15.0, cache_days: float = 7.0,
                 max_cache_mb: float = 4096.0) -> None:
        self.dir = Path(cache_dir)
        self.ffmpeg = ffmpeg
        self.timeout = timeout
        self.max_seconds = max_minutes * 60
        self.cache_days = cache_days
        self.max_cache_bytes = int(max_cache_mb * 1024 * 1024)

    def cached(self, vid: str) -> Path | None:
        """The file if it is already there, whole."""
        for ext in ("mp4", "mkv"):
            path = self.dir / f"{vid}.{ext}"
            if path.is_file() and path.stat().st_size > 0:
                return path
        return None

    async def make(self, vid: str, video_url: str, audio_url: str) -> Path:
        """Download both streams and join them, then return the file."""
        hit = self.cached(vid)
        if hit is not None:
            return hit
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RemuxError(f"cache directory: {exc}") from exc
        final = self.dir / f"{vid}.mp4"
        # A dot name is not something the media app or prune touches, so a
        # half-written file is never served.
        part = self.dir / f".{vid}.part.mp4"
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", video_url, "-i", audio_url,
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
            "-movflags", "+faststart", "-f", "mp4", str(part),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            part.unlink(missing_ok=True)
            raise RemuxError("timed out") from None
        if proc.returncode != 0 or not part.is_file():
            detail = (err or b"").decode("utf-8", "replace").strip()[:200]
            part.unlink(missing_ok=True)
            raise RemuxError(detail or f"exit {proc.returncode}")
        try:
            os.replace(part, final)
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise RemuxError(f"rename: {exc}") from exc
        self.prune()
        return final

    def prune(self) -> None:
        """Drop files past their age, then the oldest until the cache fits."""
        try:
            files = [p for p in self.dir.iterdir() if p.is_file() and NAME.match(p.name)]
            files.sort(key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        now = time.time()
        kept = []
        for path in files:
            if now - path.stat().st_mtime > self.cache_days * 86400:
                path.unlink(missing_ok=True)
            else:
                kept.append(path)
        total = sum(p.stat().st_size for p in kept)
        for path in kept:
            if total <= self.max_cache_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size
