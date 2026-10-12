import json
import math

from voice_id.voiceprints import Recent, Store, normalise_text


def unit(*xs):
    n = math.sqrt(sum(x * x for x in xs))
    return [x / n for x in xs]


ALEX = unit(1, 0, 0, 0)
SAM = unit(0, 1, 0, 0)
ALEX_ISH = unit(0.95, 0.05, 0.1, 0)
NOBODY = unit(0, 0, 0, 1)


def store_with(tmp_path, **people):
    s = Store(str(tmp_path))
    for key, vectors in people.items():
        s._prints[key] = [list(v) for v in vectors]
    return s


def test_scores_for_everyone(tmp_path):
    s = store_with(tmp_path, alex=[ALEX], sam=[SAM])
    scores = s.scores(ALEX_ISH)
    assert set(scores) == {"alex", "sam"}
    assert scores["alex"] > 0.9 > 0.2 > scores["sam"]


def test_no_store_no_scores(tmp_path):
    s = Store(str(tmp_path / "locked"))
    assert not s.available()
    assert s.scores(ALEX) == {}
    assert not s.learn("alex", ALEX)


def test_enrol_needs_three_utterances(tmp_path):
    s = Store(str(tmp_path))
    assert s.enrol("alex", [ALEX, ALEX_ISH]) == 2  # not enough: nothing kept
    assert s.scores(ALEX) == {}
    assert s.enrol("alex", [ALEX, ALEX_ISH, unit(0.9, 0, 0.1, 0.1)]) == 3
    assert s.scores(ALEX)["alex"] > 0.9
    # Saved, and read back by a new store.
    assert Store(str(tmp_path)).scores(ALEX)["alex"] > 0.9
    assert json.load(open(tmp_path / "voiceprints.json"))["version"] == 1
    assert (tmp_path / "CACHEDIR.TAG").read_text().startswith("Signature: 8a477f597d28d172789f06886806bc55")


def test_learning_is_capped_and_refuses_drift(tmp_path):
    s = store_with(tmp_path, alex=[ALEX, ALEX, ALEX])
    assert not s.learn("alex", NOBODY)          # too far from Alex: not learned
    assert not s.learn("mallory", ALEX)         # nobody by that key
    for _ in range(100):
        assert s.learn("alex", ALEX_ISH)
    assert len(s._prints["alex"]) == Store.CAP


def test_forget(tmp_path):
    s = store_with(tmp_path, alex=[ALEX], sam=[SAM])
    assert s.forget("alex")
    assert not s.forget("alex")
    assert set(s.scores(ALEX)) == {"sam"}


def test_recent_utterances_by_text():
    r = Recent(seconds=600)
    r.add("Hello there, Nabu!", ALEX, now=0.0)
    r.add("what time is it", SAM, now=10.0)
    assert r.get("hello there nabu", now=20.0) == ALEX
    assert r.get("Hello there, Nabu", now=700.0) is None  # too old
    assert r.find(["What time is it?", "unheard"], now=20.0) == [SAM]


def test_normalise_text():
    assert normalise_text("  Hello,   there!  ") == "hello there"
    assert normalise_text("What's up?") == "what's up"
