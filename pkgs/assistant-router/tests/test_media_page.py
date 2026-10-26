"""The results page's pictures. yt-show hands out its sources' public
addresses, which the Show cannot always reach; the router fetches them from
the loopback instead (and is only allowed to reach the loopback)."""
from aiohttp import web

from assistant_router.server import Config, make_app


def router(**sources):
    return make_app(Config(local_url="http://127.0.0.1:1/v1", local_model="x",
                           cloud_url="http://127.0.0.1:1/v1", cloud_model="smart",
                           mode="cloud-first", **sources))


async def test_a_thumbnail_is_fetched_from_the_loopback_source(aiohttp_client, aiohttp_server):
    seen = []

    async def picture(request):
        seen.append(request.path_qs)
        return web.Response(body=b"\xff\xd8thumb", content_type="image/jpeg")

    source = web.Application()
    source.router.add_get("/vi/abc/mqdefault.jpg", picture)
    source_url = f"http://127.0.0.1:{(await aiohttp_server(source)).port}"
    client = await aiohttp_client(router(invidious_public_url="https://youtube.example",
                                         invidious_url=source_url))

    response = await client.get("/media/thumb",
                                params={"u": "https://youtube.example/vi/abc/mqdefault.jpg"})
    assert response.status == 200
    assert await response.read() == b"\xff\xd8thumb"
    assert seen == ["/vi/abc/mqdefault.jpg"]


async def test_a_jellyfin_thumbnail_is_fetched_from_the_loopback(aiohttp_client, aiohttp_server):
    async def picture(_request):
        return web.Response(body=b"\xff\xd8film", content_type="image/jpeg")

    source = web.Application()
    source.router.add_get("/Items/abc/Images/Primary", picture)
    source_url = f"http://127.0.0.1:{(await aiohttp_server(source)).port}"
    client = await aiohttp_client(router(jellyfin_public_url="http://192.0.2.10:8096",
                                         jellyfin_url=source_url))

    response = await client.get("/media/thumb", params={
        "u": "http://192.0.2.10:8096/Items/abc/Images/Primary?maxWidth=480"})
    assert response.status == 200
    assert await response.read() == b"\xff\xd8film"


async def test_a_picture_from_elsewhere_is_not_fetched(aiohttp_client):
    # No source is configured (the defaults), so nothing is fetched: a 404,
    # never an error from reading a setting that is not there.
    client = await aiohttp_client(router())
    assert (await client.get("/media/thumb", params={"u": "https://example/vi/a.jpg"})).status == 404
