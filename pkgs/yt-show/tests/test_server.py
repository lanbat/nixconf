"""server.py: Invidious first, yt-dlp second, and one page at a time."""
import pytest

from yt_show.invidious import InvidiousError
from yt_show.server import Config, make_app
from yt_show.ytdlp import YtDlpError

ITEMS = [
    {"kind": "video", "id": f"vid{i:08d}", "title": f"Title {i}", "channel": "A channel", "seconds": 60 + i}
    for i in range(20)
]
PLID = "PLabcdefghijklmnopqrstuvwx"
PLAYLISTS = [
    {"kind": "playlist", "id": f"PLplaylist{i:013d}", "title": f"Playlist {i}",
     "channel": "C", "count": 100 + i}
    for i in range(20)
]


class FakeInvidious:
    def __init__(self, fail=False, pages=None, itag="22", seconds=60):
        self.fail = fail
        self.pages = pages
        self.itag = itag
        self.seconds = seconds
        self.calls = []

    async def search(self, query, page, kind="video"):
        self.calls.append(("search", query, page, kind))
        if self.fail:
            raise InvidiousError("down")
        if self.pages is not None:
            return self.pages.get((page, kind), [])
        return ITEMS if kind == "video" else PLAYLISTS

    async def stream(self, vid):
        self.calls.append(("stream", vid))
        if self.fail:
            raise InvidiousError("down")
        if vid == "NoMuxedAAA1":
            raise InvidiousError("no 720p or 360p MP4")
        return ("A title", f"https://youtube.test/latest_version?id={vid}&itag={self.itag}&local=true",
                self.itag, self.seconds)

    async def dash(self, vid):
        self.calls.append(("dash", vid))
        if self.fail:
            raise InvidiousError("down")
        if vid == "NoDashAAAA1":
            return None
        return (f"https://youtube.test/latest_version?id={vid}&itag=136&local=true",
                f"https://youtube.test/latest_version?id={vid}&itag=140&local=true")

    async def playlist(self, plid, page):
        self.calls.append(("playlist", plid, page))
        if self.fail or plid != PLID:
            raise InvidiousError("down")
        return "A playlist", [
            {"kind": "video", "id": f"vid{i:08d}", "title": f"Video {i}",
             "channel": "C", "seconds": 60 + i}
            for i in range(120)
        ]


class FakeYtDlp:
    def __init__(self, items=None, fail=False):
        self.items = ITEMS if items is None else items
        self.fail = fail

    async def search(self, query, page, size):
        if self.fail:
            raise YtDlpError("down")
        return self.items[: page * size]

    async def stream(self, vid):
        if self.fail:
            raise YtDlpError("down")
        return "Yt title", "https://googlevideo.test/videoplayback"


def app(invidious, ytdlp, ma=None, jellyfin=None):
    return make_app(Config(invidious_url="http://invidious.test", page_size=5),
                    invidious=invidious, ytdlp=ytdlp, ma=ma, jellyfin=jellyfin)


async def test_first_page_is_four_videos_and_a_playlist(aiohttp_client):
    invidious = FakeInvidious()
    client = await aiohttp_client(app(invidious, FakeYtDlp()))
    resp = await client.post("/search", json={"query": "kittens"})
    assert resp.status == 200
    body = await resp.json()
    assert body["source"] == "invidious"
    assert body["has_next"] is True
    assert [item["n"] for item in body["items"]] == [1, 2, 3, 4, 5]
    assert [item["kind"] for item in body["items"]] == ["video"] * 4 + ["playlist"]
    assert body["items"][0]["title"] == "Title 0"
    assert body["items"][0]["duration"] == "1:00"
    assert body["items"][-1]["title"] == "Playlist 0"
    assert body["items"][-1]["count"] == 100
    assert invidious.calls == [
        ("search", "kittens", 1, "video"),
        ("search", "kittens", 1, "playlist"),
    ]


async def test_a_later_page_is_sliced_out_of_the_invidious_page(aiohttp_client):
    # Our page 4 is videos 12-15 and playlist 3 (four videos a page, then one).
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    body = await (await client.post("/search", json={"query": "x", "page": 4})).json()
    assert [item["title"] for item in body["items"]] == [
        *(f"Title {i}" for i in range(12, 16)), "Playlist 3"]
    assert [item["n"] for item in body["items"]] == [1, 2, 3, 4, 5]


