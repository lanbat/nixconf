import pytest
from aiohttp import web

from assistant_router.hass import Hass, HassError

STATES = [
    {"entity_id": "calendar.kiril", "state": "off", "attributes": {"friendly_name": "Kiril"}},
    {"entity_id": "todo.kiril_todo", "state": "2", "attributes": {"friendly_name": "akadmin_todo"}},
    {"entity_id": "calendar.family", "state": "on", "attributes": {}},
    {"entity_id": "bad id", "state": "x"},
]


@pytest.fixture
async def fake(aiohttp_server):
    seen = []

    async def states(request):
        seen.append(("GET", request.path, request.query_string, request.headers.get("Authorization"), None))
        if request.headers.get("Authorization") != "Bearer good":
            return web.Response(status=401, text="401: Unauthorized")
        return web.json_response(STATES)

    async def service(request):
        body = await request.json()
        seen.append(("POST", request.path, request.query_string, request.headers.get("Authorization"), body))
        if "return_response" in request.query:
            return web.json_response({"changed_states": [], "service_response": {"todo.kiril_todo": {"items": []}}})
        return web.json_response([])

    app = web.Application()
    app.router.add_get("/api/states", states)
    app.router.add_post("/api/services/{domain}/{service}", service)
    server = await aiohttp_server(app)
    return f"http://127.0.0.1:{server.port}", seen


async def test_states_by_domain_and_name(fake):
    url, seen = fake
    ha = Hass(url, "good")
    assert [e["entity_id"] for e in await ha.states("calendar")] == ["calendar.family", "calendar.kiril"]
    every = await ha.states()
    assert [e["name"] for e in every] == ["akadmin_todo", "calendar.family", "Kiril"]  # no name: the id
    assert every[2] == {"entity_id": "calendar.kiril", "name": "Kiril", "state": "off"}
    assert seen[0][3] == "Bearer good"
    await ha.aclose()


async def test_a_service_asks_for_a_response_only_when_told(fake):
    url, seen = fake
    ha = Hass(url, "good")
    got = await ha.call("todo", "get_items", {"entity_id": "todo.kiril_todo"}, response=True)
    assert got == {"todo.kiril_todo": {"items": []}}
    assert seen[-1][:3] == ("POST", "/api/services/todo/get_items", "return_response")
    assert await ha.call("todo", "add_item", {"entity_id": "todo.kiril_todo", "item": "milk"}) == {}
    assert seen[-1][2] == "" and seen[-1][4] == {"entity_id": "todo.kiril_todo", "item": "milk"}
    with pytest.raises(HassError):
        await ha.call("todo/../x", "add_item", {})
    await ha.aclose()


async def test_a_refused_token_stops_every_call(fake):
    """Home Assistant bans an address after a few refused logins: one is enough."""
    url, seen = fake
    ha = Hass(url, "wrong")
    with pytest.raises(HassError) as e:
        await ha.states()
    assert e.value.status == 401 and ha.refused
    with pytest.raises(HassError):
        await ha.call("todo", "add_item", {})
    assert len(seen) == 1  # the second never left
    await ha.aclose()


async def test_no_answer_is_an_error():
    ha = Hass("http://127.0.0.1:9", "good", timeout=2)
    with pytest.raises(HassError):
        await ha.states()
    assert not ha.refused
    await ha.aclose()


async def test_a_refused_service_list_does_not_stop_the_other_calls(aiohttp_server):
    """An ordinary user may not read Home Assistant's service list on some
    versions; that must not take the lists and reminders down with it."""
    async def refused(request):
        return web.Response(status=403, text="403: Forbidden")

    async def empty(request):
        return web.json_response([])

    app = web.Application()
    app.router.add_get("/api/services", refused)
    app.router.add_get("/api/states", empty)
    server = await aiohttp_server(app)
    ha = Hass(f"http://127.0.0.1:{server.port}", "good")
    with pytest.raises(HassError):
        await ha.services("esphome")
    assert not ha.refused
    assert await ha.states() == []
    await ha.aclose()
