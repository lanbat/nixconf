import asyncio

from assistant_router.voice import VoiceReports


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_the_better_of_two_reports_is_kept():
    r = VoiceReports(Clock())
    r.add("Good morning!", {"kiril": 0.55, "maria": 0.2})   # the satellite's microphone
    r.add("good morning", {"kiril": 0.78, "maria": 0.1})    # the robot's, nearer
    assert r.get("good morning") == {"kiril": 0.78, "maria": 0.1}
    r.add("good morning", {"kiril": 0.3})                   # a worse one doesn't replace it
    assert r.get("good morning")["kiril"] == 0.78
    assert r.count("good morning") == 3


def test_an_empty_report_never_replaces_scores():
    r = VoiceReports(Clock())
    r.add("hello there", {"kiril": 0.6})
    r.add("hello there", {})  # the robot heard too little
    assert r.get("hello there") == {"kiril": 0.6}


async def test_a_room_with_an_ear_waits_for_both():
    r = VoiceReports(Clock())

    async def later():
        await asyncio.sleep(0.05)
        r.add("lights on please", {"kiril": 0.8})

    r.add("lights on please", {"kiril": 0.5})
    task = asyncio.create_task(later())
    got = await r.wait("lights on please", timeout=0.5, expect=2)
    await task
    assert got == {"kiril": 0.8}


async def test_one_report_is_enough_after_the_wait():
    r = VoiceReports(Clock())
    r.add("what time is it", {"kiril": 0.5})
    got = await r.wait("what time is it", timeout=0.05, expect=2)  # the ear's never came
    assert got == {"kiril": 0.5}
