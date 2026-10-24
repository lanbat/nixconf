"""The video path through the router: a cloud tool fills the list, and the
next thing said plays from it. Home Assistant, yt-show and the cloud are
stand-ins, so the test runs offline."""
import json

import pytest
from aiohttp import web

from assistant_router.body import Bodies
from assistant_router.hass import Hass
from assistant_router.server import Config, make_app
from assistant_router.state import State
from assistant_router.youtube import YtShow

CTX = """LANBAT-CONTEXT v1
room: Kitchen
device: dev1
time: t
entities:
media_player.kitchen_show|Kitchen Show|Kitchen|
end
"""

ITEMS = [
    {"n": i, "id": f"vid{i:08d}", "title": f"Title {i}", "channel": "C", "duration": "1:00"}
    for i in range(1, 6)
]

SCREEN_CAN = ["mood", "caption", "greet", "capture", "phones", "here"]


class Sock:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def req(text, conv="c1"):
    return {"model": "assistant",
            "messages": [{"role": "system", "content": CTX},
                         {"role": "user", "content": text}],
            "user": conv, "tools": [], "temperature": 0.5, "top_p": 1.0}


@pytest.fixture
async def world(aiohttp_server):
    seen = {"yt": [], "ha": [], "cloud": []}
    cloud = {"reply": None}

    async def yt_search(request):
        seen["yt"].append(("search", await request.json()))
        return web.json_response({"items": ITEMS, "has_next": False, "source": "invidious"})

    async def yt_resolve(request):
        body = await request.json()
        seen["yt"].append(("resolve", body))
        return web.json_response({"url": "https://youtube.example/latest_version?id=x&itag=22&local=true",
                                  "title": "Title 2", "source": "invidious"})

    yt = web.Application()
    yt.router.add_post("/search", yt_search)
    yt.router.add_post("/resolve", yt_resolve)
    yt_url = f"http://127.0.0.1:{(await aiohttp_server(yt)).port}"

    async def ha_services(request):
        return web.json_response([{"domain": "esphome",
                                   "services": {"kitchen_play_video": {}}}])

    async def ha_play(request):
        seen["ha"].append(("play", await request.json()))
        return web.json_response([])

    ha = web.Application()
    ha.router.add_get("/api/services", ha_services)
    ha.router.add_post("/api/services/esphome/kitchen_play_video", ha_play)
    ha_url = f"http://127.0.0.1:{(await aiohttp_server(ha)).port}"

    async def cloud_chat(request):
        seen["cloud"].append(await request.json())
        return web.json_response({"choices": [{"index": 0, "finish_reason": "stop",
                                               "message": cloud["reply"]}]})

    llm = web.Application()
    llm.router.add_post("/v1/chat/completions", cloud_chat)
    llm_url = f"http://127.0.0.1:{(await aiohttp_server(llm)).port}/v1/chat/completions"
    return seen, cloud, yt_url, ha_url, llm_url


def router(yt_url, ha_url, llm_url, bodies):
    cfg = Config(local_url="http://127.0.0.1:1/v1", local_model="x", cloud_url=llm_url,
                 cloud_model="smart", mode="cloud-first",
                 media_players={"kitchen": "echo show 8"})
    hass = Hass(ha_url, "token")
    return make_app(cfg, state=State(), bodies=bodies, yt_show=YtShow(yt_url), hass=hass)


async def test_the_cloud_fills_the_list_and_the_next_sentence_plays_it(aiohttp_client, world):
    seen, cloud, yt_url, ha_url, llm_url = world
    bodies = Bodies("You are Nabu.")
    sock = Sock()
    bodies.connect("Kitchen", sock, "screen", SCREEN_CAN)
    client = await aiohttp_client(router(yt_url, ha_url, llm_url, bodies))

    # "Play kittens": the cloud calls the search tool.
    cloud["reply"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "search_media", "arguments": '{"query": "kittens"}'}}]}
    body = await (await client.post("/v1/chat/completions", json=req("play kittens"))).json()
    said = body["choices"][0]["message"]["content"]
    assert said.startswith("Here are 5 for kittens"), said
    assert seen["yt"] == [("search", {"query": "kittens", "page": 1})]
    # The list is on the screen, and stays up longer than a greeting.
    sent = sock.sent[-1]["act"]
    assert sent["caption_seconds"] == 120 and sent["caption"].startswith("1. Title 1"), sent

    # "Play two": the early path answers it, without the cloud.
    played = await (await client.post("/v1/chat/completions", json=req("play two", conv="c2"))).json()
    assert played["choices"][0]["message"]["content"] == "Playing Title 2."
    assert seen["yt"][-1] == ("resolve", {"video_id": "vid00000002"})
    assert seen["ha"] == [("play", {
        "url": "https://youtube.example/latest_version?id=x&itag=22&local=true",
        "title": "Title 2"})]
    assert len(seen["cloud"]) == 1  # the selection did not need a model


