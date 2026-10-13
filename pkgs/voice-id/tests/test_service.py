from aiohttp import web

from voice_id.service import VoiceId, make_api
from voice_id.voiceprints import Store
from test_voiceprints import ALEX, ALEX_ISH, NOBODY, SAM, unit


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def fake_router(aiohttp_server):
    seen = []

    async def voice(request):
        seen.append(await request.json())
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/v1/voice", voice)
    srv = await aiohttp_server(app)
    return seen, f"http://{srv.host}:{srv.port}/v1/voice"


async def test_an_utterance_is_scored_and_sent_to_the_router(tmp_path, aiohttp_server):
    seen, url = await fake_router(aiohttp_server)
    store = Store(str(tmp_path))
    store.enrol("alex", [ALEX, ALEX, ALEX])
    v = VoiceId(store, embed=None, router_url=url, clock=Clock())
    await v.utterance("good morning", ALEX_ISH)
    await v.close()
    assert seen[0]["text"] == "good morning"
    assert seen[0]["scores"]["alex"] > 0.9


async def test_no_voiceprints_or_speech_is_an_empty_report(tmp_path, aiohttp_server):
    # The router waits for each sentence's report: an empty one ends the wait.
    seen, url = await fake_router(aiohttp_server)
    v = VoiceId(Store(str(tmp_path)), embed=None, router_url=url, clock=Clock())
    await v.utterance("hi", ALEX)          # nobody enrolled yet
    await v.utterance("hello", None)       # too little speech
    await v.utterance("   ", None)         # no transcript: nothing to match
    await v.close()
    assert [(r["text"], r["scores"]) for r in seen] == [("hi", {}), ("hello", {})]


def test_enrolment_collects_the_conversation_until_there_is_enough(tmp_path):
    clock = Clock()
    v = VoiceId(Store(str(tmp_path)), embed=None, clock=clock)
    v.recent.add("remember me I'm Alex", ALEX, clock())
    v.recent.add("what a nice day", ALEX_ISH, clock())
    first = v.enrol("alex", ["Remember me, I'm Alex.", "What a nice day!", "never said"])
    assert first == {"enrolled": False, "utterances": 2, "needed": 3, "available": True}
    clock.t += 30
    v.recent.add("I like cooking", unit(0.9, 0.1, 0, 0.1), clock())
    second = v.enrol("alex", ["I like cooking"])
    assert second["enrolled"] and second["utterances"] == 3
    assert v.store.scores(ALEX)["alex"] > 0.9


def test_unknown_voices_are_never_written(tmp_path):
    clock = Clock()
    v = VoiceId(Store(str(tmp_path)), embed=None, clock=clock)
    v.recent.add("just passing", NOBODY, clock())
    assert not (tmp_path / "voiceprints.json").exists()


def test_learning_and_forgetting(tmp_path):
    clock = Clock()
    v = VoiceId(Store(str(tmp_path)), embed=None, clock=clock)
    v.store.enrol("alex", [ALEX, ALEX, ALEX])
    v.recent.add("turn on the radio", ALEX_ISH, clock())
    v.recent.add("someone else", SAM, clock())
    assert v.learn("alex", "Turn on the radio.")
    assert not v.learn("alex", "someone else")   # sounds like someone else: refused
    assert not v.learn("alex", "never heard")
    assert v.forget("alex")
    assert v.store.scores(ALEX) == {}


def test_a_locked_layer_knows_nobody(tmp_path):
    clock = Clock()
    v = VoiceId(Store(str(tmp_path / "locked")), embed=None, clock=clock)
    v.recent.add("remember me", ALEX, clock())
    v.recent.add("please", ALEX, clock())
    v.recent.add("now", ALEX, clock())
    assert v.enrol("alex", ["remember me", "please", "now"])["enrolled"] is False


async def test_the_api(tmp_path, aiohttp_client):
    clock = Clock()
    v = VoiceId(Store(str(tmp_path)), embed=None, clock=clock)
    for text in ("one two three", "four five six", "seven eight nine"):
        v.recent.add(text, ALEX, clock())
    c = await aiohttp_client(make_api(v))
    r = await c.post("/enrol", json={"key": "alex", "texts": ["one two three", "four five six", "seven eight nine"]})
    assert (await r.json())["enrolled"]
    assert (await (await c.get("/status")).json()) == {"available": True, "people": ["alex"]}
    assert (await (await c.post("/learn", json={"key": "alex", "text": "one two three"})).json()) == {"learned": True}
    assert (await c.post("/enrol", json={"key": "../etc", "texts": []})).status == 400
    assert (await (await c.delete("/people/alex")).json()) == {"forgotten": True}


async def test_a_robots_ear_is_scored_and_reported_but_not_kept(tmp_path, aiohttp_server, aiohttp_client):
    import base64
    import struct

    seen, url = await fake_router(aiohttp_server)
    store = Store(str(tmp_path))
    store.enrol("alex", [ALEX, ALEX, ALEX])
    heard = []

    def embed(signal, rate):
        heard.append((len(signal), rate))
        return ALEX_ISH

    clock = Clock()
    v = VoiceId(store, embed=embed, router_url=url, clock=clock)
    c = await aiohttp_client(make_api(v))
    # A quiet robot microphone: two seconds of silence and a second of voice.
    import math
    voice = [int(150 * math.sin(i / 7)) for i in range(16000)]
    pcm = struct.pack("<48000h", *([3] * 16000 + voice + [3] * 16000))
    r = await c.post("/embed", json={"text": "good morning", "pcm": base64.b64encode(pcm).decode()})
    assert (await r.json()) == {"reported": True}
    assert heard == [(48000, 16000)]
    assert seen[0]["text"] == "good morning" and seen[0]["scores"]["alex"] > 0.9
    # Not kept: nothing to enrol or learn from.
    assert v.recent.get("good morning", clock()) is None
    assert (await c.post("/embed", json={"text": "hi", "pcm": "not base64!"})).status == 400
    assert (await c.post("/embed", json={"text": "hi"})).status == 400
    await v.close()



def test_levelling_a_quiet_microphone():
    import math

    from voice_id.embedder import levelled, speech_seconds

    quiet_voice = [0.0001] * 16000 + [0.005 * math.sin(i / 7) for i in range(16000)]
    out = levelled(quiet_voice)
    assert out is not None and speech_seconds(out) >= 0.9  # the voice now counts
    assert speech_seconds(quiet_voice) == 0  # and did not before
    # The background stays background: only the voice's second counts.
    assert speech_seconds(out) <= 1.05
    assert levelled([0.003 * math.sin(i / 7) for i in range(32000)]) is None  # a hum: no contrast
    assert levelled([0.0] * 100) is None