async def test_the_end_of_the_list_stops(aiohttp_client):
    # Our page 6 needs Invidious' second video page, and past the playlists.
    invidious = FakeInvidious(pages={(2, "video"): [], (1, "playlist"): []})
    client = await aiohttp_client(app(invidious, FakeYtDlp()))
    body = await (await client.post("/search", json={"query": "x", "page": 6})).json()
    assert body["items"] == []
    assert body["has_next"] is False
    assert invidious.calls[-1] == ("search", "x", 1, "playlist")


async def test_a_search_stops_at_six_pages_and_says_there_is_more(aiohttp_client):
    # Five a page for six pages: thirty is all a spoken conversation needs.
    # Past that the last page asks for a narrower request instead of a seventh.
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    body = await (await client.post("/search", json={"query": "x", "page": 6})).json()
    assert body["items"]
    assert body["has_next"] is False
    assert body["more"] is True
    assert (await client.post("/search", json={"query": "x", "page": 7})).status == 400


async def test_invidious_down_falls_back_to_ytdlp(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(fail=True), FakeYtDlp()))
    body = await (await client.post("/search", json={"query": "x"})).json()
    assert body["source"] == "yt-dlp"
    assert [item["n"] for item in body["items"]] == [1, 2, 3, 4, 5]


async def test_neither_source_answering_is_a_bad_gateway(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(fail=True), FakeYtDlp(fail=True)))
    resp = await client.post("/search", json={"query": "x"})
    assert resp.status == 502


