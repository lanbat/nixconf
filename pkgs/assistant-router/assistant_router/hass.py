"""Talk to Home Assistant over its REST API, as the voice satellites' user.

The token is the voice satellites' (ha-voice-token): an ordinary user, not an
administrator, which may read states and call ordinary services (todo.*,
calendar.*), and nothing that changes Home Assistant's configuration.

Home Assistant bans an address after a few refused logins (ip_bans.yaml), and
this one is the server's own: so after the first 401 or 403 the client calls
nothing more until the router restarts, and says so in the log.

Thin, as frigate.py: a call is one request, the body passed through. Domain,
service and entity names are checked up front. Unit-tested against a fake Home
Assistant (tests/test_hass.py).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

LOG = logging.getLogger("assistant_router")

_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
ENTITY = re.compile(r"^[a-z0-9_]{1,64}\.[a-z0-9_]{1,128}$")


class HassError(Exception):
    """A call failed: refused, a non-2xx response, or no answer."""

    def __init__(self, what: str, status: int | None = None, detail: str = "") -> None:
        super().__init__(f"{what}: {detail}" if detail else what)
        self.what, self.status, self.detail = what, status, detail


def _body(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


class Hass:
    def __init__(self, base_url: str, token: str, session: aiohttp.ClientSession | None = None,
                 timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self._token = token
        self._session = session
        self._owns = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self.refused = False  # the token was refused once: no more calls until a restart

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def aclose(self) -> None:
        if self._owns and self._session is not None:
            await self._session.close()
            self._session = None

    async def _call(self, method: str, path: str, payload: dict | None = None,
                    poison: bool = True) -> Any:
        if self.refused:
            raise HassError(f"{method} {path}", status=401, detail="the token was refused before")
        session = await self._ensure()
        try:
            async with session.request(method, self.base + path, json=payload,
                                       headers={"Authorization": f"Bearer {self._token}"}) as resp:
                status, text = resp.status, await resp.text()
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HassError(f"{method} {path}", detail=str(e)) from e
        if status in (401, 403) and poison:
            self.refused = True
            LOG.warning(json.dumps({"home_assistant": "token refused", "status": status,
                                    "calls": "stopped until the router restarts"}))
        if status >= 400:
            raise HassError(f"{method} {path}", status=status, detail=text[:200])
        return _body(text)

    async def states(self, domain: str | None = None) -> list[dict]:
        """Each entity's {"entity_id", "name", "state"}, of one domain or all, by name."""
        if domain is not None and not _NAME.match(domain):
            raise HassError(f"bad domain {domain!r}")
        data = await self._call("GET", "/api/states")
        out = []
        for s in data if isinstance(data, list) else []:
            entity = s.get("entity_id") if isinstance(s, dict) else None
            if not isinstance(entity, str) or not ENTITY.match(entity):
                continue
            if domain is not None and not entity.startswith(domain + "."):
                continue
            attrs = s.get("attributes") if isinstance(s.get("attributes"), dict) else {}
            name = attrs.get("friendly_name")
            out.append({"entity_id": entity, "name": name if isinstance(name, str) and name else entity,
                        "state": str(s.get("state", ""))})
        return sorted(out, key=lambda e: (e["name"].casefold(), e["entity_id"]))

    async def call(self, domain: str, service: str, data: dict, response: bool = False) -> dict:
        """Call a service: its response (a service that returns one, with
        `response`), else {}. Home Assistant refuses return_response on a
        service that has none, so only ask for it where there is one."""
        if not _NAME.match(domain) or not _NAME.match(service):
            raise HassError(f"bad service {domain}.{service}")
        body = await self._call("POST", f"/api/services/{domain}/{service}"
                                        + ("?return_response" if response else ""), data)
        if response and isinstance(body, dict) and isinstance(body.get("service_response"), dict):
            return body["service_response"]
        return {}

    async def services(self, domain: str) -> list[str]:
        """The service names of one domain, as Home Assistant lists them
        (`esphome`'s `<node>_play_video` actions, for instance), sorted.

        A refusal here (an ordinary user may not read the service list on some
        versions) is not a token refusal: it fails this lookup only, and the
        client keeps calling Home Assistant for lists and reminders."""
        if not _NAME.match(domain):
            raise HassError(f"bad domain {domain!r}")
        data = await self._call("GET", "/api/services", poison=False)
        for entry in data if isinstance(data, list) else []:
            if not isinstance(entry, dict) or entry.get("domain") != domain:
                continue
            found = entry.get("services")
            if isinstance(found, dict):
                return sorted(key for key in found if isinstance(key, str) and _NAME.match(key))
        return []

