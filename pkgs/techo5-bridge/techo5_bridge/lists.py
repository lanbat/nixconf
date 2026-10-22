"""Home Assistant to-do lists and calendars for the Echo Show bridge."""

from __future__ import annotations

import json
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

LOG = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_MARKUP = re.compile(r"[`\*_\[\]<>]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_ITEMS = 200
_ELLIPSIS = "…"


@dataclass
class Item:
    uid: str
    title: str
    due: datetime | None
    list: str


@dataclass
class Event:
    title: str
    start: datetime
    all_day: bool


def clean(text: str, limit: int = 60) -> str:
    one = _CONTROL.sub(" ", text).replace("\n", " ").replace("\r", " ")
    one = _MARKUP.sub("", one)
    one = " ".join(one.split())
    if len(one) <= limit:
        return one
    if limit <= len(_ELLIPSIS):
        return _ELLIPSIS[:limit]
    return one[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def _unwrap(response: dict) -> dict:
    if "service_response" in response:
        return response["service_response"]
    return response


def _parse_due(raw: str) -> datetime:
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return datetime.fromisoformat(f"{raw}T00:00:00")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _localize(due: datetime, tz: ZoneInfo) -> datetime:
    if due.tzinfo is not None:
        return due
    try:
        return due.replace(tzinfo=tz)
    except Exception:
        return due.replace(fold=1, tzinfo=tz)


def parse_items(entity: str, response: dict) -> list[Item]:
    body = _unwrap(response)
    block = body.get(entity) or {}
    raw_items = block.get("items") or []
    out: list[Item] = []
    for row in raw_items[:_MAX_ITEMS]:
        if row.get("status") != "needs_action":
            continue
        due = None
        if due_raw := row.get("due"):
            due = _parse_due(str(due_raw))
        out.append(
            Item(
                uid=str(row.get("uid", "")),
                title=str(row.get("summary") or ""),
                due=due,
                list=entity,
            )
        )
    return out


def pick_reminders(items: list[Item], now: datetime, limit: int = 5) -> list[Item]:
    tz = now.tzinfo
    if tz is None:
        raise ValueError("now must be timezone-aware")
    end_of_today = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    dated: list[tuple[datetime, Item]] = []
    for item in items:
        if item.due is None:
            continue
        due = _localize(item.due, tz)
        dated.append((due, Item(item.uid, item.title, due, item.list)))

    overdue = sorted((d for d in dated if d[0] <= now), key=lambda x: x[0])
    later_today = sorted(
        (d for d in dated if now < d[0] < end_of_today),
        key=lambda x: x[0],
    )
    picked = [item for _, item in overdue + later_today]
    return picked[:limit]


def pick_todos(items: list[Item], limit: int = 5) -> tuple[list[Item], int]:
    undated = [i for i in items if i.due is None]
    picked = undated[:limit]
    return picked, max(0, len(undated) - len(picked))


def _parse_event_start(raw: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        d = date.fromisoformat(raw)
        start = datetime(d.year, d.month, d.day, tzinfo=tz)
        return start, True
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    start = datetime.fromisoformat(raw)
    if start.tzinfo is None:
        start = _localize(start, tz)
    return start, False


def _parse_event_end(raw: str, tz: ZoneInfo, all_day: bool) -> datetime:
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        d = date.fromisoformat(raw)
        if all_day:
            # Home Assistant all-day end dates are exclusive.
            d = d - timedelta(days=1)
        end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
        return end
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    end = datetime.fromisoformat(raw)
    if end.tzinfo is None:
        end = _localize(end, tz)
    return end


def parse_events(response: dict, now: datetime, limit: int = 3) -> list[Event]:
    tz = now.tzinfo
    if tz is None:
        raise ValueError("now must be timezone-aware")
    body = _unwrap(response)
    candidates: list[Event] = []
    for block in body.values():
        if not isinstance(block, dict):
            continue
        for row in block.get("events") or []:
            start_raw = str(row.get("start") or "")
            end_raw = str(row.get("end") or start_raw)
            if not start_raw:
                continue
            start, all_day = _parse_event_start(start_raw, tz)
            end = _parse_event_end(end_raw, tz, all_day)
            if end < now:
                continue
            candidates.append(
                Event(title=str(row.get("summary") or ""), start=start, all_day=all_day)
            )
    candidates.sort(key=lambda e: e.start)
    return candidates[:limit]


class Ha:
    def __init__(
        self,
        url: str,
        token: str,
        session: aiohttp.ClientSession,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._session = session
        self._ssl = ssl_context

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict) -> dict | None:
        url = f"{self._url}/api/services/{path}?return_response"
        try:
            async with self._session.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=_TIMEOUT,
                ssl=self._ssl,
            ) as resp:
                if resp.status >= 400:
                    LOG.warning("Home Assistant %s returned HTTP %s", path, resp.status)
                    return None
                text = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as exc:
            LOG.warning("Home Assistant %s failed: %s", path, exc)
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            LOG.warning("Home Assistant %s returned invalid JSON", path)
            return None

    async def todo_items(self, entity: str) -> dict | None:
        return await self._post("todo/get_items", {"entity_id": entity})

    async def events(self, calendars: list[str], hours: int = 48) -> dict | None:
        start = datetime.now(tz=ZoneInfo("UTC")).replace(microsecond=0).isoformat()
        end = (datetime.now(tz=ZoneInfo("UTC")) + timedelta(hours=hours)).replace(
            microsecond=0
        ).isoformat()
        return await self._post(
            "calendar/get_events",
            {
                "entity_id": calendars,
                "start_date_time": start,
                "end_date_time": end,
            },
        )

    async def complete(self, entity: str, uid: str) -> bool:
        result = await self._post(
            "todo/update_item",
            {"entity_id": entity, "item": uid, "status": "completed"},
        )
        return result is not None