async def test_resolve_prefers_invidious_and_wants_a_muxed_itag(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    body = await (await client.post("/resolve", json={"video_id": "dQw4w9WgXcQ"})).json()
    assert body["source"] == "invidious"
    assert body["url"].endswith("id=dQw4w9WgXcQ&itag=22&local=true")


class FakeRemux:
    max_seconds = 900

    def __init__(self, path, fail=False):
        self.path = path
        self.fail = fail
        self.made = 0
        self.args = None

    def cached(self, vid):
        return None

    async def make(self, vid, video, audio):
        self.made += 1
        self.args = (video, audio)
        if self.fail:
            from yt_show.remux import RemuxError
            raise RemuxError("no such format")
        return self.path


async def test_a_360p_only_video_is_remuxed_and_served(aiohttp_client, tmp_path):
    path = tmp_path / "dQw4w9WgXcQ.mp4"
    path.write_bytes(b"video")
    remux = FakeRemux(path)
    cfg = Config(invidious_url="http://invidious.test", cache_dir=str(tmp_path),
                 media_url_base="https://yt.test")
    client = await aiohttp_client(make_app(cfg, invidious=FakeInvidious(itag="18"),
                                           ytdlp=FakeYtDlp(), remux=remux))
    body = await (await client.post("/resolve", json={"video_id": "dQw4w9WgXcQ"})).json()
    assert body["source"] == "remux"
    assert body["url"] == "https://yt.test/media/dQw4w9WgXcQ.mp4"
    assert remux.made == 1
    assert remux.args == (
        "https://youtube.test/latest_version?id=dQw4w9WgXcQ&itag=136&local=true",
        "https://youtube.test/latest_version?id=dQw4w9WgXcQ&itag=140&local=true")


async def test_a_video_without_a_720p_pair_plays_360p(aiohttp_client):
    cfg = Config(invidious_url="http://invidious.test", cache_dir="/tmp/x",
                 media_url_base="https://yt.test")
    client = await aiohttp_client(make_app(cfg, invidious=FakeInvidious(itag="18"),
                                           ytdlp=FakeYtDlp(), remux=FakeRemux(None)))
    body = await (await client.post("/resolve", json={"video_id": "NoDashAAAA1"})).json()
    assert body["source"] == "invidious" and "itag=18" in body["url"]


async def test_a_long_video_plays_360p_rather_than_keep_the_screen_waiting(aiohttp_client):
    remux = FakeRemux(None)
    cfg = Config(invidious_url="http://invidious.test", cache_dir="/tmp/x",
                 media_url_base="https://yt.test")
    client = await aiohttp_client(make_app(cfg, invidious=FakeInvidious(itag="18", seconds=3600),
                                           ytdlp=FakeYtDlp(), remux=remux))
    body = await (await client.post("/resolve", json={"video_id": "dQw4w9WgXcQ"})).json()
    assert body["source"] == "invidious" and "itag=18" in body["url"]
    assert remux.made == 0


async def test_a_failed_remux_falls_back_to_360p(aiohttp_client):
    cfg = Config(invidious_url="http://invidious.test", cache_dir="/tmp/x",
                 media_url_base="https://yt.test")
    client = await aiohttp_client(make_app(cfg, invidious=FakeInvidious(itag="18"),
                                           ytdlp=FakeYtDlp(), remux=FakeRemux(None, fail=True)))
    body = await (await client.post("/resolve", json={"video_id": "dQw4w9WgXcQ"})).json()
    assert body["source"] == "invidious" and "itag=18" in body["url"]


async def test_a_video_invidious_cannot_play_comes_from_ytdlp(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    body = await (await client.post("/resolve", json={"video_id": "NoMuxedAAA1"})).json()
    assert body == {"url": "https://googlevideo.test/videoplayback", "title": "Yt title",
                    "source": "yt-dlp"}


async def test_resolve_failing_everywhere_is_a_bad_gateway(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(fail=True), FakeYtDlp(fail=True)))
    assert (await client.post("/resolve", json={"video_id": "dQw4w9WgXcQ"})).status == 502


@pytest.mark.parametrize("payload", [
    {},
    {"query": ""},
    {"query": "x", "page": 0},
    {"query": "x", "page": "2"},
    {"query": "x", "page": 99},
])
async def test_bad_search_input_is_refused(aiohttp_client, payload):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    assert (await client.post("/search", json=payload)).status == 400


@pytest.mark.parametrize("payload", [{}, {"video_id": "not a video"}, {"video_id": 7}])
async def test_bad_resolve_input_is_refused(aiohttp_client, payload):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    assert (await client.post("/resolve", json=payload)).status == 400


async def test_health_says_which_source_is_configured(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    assert await (await client.get("/healthz")).json() == {
        "ok": True, "source": "invidious", "music": False, "jellyfin": False}


async def test_a_playlist_lists_its_videos_a_page_at_a_time(aiohttp_client):
    invidious = FakeInvidious()
    client = await aiohttp_client(app(invidious, FakeYtDlp()))
    first = await (await client.post("/contents", json={"playlist_id": PLID})).json()
    assert first["title"] == "A playlist" and first["has_next"] is True
    assert [i["title"] for i in first["items"]] == [f"Video {i}" for i in range(5)]
    assert [i["n"] for i in first["items"]] == [1, 2, 3, 4, 5]
    second = await (await client.post("/contents", json={"playlist_id": PLID, "page": 2})).json()
    assert [i["title"] for i in second["items"]] == [f"Video {i}" for i in range(5, 10)]
    # Our page 2 is still inside Invidious' first page of 100.
    assert invidious.calls[-1] == ("playlist", PLID, 1)


async def test_a_bad_playlist_id_is_refused(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    assert (await client.post("/contents", json={"playlist_id": "not a playlist"})).status == 400


async def test_a_playlist_that_cannot_be_listed_is_a_bad_gateway(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(fail=True), FakeYtDlp()))
    assert (await client.post("/contents", json={"playlist_id": PLID})).status == 502


def _ma_items():
    out = []
    for i in range(10):
        out.append({"source": "music-assistant", "kind": "radio", "id": f"library://radio/{i}",
                    "title": f"Radio {i}", "channel": "MA"})
        out.append({"source": "music-assistant", "kind": "track", "id": f"library://track/{i}",
                    "title": f"Track {i}", "channel": "An artist"})
    return out


class FakeMa:
    def __init__(self, fail=False):
        self.fail = fail
        self.played = []

    async def search(self, query, limit=40):
        if self.fail:
            from yt_show.ma import MaError
            raise MaError("down")
        return _ma_items()



async def test_a_page_mixes_videos_playlists_and_music(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), ma=FakeMa()))
    body = await (await client.post("/search", json={"query": "lo-fi"})).json()
    assert [(i["kind"], i["source"]) for i in body["items"]] == [
        ("video", "youtube"),
        ("radio", "music-assistant"),
        ("video", "youtube"),
        ("track", "music-assistant"),
        ("playlist", "youtube"),
    ]
    assert [i["n"] for i in body["items"]] == [1, 2, 3, 4, 5]


