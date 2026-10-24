"""youtube.py: the list on the screen, what is said next, and the play action."""
import time

import pytest
from aiohttp import web

from assistant_router.hass import Hass, HassError
from assistant_router.youtube import (Results, Sessions, YtShow, caption, control, interpret,
                                      play_service, plays_something, spoken)

TITLES = [
    "A very long title about kittens that goes on and on and on for ages",
    "Another long title, this one about puppies playing in a garden",
    "Third: cooking a whole chicken in a kettle",
    "Fourth: the history of the traffic cone",
    "Fifth: a surprisingly good cover of a famous song",
]


def results(has_next=True, page=1, query="kittens", at=None):
    return Results(query, page, tuple(
        {"n": i, "id": f"vid{i:08d}", "title": title, "channel": "C", "duration": "1:00"}
        for i, title in enumerate(TITLES, start=1)), has_next,
        time.monotonic() if at is None else at)


@pytest.mark.parametrize("said", ["play 2", "play two", "play the second one", "the second one",
                                  "number 2", "result two", "watch 2", "2"])
def test_choosing_a_result(said):
    assert control(said) == ("play", 2)


def test_choosing_a_result_by_name():
    assert control("put on the third one") == ("play", 3)


def test_last_is_the_last_one():
    assert control("play the last one") == ("play", -1)


@pytest.mark.parametrize("said", ["next page", "next", "more results", "page 2", "page down"])
def test_more_pages(said):
    assert control(said) in (("next", None), ("page", 2))


def test_going_back():
    assert control("previous page") == ("prev", None)
    assert control("back a page") == ("prev", None)


def test_going_back_to_the_list_below():
    assert control("go back") == ("back", None)
    assert control("back to results") == ("back", None)
    assert control("back to the search") == ("back", None)


@pytest.mark.parametrize("said", ["cancel", "never mind", "forget it", "stop"])
def test_cancelling(said):
    assert control(said) == ("cancel", None)


@pytest.mark.parametrize("said", ["what's the weather", "add milk to my list", "turn off the light",
                                  "play some music", "hello"])
def test_anything_else_is_not_a_control(said):
    assert control(said) is None


def test_a_link_is_played_as_it_is():
    assert interpret("https://youtu.be/dQw4w9WgXcQ") == ("raw", "https://youtu.be/dQw4w9WgXcQ")
    assert interpret("dQw4w9WgXcQ") == ("raw", "dQw4w9WgXcQ")
    assert interpret("play two") == ("play", 2)
    assert interpret("something vague") is None


@pytest.mark.parametrize("said", ["play kittens", "play the new Linus video", "play a video about cats",
                                  "watch a documentary about trains"])
def test_playing_something_goes_to_the_search(said):
    assert plays_something(said)


@pytest.mark.parametrize("said", ["play", "play it", "play that again", "play some music",
                                  "play the radio", "play a playlist", "resume", "pause"])
def test_resuming_or_music_is_not_a_search(said):
    assert not plays_something(said)


def test_the_caption_fits_a_caption_and_numbers_everything():
    text = caption(results())
    assert len(text) <= 200, text
    for n in range(1, 6):
        assert f"{n}." in text
    assert "next page" in text
    assert len(caption(results(has_next=False))) <= 200


def test_the_spoken_list_names_the_numbers_and_what_to_say():
    said = spoken(results())
    assert said.startswith("Here are 5 for kittens")
    assert "1, " in said and "5, " in said
    assert "next page" in said
    assert "next page" not in spoken(results(has_next=False))


def test_a_playlist_is_labelled_on_the_screen_and_in_speech():
    mixed = Results("lo-fi", 1, (
        {"n": 1, "kind": "video", "id": "dQw4w9WgXcQ", "title": "A video", "duration": "3:00"},
        {"n": 2, "kind": "playlist", "id": "PLabcdefghijklmnopqrstuvwx",
         "title": "A playlist", "count": 12},
    ), False, time.monotonic())
    screen = caption(mixed)
    assert len(screen) <= 200
    assert "[playlist]" in screen and "(12)" in screen
    said = spoken(mixed)
    assert "playlist" in said and "12 videos" in said


def test_music_results_are_labelled_on_the_screen_and_in_speech():
    mixed = Results("jazz", 1, (
        {"n": 1, "kind": "radio", "source": "music-assistant", "id": "library://radio/1",
         "title": "BBC Radio 4", "channel": "RadioBrowser"},
        {"n": 2, "kind": "track", "source": "music-assistant", "id": "library://track/1",
         "title": "So What", "channel": "Miles Davis"},
        {"n": 3, "kind": "playlist", "source": "music-assistant", "id": "library://playlist/1",
         "title": "Jazz", "count": 20},
    ), False, time.monotonic())
    screen = caption(mixed)
    assert "[radio]" in screen and "[track]" in screen and "[music playlist]" in screen
    said = spoken(mixed)
    assert "radio, BBC Radio 4" in said and "track, So What" in said
    assert "music playlist" in said


