import json
import logging

from assistant_router.hass import HassError
from assistant_router.selfcheck import check, run


class Ha:
    def __init__(self, states=None, fail=None, refused_after=None):
        self._states = states
        self.fail = fail or {}  # "states" | "todo" | "calendar" -> HassError
        self.refused = False
        self.calls = []

    async def states(self, domain=None):
        if "states" in self.fail:
            raise self.fail["states"]
        return self._states

    async def call(self, domain, service, data, response=False):
        self.calls.append((domain, service, data, response))
        if domain in self.fail:
            if self.fail[domain].status in (401, 403):
                self.refused = True
            raise self.fail[domain]
        return {}


ENTITIES = [{"entity_id": "todo.kiril_todo", "name": "x", "state": "0"},
            {"entity_id": "calendar.family", "name": "y", "state": "off"}]


async def test_everything_it_needs_is_allowed():
    ha = Ha(ENTITIES)
    assert await check(ha) == {"home_assistant": "ok", "entities": 2, "todo": "ok", "calendar": "ok"}
    assert [c[:2] for c in ha.calls] == [("todo", "get_items"), ("calendar", "get_events")]
    assert all(c[3] for c in ha.calls)  # both are services that answer, asked for the answer


async def test_it_only_reads():
    ha = Ha(ENTITIES)
    await check(ha)
    assert {c[1] for c in ha.calls} <= {"get_items", "get_events"}


async def test_no_list_or_calendar_yet_is_said_so():
    assert await check(Ha([])) == {"home_assistant": "ok", "entities": 0, "todo": "none", "calendar": "none"}


async def test_a_refused_token_is_one_request_and_names_the_status():
    assert await check(Ha(None, {"states": HassError("GET", status=401)})) == \
        {"home_assistant": "refused", "status": 401}
    assert await check(Ha(None, {"states": HassError("GET", status=403)})) == \
        {"home_assistant": "refused", "status": 403}


async def test_a_todo_refusal_stops_the_calendar_call():
    ha = Ha(ENTITIES, {"todo": HassError("POST", status=403)})
    out = await check(ha)
    assert out["todo"] == "refused" and out["calendar"] == "none"
    assert [c[0] for c in ha.calls] == ["todo"]  # a refusal means no more calls: Home Assistant would ban the address


async def test_a_calendar_that_will_not_answer_is_an_error_not_a_refusal():
    out = await check(Ha(ENTITIES, {"calendar": HassError("POST", status=400)}))
    assert out["todo"] == "ok" and out["calendar"] == "error" and out["status"] == 400


async def test_down_is_tried_again_and_the_last_answer_is_logged(caplog):
    class Late(Ha):
        n = 0

        async def states(self, domain=None):
            Late.n += 1
            if Late.n < 3:
                raise HassError("GET")
            return []

    with caplog.at_level(logging.INFO, logger="assistant_router"):
        out = await run(Late(), retry_s=0, tries=5)
    assert out["home_assistant"] == "ok" and Late.n == 3
    assert json.loads(caplog.records[-1].message)["home_assistant"] == "ok"


async def test_down_for_good_is_logged_as_down(caplog):
    with caplog.at_level(logging.INFO, logger="assistant_router"):
        out = await run(Ha(None, {"states": HassError("GET")}), retry_s=0, tries=3)
    assert out == {"home_assistant": "down"}