async def test_music_keeps_its_own_offsets_on_later_pages(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), ma=FakeMa()))
    body = await (await client.post("/search", json={"query": "x", "page": 2})).json()
    assert [i["title"] for i in body["items"]] == [
        "Title 2", "Radio 1", "Title 3", "Track 1", "Playlist 1"]


async def test_music_assistant_being_down_leaves_videos_and_playlists(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), ma=FakeMa(fail=True)))
    body = await (await client.post("/search", json={"query": "x"})).json()
    assert [i["kind"] for i in body["items"]] == ["video"] * 4 + ["playlist"]


JELLY_FILM = {"source": "jellyfin", "kind": "movie", "id": "abc123",
              "title": "A film", "channel": ""}
JELLY_EPISODES = [
    {"source": "jellyfin", "kind": "episode", "id": f"ep{i}", "title": f"Episode {i}",
     "channel": "S1E1 · Show"}
    for i in range(3)
]


class FakeJellyfin:
    def __init__(self, fail=False):
        self.fail = fail

    async def search(self, query, limit=20):
        if self.fail:
            from yt_show.jellyfin import JellyfinError
            raise JellyfinError("down")
        return [JELLY_FILM]

    async def episodes(self, series, start, limit):
        return JELLY_EPISODES[start : start + limit], len(JELLY_EPISODES) > start + limit

    def stream(self, item):
        return f"http://192.168.1.10:8096/Videos/{item}/stream.mp4?api_key=k"


async def test_a_page_includes_a_jellyfin_title(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), ma=FakeMa(),
                                      jellyfin=FakeJellyfin()))
    body = await (await client.post("/search", json={"query": "the matrix"})).json()
    assert [(i["kind"], i["source"]) for i in body["items"]] == [
        ("video", "youtube"),
        ("radio", "music-assistant"),
        ("video", "youtube"),
        ("movie", "jellyfin"),
        ("playlist", "youtube"),
    ]
    assert body["items"][3]["title"] == "A film"


async def test_jellyfin_being_down_leaves_the_other_sources(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), ma=FakeMa(),
                                      jellyfin=FakeJellyfin(fail=True)))
    body = await (await client.post("/search", json={"query": "x"})).json()
    assert [i["kind"] for i in body["items"]] == ["video", "radio", "video", "track", "playlist"]


async def test_a_jellyfin_stream_is_resolved(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), jellyfin=FakeJellyfin()))
    body = await (await client.post("/jellyfin/resolve", json={"item_id": "abc123"})).json()
    assert body["url"] == "http://192.168.1.10:8096/Videos/abc123/stream.mp4?api_key=k"
    assert (await client.post("/jellyfin/resolve", json={"item_id": "bad id"})).status == 400
    assert (await client.post("/jellyfin/resolve",
                              json={"item_id": "abc123"})).status == 200


async def test_a_series_episodes_are_listed(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp(), jellyfin=FakeJellyfin()))
    body = await (await client.post("/jellyfin/contents",
                                    json={"series_id": "abc123", "page": 1})).json()
    assert [i["title"] for i in body["items"]] == ["Episode 0", "Episode 1", "Episode 2"]
    assert body["page"] == 1


async def test_jellyfin_needs_to_be_configured(aiohttp_client):
    client = await aiohttp_client(app(FakeInvidious(), FakeYtDlp()))
    assert (await client.post("/jellyfin/resolve", json={"item_id": "abc123"})).status == 502
    assert (await client.post("/jellyfin/contents", json={"series_id": "abc123"})).status == 502
