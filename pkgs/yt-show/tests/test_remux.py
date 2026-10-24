"""remux.py: the 720p file from a DASH pair, and the app that serves it."""
import os
import sys
import time

import pytest

from yt_show.remux import Remux, RemuxError
from yt_show.server import Config, make_media_app

VID = "dQw4w9WgXcQ"
BODY = b"0123456789"


def fake_ffmpeg(tmp_path, fail=False):
    script = tmp_path / "ffmpeg"
    script.write_text(f"""#!{sys.executable}
import pathlib, sys
if {"True" if fail else "False"}:
    print("ERROR: no such stream", file=sys.stderr)
    sys.exit(1)
pathlib.Path(sys.argv[-1]).write_bytes({BODY!r})
""")
    script.chmod(0o755)
    return script


async def test_a_720p_pair_is_joined_into_the_cache(tmp_path):
    cache = tmp_path / "cache"
    remux = Remux(str(cache), str(fake_ffmpeg(tmp_path)), timeout=5)
    path = await remux.make(VID, "https://video.example/136", "https://audio.example/140")
    assert path == cache / f"{VID}.mp4"
    assert path.read_bytes() == BODY
    assert remux.cached(VID) == path


async def test_a_failed_ffmpeg_leaves_no_file(tmp_path):
    remux = Remux(str(tmp_path / "cache"), str(fake_ffmpeg(tmp_path, fail=True)), timeout=5)
    with pytest.raises(RemuxError):
        await remux.make(VID, "https://video.example/136", "https://audio.example/140")
    assert remux.cached(VID) is None
    assert list((tmp_path / "cache").iterdir()) == []


def test_pruning_drops_old_files_and_then_the_oldest_until_it_fits(tmp_path):
    remux = Remux(str(tmp_path), "yt-dlp", cache_days=7, max_cache_mb=0.0015)  # ~1.5 KB
    now = time.time()
    old = tmp_path / "AAAAAAAAAAA.mp4"
    old.write_bytes(b"x" * 10)
    os.utime(old, (0, 0))
    first = tmp_path / "BBBBBBBBBBB.mp4"
    first.write_bytes(b"x" * 1000)
    os.utime(first, (now - 100, now - 100))
    newest = tmp_path / "CCCCCCCCCCC.mp4"
    newest.write_bytes(b"x" * 1000)
    os.utime(newest, (now, now))
    remux.prune()
    assert not old.exists()  # past its age
    assert not first.exists()  # the cache would be over its limit
    assert newest.exists()


async def test_the_media_app_serves_only_whole_names_with_ranges(aiohttp_client, tmp_path):
    (tmp_path / f"{VID}.mp4").write_bytes(BODY)
    cfg = Config(cache_dir=str(tmp_path), media_url_base="https://yt.example")
    client = await aiohttp_client(make_media_app(cfg))

    whole = await client.get(f"/media/{VID}.mp4")
    assert whole.status == 200 and await whole.read() == BODY

    ranged = await client.get(f"/media/{VID}.mp4", headers={"Range": "bytes=0-3"})
    assert ranged.status == 206 and await ranged.read() == b"0123"

    assert (await client.get("/media/not-a-video.mp4")).status == 404
    assert (await client.get(f"/media/{VID}.mkv")).status == 404
    assert (await client.get(f"/media/{VID}.mp4?x=1")).status == 200
