import pytest
from aiohttp import web

from assistant_router.frigate import Frigate, FrigateError, _body, safe_name


# ── pure helpers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["kiril", "m4ria", "a-b_c", "Z9"])
def test_safe_name_accepts_person_keys(key):
    assert safe_name(key) == key


@pytest.mark.parametrize("bad", ["", "  ", "kiril/", "a/b", "..", "a b", "a.b", "a\\b", "café"])
def test_safe_name_rejects_unsafe_names(bad):
    with pytest.raises(FrigateError):
        safe_name(bad)


def test_body_reads_json_and_ignores_garbage():
    assert _body('{"success": true, "name": "kiril"}') == {"success": True, "name": "kiril"}
    assert _body("not json") == {}
    assert _body("") == {}
    assert _body("[1, 2]") == {}  # a list, not a dict


# ── the client, against a fake Frigate ────────────────────────────────────────

async def _upload(request):
    """(field name, filename, bytes) of the single uploaded part."""
    reader = await request.multipart()
    name, filename, payload = None, None, b""
    async for part in reader:
        for token in part.headers.get("Content-Disposition", "").split(";"):
            t = token.strip()
            if t.startswith("name="):
                name = t[5:].strip().strip('"')
            elif t.startswith("filename="):
                filename = t[9:].strip().strip('"')
        payload = await part.read(decode=True)
    return name, filename, payload


@pytest.fixture
async def fake(aiohttp_server):
    state = {"faces": {}, "calls": [], "upload": None, "fail": False}

    async def get_faces(request):
        return web.json_response({n: list(fs) for n, fs in state["faces"].items()})

    async def create(request):
        n = request.match_info["name"]
        state["faces"].setdefault(n, [])
        state["calls"].append(("create", n))
        return web.json_response({"success": False, "message": "Successfully created face folder."})

    async def register(request):
        n = request.match_info["name"]
        if state["fail"]:
            return web.json_response({"success": False, "message": "bad image"}, status=400)
        state["upload"] = await _upload(request)
        state["calls"].append(("register", n))
        return web.json_response({"success": True, "name": n})

    async def recognize(request):
        state["upload"] = await _upload(request)
        state["calls"].append(("recognize",))
        # As Frigate 0.17 answers.
        return web.json_response(state.get("recognized") or {"success": True, "score": 0.91, "face_name": "kiril"})

    async def delete(request):
        n = request.match_info["name"]
        state["calls"].append(("delete", n, await request.json()))
        return web.json_response({"success": True, "message": "ok"})

    async def rename(request):
        old = request.match_info["old_name"]
        state["calls"].append(("rename", old, await request.json()))
        return web.json_response({"success": True, "message": "ok"})

    app = web.Application()
    app.router.add_get("/api/faces", get_faces)
    app.router.add_post("/api/faces/{name}/create", create)
    app.router.add_post("/api/faces/{name}/register", register)
    app.router.add_post("/api/faces/recognize", recognize)
    app.router.add_post("/api/faces/{name}/delete", delete)
    app.router.add_put("/api/faces/{old_name}/rename", rename)
    srv = await aiohttp_server(app)
    client = Frigate(f"http://{srv.host}:{srv.port}")
    try:
        yield client, state
    finally:
        await client.aclose()


async def test_list_reads_the_library(fake):
    client, state = fake
    state["faces"] = {"kiril": ["kiril-1.webp", "kiril-2.webp"], "maria": []}
    assert await client.list() == {"kiril": ["kiril-1.webp", "kiril-2.webp"], "maria": []}
    assert state["calls"] == []


async def test_create_posts_to_the_person_and_ignores_the_flag(fake):
    client, state = fake
    out = await client.create("kiril")
    assert state["calls"] == [("create", "kiril")]
    assert "kiril" in state["faces"]
    assert out["message"] == "Successfully created face folder."


async def test_register_uploads_the_image_as_the_file_field(fake):
    client, state = fake
    img = b"\xff\xd8\xff fake-jpeg"
    out = await client.register("kiril", img)
    assert out == {"success": True, "name": "kiril"}
    assert state["upload"] == ("file", "capture.jpg", img)


async def test_recognize_uploads_and_returns_the_match(fake):
    client, state = fake
    out = await client.recognize(b"\xff\xd8 probe")
    assert out == {"success": True, "name": "kiril", "confidence": 0.91}
    assert state["upload"][0] == "file"
    assert state["calls"] == [("recognize",)]


async def test_recognize_reads_an_unknown_face_as_nobody(fake):
    client, state = fake
    state["recognized"] = {"success": True, "score": 0.42, "face_name": "unknown"}
    assert await client.recognize(b"\xff\xd8 probe") == {"success": False, "name": None, "confidence": 0.42}


async def test_recognize_still_reads_the_older_answer(fake):
    client, state = fake
    state["recognized"] = {"success": True, "name": "kiril", "confidence": 0.88}
    assert await client.recognize(b"\xff\xd8 probe") == {"success": True, "name": "kiril", "confidence": 0.88}


async def test_delete_posts_the_ids(fake):
    client, state = fake
    await client.delete("kiril", ["a.webp", "b.webp"])
    assert state["calls"] == [("delete", "kiril", {"ids": ["a.webp", "b.webp"]})]


async def test_rename_puts_the_new_name(fake):
    client, state = fake
    await client.rename("kiril", "maria")
    assert state["calls"] == [("rename", "kiril", {"new_name": "maria"})]


async def test_enroll_creates_then_registers(fake):
    client, state = fake
    out = await client.enroll("kiril", b"jpeg-bytes")
    assert out == {"success": True, "name": "kiril"}
    assert state["calls"] == [("create", "kiril"), ("register", "kiril")]
    assert state["upload"][2] == b"jpeg-bytes"


@pytest.mark.parametrize("bad", ["kiril/", "a b", ".."])
async def test_a_bad_name_never_reaches_frigate(fake, bad):
    client, state = fake
    with pytest.raises(FrigateError):
        await client.register(bad, b"img")
    assert state["calls"] == []


async def test_a_4xx_raises_with_the_status(fake):
    client, state = fake
    state["fail"] = True
    with pytest.raises(FrigateError) as e:
        await client.register("kiril", b"img")
    assert e.value.status == 400
