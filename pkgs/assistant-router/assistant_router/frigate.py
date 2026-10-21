"""Talk to Frigate's face library over its local API.

Frigate keeps a face library: one named folder per person, holding the images
it matches against. When it recognises someone, the folder's name is the
object's sub_label, which the person-mapper turns into a person key; so a
library name and a person key are the same string, and enrolling a new person
is a folder create plus a face-image register against this API. The router and
Frigate run on one host and Frigate has no auth, so no token is needed.

Thin: a call is one request and the response dict, passed through unchanged.
Only the library name is checked, up front, so nothing but a person key can
reach a path. Unit-tested against a fake Frigate (tests/test_frigate.py).
"""
from __future__ import annotations

import json
import re

import aiohttp

# A library name is a person key: letters, digits, underscore, hyphen.
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# A face image's file name, as Frigate lists it (akadmin_1792518459.451025.webp).
_FILE = re.compile(r"[A-Za-z0-9_.-]{1,120}")
# An event's id (1792518459.451025-ab12cd).
_EVENT = re.compile(r"[0-9]{6,14}\.[0-9]{1,8}-[a-z0-9]{1,16}")


class FrigateError(Exception):
    """A face-library call failed: a non-2xx response or a connection error."""

    def __init__(self, what: str, status: int | None = None, detail: str = "") -> None:
        super().__init__(f"{what}: {detail}" if detail else what)
        self.what, self.status, self.detail = what, status, detail


def safe_name(name: str) -> str:
    """A face-library name: the person key, or a FrigateError if it is not one."""
    n = (name or "").strip()
    if not _NAME.match(n):
        raise FrigateError(f"bad face name {name!r}")
    return n


def _body(text: str) -> dict:
    """The response body as a dict, or {} when Frigate sent none or not JSON."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Frigate:
    def __init__(self, base_url: str, session: aiohttp.ClientSession | None = None,
                 timeout: float = 20.0) -> None:
        self.base = base_url.rstrip("/")
        self._session = session
        self._owns = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def aclose(self) -> None:
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None

    async def _call(self, method: str, path: str, *, data=None, payload=None) -> dict:
        session = await self._ensure()
        try:
            async with session.request(method, self.base + path, data=data,
                                       json=payload) as resp:
                status, text = resp.status, await resp.text()
        except aiohttp.ClientError as e:
            raise FrigateError(f"{method} {path}", detail=str(e)) from e
        if status >= 400:
            raise FrigateError(f"{method} {path}", status=status,
                               detail=str(_body(text) or text)[:200])
        return _body(text)

    async def list(self) -> dict[str, list[str]]:
        """Every registered face and the image files under its name."""
        data = await self._call("GET", "/api/faces")
        return {k: list(v) for k, v in data.items() if isinstance(v, list)}

    async def create(self, name: str) -> dict:
        """Make a person's folder. Idempotent; the body's success flag is not reliable."""
        return await self._call("POST", f"/api/faces/{safe_name(name)}/create")

    async def register(self, name: str, image: bytes, filename: str = "capture.jpg") -> dict:
        """Upload a face image for a person and register it for recognition."""
        data = aiohttp.FormData()
        data.add_field("file", image, filename=filename, content_type="image/jpeg")
        return await self._call("POST", f"/api/faces/{safe_name(name)}/register", data=data)

    async def recognize(self, image: bytes, filename: str = "probe.jpg") -> dict:
        """Match an image against the library: {"success", "name", "confidence"},
        the name None when nobody in the library matches. Frigate (0.16, 0.17)
        answers {"success", "face_name", "score"}, with face_name "unknown" for
        a face below its unknown_score; an older "name"/"confidence" is read too."""
        data = aiohttp.FormData()
        data.add_field("file", image, filename=filename, content_type="image/jpeg")
        body = await self._call("POST", "/api/faces/recognize", data=data)
        name = body.get("face_name", body.get("name"))
        if not isinstance(name, str) or not name or name == "unknown":
            name = None
        score = body.get("score", body.get("confidence"))
        return {"success": bool(body.get("success")) and name is not None, "name": name,
                "confidence": float(score) if type(score) in (int, float) else None}

    async def delete(self, name: str, ids: list[str]) -> dict:
        """Remove face images from a person; all of them, to drop the person."""
        return await self._call("POST", f"/api/faces/{safe_name(name)}/delete",
                                payload={"ids": list(ids)})

    async def rename(self, old: str, new: str) -> dict:
        """Give a person's face library a new name."""
        return await self._call("PUT", f"/api/faces/{safe_name(old)}/rename",
                                payload={"new_name": safe_name(new)})

    async def enroll(self, name: str, image: bytes, filename: str = "capture.jpg") -> dict:
        """Register a new person: make the folder, then register the image."""
        name = safe_name(name)
        await self.create(name)
        return await self.register(name, image, filename)

    async def image(self, name: str, file: str) -> bytes:
        """One of a person's face images, as Frigate serves it."""
        if not _FILE.fullmatch(file or "") or ".." in file:
            raise FrigateError(f"bad face file {file!r}")
        path = f"/clips/faces/{safe_name(name)}/{file}"
        session = await self._ensure()
        try:
            async with session.get(self.base + path) as resp:
                if resp.status >= 400:
                    raise FrigateError(f"GET {path}", status=resp.status)
                return await resp.read()
        except aiohttp.ClientError as e:
            raise FrigateError(f"GET {path}", detail=str(e)) from e

    async def thumbnail(self, event: str) -> bytes:
        """A tracked object's thumbnail, as Frigate keeps it for the event."""
        if not _EVENT.fullmatch(event or ""):
            raise FrigateError(f"bad event {event!r}")
        path = f"/api/events/{event}/thumbnail.jpg"
        session = await self._ensure()
        try:
            async with session.get(self.base + path) as resp:
                if resp.status >= 400:
                    raise FrigateError(f"GET {path}", status=resp.status)
                return await resp.read()
        except aiohttp.ClientError as e:
            raise FrigateError(f"GET {path}", detail=str(e)) from e

    async def move(self, name: str, into: str) -> int:
        """`name`'s face images become `into`'s (an admin linked the two): a
        rename when `into` has no folder, else each copied over and then
        dropped. How many moved."""
        name, into = safe_name(name), safe_name(into)
        faces = await self.list()
        mine = faces.get(name) or []
        if not mine or name == into:
            return 0
        if into not in faces:
            await self.rename(name, into)
            return len(mine)
        for file in mine:
            await self.register(into, await self.image(name, file), file)
        await self.delete(name, mine)
        return len(mine)