async def test_next_page_asks_the_helper_for_the_next_page(aiohttp_client, world):
    seen, cloud, yt_url, ha_url, llm_url = world
    bodies = Bodies("You are Nabu.")
    sock = Sock()
    bodies.connect("Kitchen", sock, "screen", SCREEN_CAN)
    client = await aiohttp_client(router(yt_url, ha_url, llm_url, bodies))

    cloud["reply"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "search_media", "arguments": '{"query": "kittens"}'}}]}
    await client.post("/v1/chat/completions", json=req("play kittens"))
    await client.post("/v1/chat/completions", json=req("next page", conv="c2"))
    assert seen["yt"][-1] == ("search", {"query": "kittens", "page": 2})


async def test_cancel_forgets_the_list(aiohttp_client, world):
    seen, cloud, yt_url, ha_url, llm_url = world
    bodies = Bodies("You are Nabu.")
    sock = Sock()
    bodies.connect("Kitchen", sock, "screen", SCREEN_CAN)
    client = await aiohttp_client(router(yt_url, ha_url, llm_url, bodies))

    cloud["reply"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "search_media", "arguments": '{"query": "kittens"}'}}]}
    await client.post("/v1/chat/completions", json=req("play kittens"))
    cancelled = await (await client.post("/v1/chat/completions", json=req("cancel", conv="c2"))).json()
    assert cancelled["choices"][0]["message"]["content"] == "Okay."
    # With no list, "play two" is not a selection: it goes to the model again.
    cloud["reply"] = {"role": "assistant", "content": "Sorry, what would you like?"}
    await client.post("/v1/chat/completions", json=req("play two", conv="c3"))
    assert len(seen["cloud"]) == 2


MIXED = [
    {"n": 1, "kind": "video", "source": "youtube", "id": "vid00000001", "title": "A video",
     "channel": "C", "duration": "3:00"},
    {"n": 2, "kind": "playlist", "source": "youtube", "id": "PLabcdefghijklmnopqrstuvwx",
     "title": "A playlist", "channel": "C", "count": 2},
    {"n": 3, "kind": "radio", "source": "music-assistant", "id": "library://radio/1",
     "title": "BBC Radio 4", "channel": "RadioBrowser"},
]


@pytest.fixture
async def mixed_world(aiohttp_server):
    seen = {"yt": [], "ha": []}
    cloud = {"reply": None}

    async def yt_search(request):
        seen["yt"].append(("search", await request.json()))
        return web.json_response({"items": MIXED, "has_next": False, "source": "invidious"})

    async def yt_contents(request):
        seen["yt"].append(("contents", await request.json()))
        return web.json_response({
            "title": "A playlist", "has_next": False, "source": "invidious",
            "items": [
                {"n": 1, "kind": "video", "id": "vid00000007", "title": "Video 7",
                 "channel": "C", "duration": "1:00"},
                {"n": 2, "kind": "video", "id": "vid00000008", "title": "Video 8",
                 "channel": "C", "duration": "2:00"},
            ]})

    async def yt_resolve(request):
        body = await request.json()
        seen["yt"].append(("resolve", body))
        return web.json_response({"url": "https://youtube.example/video.mp4",
                                  "title": "Video 7", "source": "invidious"})

    yt = web.Application()
    async def yt_play(request):
        seen["yt"].append(("play", await request.json()))
        return web.json_response({"ok": True, "player": "Echo Show 8"})

    yt.router.add_post("/search", yt_search)
    yt.router.add_post("/contents", yt_contents)
    yt.router.add_post("/resolve", yt_resolve)
    yt.router.add_post("/play", yt_play)
    yt_url = f"http://127.0.0.1:{(await aiohttp_server(yt)).port}"

    async def ha_services(request):
        return web.json_response([{"domain": "esphome",
                                   "services": {"kitchen_play_video": {}}}])

    async def ha_play(request):
        seen["ha"].append(("play_video", await request.json()))
        return web.json_response([])

    async def ha_states(request):
        return web.json_response([
            {"entity_id": "media_player.echo_show_8", "state": "idle",
             "attributes": {"friendly_name": "Echo Show 8"}},
        ])

    async def ha_play_media(request):
        seen["ha"].append(("play_media", await request.json()))
        return web.json_response([])

    ha = web.Application()
    ha.router.add_get("/api/services", ha_services)
    ha.router.add_get("/api/states", ha_states)
    ha.router.add_post("/api/services/esphome/kitchen_play_video", ha_play)
    ha.router.add_post("/api/services/media_player/play_media", ha_play_media)
    ha_url = f"http://127.0.0.1:{(await aiohttp_server(ha)).port}"

    async def cloud_chat(request):
        return web.json_response({"choices": [{"index": 0, "finish_reason": "stop",
                                               "message": cloud["reply"]}]})

    llm = web.Application()
    llm.router.add_post("/v1/chat/completions", cloud_chat)
    llm_url = f"http://127.0.0.1:{(await aiohttp_server(llm)).port}/v1/chat/completions"
    return seen, cloud, yt_url, ha_url, llm_url


