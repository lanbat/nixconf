"""The home page the Show streams: served by the router, and what it draws."""
from aiohttp import web

from assistant_router.server import Config, make_app


def router():
    return make_app(Config(local_url="http://127.0.0.1:1/v1", local_model="x",
                           cloud_url="http://127.0.0.1:1/v1", cloud_model="smart",
                           mode="cloud-first"))


async def test_the_page_is_served(aiohttp_client):
    client = await aiohttp_client(router())
    response = await client.get("/screen")
    assert response.status == 200
    assert b"LANBAT" in await response.read()


async def test_with_no_screen_the_page_is_told_nothing(aiohttp_client):
    client = await aiohttp_client(router())
    data = await (await client.get("/screen/state")).json()
    assert data["room"] == "" and data["glance"] == []
    assert data["events"] == [] and data["reminders"] == []
