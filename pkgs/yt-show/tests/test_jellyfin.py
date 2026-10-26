"""jellyfin.py: the library mapping and the stream address."""
from aiohttp import web

from yt_show.jellyfin import Jellyfin, _item


def test_a_movie_a_series_and_an_episode():
    movie = _item({"Id": "m1", "Name": "The Matrix", "Type": "Movie",
                   "RunTimeTicks": 136 * 10_000_000, "ProductionYear": 1999})
    assert movie == {"source": "jellyfin", "kind": "movie", "id": "m1",
                     "title": "The Matrix", "channel": "", "seconds": 136}
    series = _item({"Id": "s1", "Name": "The Wire", "Type": "Series", "ChildCount": 60})
    assert series["kind"] == "series" and series["count"] == 60
    episode = _item({"Id": "e1", "Name": "Pilot", "Type": "Episode",
                     "SeriesName": "The Wire", "RunTimeTicks": 59 * 10_000_000})
    assert episode["kind"] == "episode" and episode["channel"] == "The Wire"


def test_an_item_without_an_id_or_a_name_is_dropped():
    assert _item({"Id": "", "Name": "x"}) is None
    assert _item({"Id": "x", "Name": ""}) is None
    assert _item("not a dict") is None


async def _jellyfin(aiohttp_server, tmp_path, items: list[dict]) -> Jellyfin:
    """A Jellyfin answering the item lookup with the given items."""
    app = web.Application()

    async def items_route(request):
        return web.json_response({"Items": items})

    app.router.add_get("/Items", items_route)
    server = await aiohttp_server(app)
    key = tmp_path / "key"
    key.write_text("secret-key\n")
    return Jellyfin(str(server.make_url("")).rstrip("/"),
                    "http://192.168.1.10:8096", str(key))


def _source(container: str, video: str, audio: str) -> list[dict]:
    return [{"MediaSources": [{"Container": container, "MediaStreams": [
        {"Type": "Subtitle", "Codec": "subrip"},
        {"Type": "Video", "Codec": video},
        {"Type": "Audio", "Codec": audio},
    ]}]}]


async def test_a_file_the_screen_plays_is_served_as_the_file(aiohttp_server, tmp_path):
    jellyfin = await _jellyfin(aiohttp_server, tmp_path, _source("mp4", "h264", "aac"))
    url = await jellyfin.stream("abc123")
    await jellyfin.aclose()
    assert url == ("http://192.168.1.10:8096/Videos/abc123/stream.mp4"
                   "?static=true&api_key=secret-key")


async def test_anything_else_is_transcoded(aiohttp_server, tmp_path):
    jellyfin = await _jellyfin(aiohttp_server, tmp_path, _source("mkv", "hevc", "eac3"))
    url = await jellyfin.stream("abc123")
    await jellyfin.aclose()
    assert url.startswith("http://192.168.1.10:8096/Videos/abc123/stream.mp4?")
    assert "VideoCodec=h264" in url and "AudioCodec=aac" in url
    assert "MaxHeight=720" in url and "api_key=secret-key" in url


async def test_an_item_without_media_sources_is_transcoded(aiohttp_server, tmp_path):
    jellyfin = await _jellyfin(aiohttp_server, tmp_path, [])
    url = await jellyfin.stream("abc123")
    await jellyfin.aclose()
    assert "VideoCodec=h264" in url


def test_a_description_and_a_picture_from_the_library():
    item = _item({"Id": "m1", "Name": "The Matrix", "Type": "Movie",
                  "Overview": "A hacker learns the truth."},
                 "http://192.168.1.10:8096", "secret-key")
    assert item["description"] == "A hacker learns the truth."
    assert item["thumb"] == ("http://192.168.1.10:8096/Items/m1/Images/Primary"
                             "?maxWidth=480&quality=90&api_key=secret-key")