def test_a_playlist_list_sits_over_the_search_and_goes_back():
    sessions = Sessions()
    search = results()
    sessions.set("Kitchen", search)
    playlist = Results("A playlist", 1, (
        {"n": 1, "kind": "video", "id": "vid00000001", "title": "Video 1", "duration": "1:00"},
    ), False, time.monotonic(), playlist="PLabcdefghijklmnopqrstuvwx")
    sessions.push("Kitchen", playlist)
    assert sessions.get("Kitchen") == playlist
    assert sessions.pop("Kitchen")
    assert sessions.get("Kitchen") == search
    assert not sessions.pop("Kitchen")  # already the first list


def test_a_playlist_page_replaces_the_one_above_it():
    sessions = Sessions()
    sessions.set("Kitchen", results())
    first = Results("A playlist", 1, (), True, time.monotonic(),
                    playlist="PLabcdefghijklmnopqrstuvwx")
    sessions.push("Kitchen", first)
    second = Results("A playlist", 2, (), False, time.monotonic(),
                     playlist="PLabcdefghijklmnopqrstuvwx")
    sessions.replace("Kitchen", second)
    assert sessions.get("Kitchen") == second
    assert sessions.pop("Kitchen")  # still just one level over the search
    assert not sessions.pop("Kitchen")


def test_a_list_is_forgotten_after_a_while():
    now = [100.0]
    sessions = Sessions(seconds=60.0, clock=lambda: now[0])
    sessions.set("Kitchen", results(at=now[0]))
    assert sessions.has("Kitchen")
    now[0] = 161.0
    assert sessions.get("Kitchen") is None
    assert not sessions.has("Kitchen")


def test_a_list_is_per_room_and_clearable():
    sessions = Sessions()
    sessions.set("Kitchen", results())
    assert sessions.has("kitchen") and not sessions.has("Office")
    sessions.clear("Kitchen")
    assert not sessions.has("Kitchen")


class FakeHass:
    def __init__(self, keys=None, fail=False):
        self.keys = keys or []
        self.fail = fail

    async def services(self, domain):
        if self.fail:
            raise HassError("no services")
        return self.keys


async def test_the_only_play_video_action_is_the_one():
    assert await play_service(FakeHass(["kitchen_play_video"]), "Kitchen") == \
        ("esphome", "kitchen_play_video")


async def test_a_room_picks_its_own_action():
    hass = FakeHass(["kitchen_play_video", "office_play_video"])
    assert await play_service(hass, "Office") == ("esphome", "office_play_video")
    assert await play_service(hass, "Hall") is None


async def test_an_override_wins_and_home_assistant_being_down_is_no_action():
    assert await play_service(FakeHass([]), "Kitchen", "esphome.kitchen_play_video") == \
        ("esphome", "kitchen_play_video")
    assert await play_service(FakeHass(fail=True), "Kitchen") is None


async def aiohttp_ha(aiohttp_server, services):
    async def listing(request):
        return web.json_response([{"domain": "esphome", "services": {k: {} for k in services}},
                                  {"domain": "todo", "services": {"add_item": {}}}])

    app = web.Application()
    app.router.add_get("/api/services", listing)
    server = await aiohttp_server(app)
    return f"http://127.0.0.1:{server.port}"


async def test_hass_lists_a_domains_services(aiohttp_server):
    base = await aiohttp_ha(aiohttp_server, ["kitchen_play_video", "kitchen_stop_video"])
    hass = Hass(base, "token")
    assert await hass.services("esphome") == ["kitchen_play_video", "kitchen_stop_video"]
    assert await hass.services("todo") == ["add_item"]
    assert await hass.services("nope") == []
    with pytest.raises(HassError):
        await hass.services("bad/domain")
    await hass.aclose()


async def test_the_helper_client_finds_the_helper_and_survives_its_absence(aiohttp_server):
    async def search(request):
        body = await request.json()
        assert body == {"query": "kittens", "page": 1}
        return web.json_response({"items": [{"n": 1, "id": "dQw4w9WgXcQ", "title": "A",
                                             "channel": "C", "duration": "1:00"}],
                                  "has_next": False})

    async def resolve(request):
        body = await request.json()
        assert body == {"video_id": "dQw4w9WgXcQ"}
        return web.json_response({"url": "https://x/video", "title": "A"})

    async def broken(request):
        return web.Response(status=502)

    async def play(request):
        body = await request.json()
        assert body == {"room": "Kitchen", "uri": "library://radio/1", "title": "BBC Radio 4"}
        return web.json_response({"ok": True, "player": "Echo Show 8"})

    app = web.Application()
    app.router.add_post("/search", search)
    app.router.add_post("/resolve", resolve)
    app.router.add_post("/play", play)
    app.router.add_post("/broken", broken)
    server = await aiohttp_server(app)
    client = YtShow(f"http://127.0.0.1:{server.port}")
    found = await client.search("kittens", 1)
    assert found["items"][0]["title"] == "A"
    assert (await client.resolve("dQw4w9WgXcQ"))["url"] == "https://x/video"
    assert await client.play_media("Kitchen", "library://radio/1", "BBC Radio 4") == "Echo Show 8"
    assert await client.play_media("Kitchen", "library://nope/1") is None
    assert await client._post("/broken", {}) is None
    await client.aclose()