async def test_a_playlist_opens_its_videos_and_one_of_them_plays(aiohttp_client, mixed_world):
    seen, cloud, yt_url, ha_url, llm_url = mixed_world
    bodies = Bodies("You are Nabu.")
    sock = Sock()
    bodies.connect("Kitchen", sock, "screen", SCREEN_CAN)
    client = await aiohttp_client(router(yt_url, ha_url, llm_url, bodies))

    cloud["reply"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "search_media", "arguments": '{"query": "lo-fi"}'}}]}
    first = await (await client.post("/v1/chat/completions", json=req("play lo-fi"))).json()
    said = first["choices"][0]["message"]["content"]
    assert "[playlist]" in sock.sent[-1]["act"]["caption"]
    assert "playlist" in said

    # "play two" is the playlist: its videos replace the list on the screen.
    opened = await (await client.post("/v1/chat/completions", json=req("play two", conv="c2"))).json()
    assert seen["yt"][-1] == ("contents", {"playlist_id": "PLabcdefghijklmnopqrstuvwx", "page": 1})
    assert "Video 7" in opened["choices"][0]["message"]["content"]
    assert sock.sent[-1]["act"]["caption"].startswith("1. Video 7")

    # "play one" plays the first of the playlist.
    played = await (await client.post("/v1/chat/completions", json=req("play one", conv="c3"))).json()
    assert played["choices"][0]["message"]["content"] == "Playing Video 7."
    assert seen["yt"][-1] == ("resolve", {"video_id": "vid00000007"})
    assert seen["ha"][-1] == ("play_video", {
        "url": "https://youtube.example/video.mp4", "title": "Video 7"})


async def test_going_back_returns_to_the_search_results(aiohttp_client, mixed_world):
    seen, cloud, yt_url, ha_url, llm_url = mixed_world
    bodies = Bodies("You are Nabu.")
    sock = Sock()
    bodies.connect("Kitchen", sock, "screen", SCREEN_CAN)
    client = await aiohttp_client(router(yt_url, ha_url, llm_url, bodies))

    cloud["reply"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "search_media", "arguments": '{"query": "lo-fi"}'}}]}
    await client.post("/v1/chat/completions", json=req("play lo-fi"))
    await client.post("/v1/chat/completions", json=req("play two", conv="c2"))
    back = await (await client.post("/v1/chat/completions", json=req("go back", conv="c3"))).json()
    assert "playlist" in sock.sent[-1]["act"]["caption"]
    assert "A playlist" in back["choices"][0]["message"]["content"]


async def test_a_music_result_plays_on_the_room_player(aiohttp_client, mixed_world):
    seen, cloud, yt_url, ha_url, llm_url = mixed_world
    bodies = Bodies("You are Nabu.")
    bodies.connect("Kitchen", Sock(), "screen", SCREEN_CAN)
    client = await aiohttp_client(router(yt_url, ha_url, llm_url, bodies))

    cloud["reply"] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "search_media", "arguments": '{"query": "radio 4"}'}}]}
    await client.post("/v1/chat/completions", json=req("play radio 4"))
    played = await (await client.post("/v1/chat/completions", json=req("play three", conv="c2"))).json()
    assert played["choices"][0]["message"]["content"] == "Playing BBC Radio 4."
    assert seen["yt"][-1] == ("search", {"query": "radio 4", "page": 1})
    assert seen["ha"][-1] == ("play_media", {
        "entity_id": "media_player.echo_show_8",
        "media_content_id": "library://radio/1",
        "media_content_type": "music"})
