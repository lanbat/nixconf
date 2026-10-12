import json

from assistant_router.people import People
from assistant_router.people_store import PeopleStore

BASE = {"kiril": "Kiril", "maria": "Maria"}


def test_overlay_merges_over_the_base(tmp_path):
    path = tmp_path / "people.json"
    path.write_text(json.dumps({"ivo": "Ivo", "kiril": "Kiril-renamed"}))
    people = People(dict(BASE))
    PeopleStore(people, str(path))
    assert people.names == {"kiril": "Kiril-renamed", "maria": "Maria", "ivo": "Ivo"}


def test_a_missing_or_bad_file_is_just_the_base(tmp_path):
    people = People(dict(BASE))
    PeopleStore(people, str(tmp_path / "nope.json"))
    assert people.names == dict(BASE)

    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    again = People(dict(BASE))
    PeopleStore(again, str(bad))
    assert again.names == dict(BASE)


def test_add_is_persisted_and_survives_a_restart(tmp_path):
    path = tmp_path / "people.json"
    people = People(dict(BASE))
    PeopleStore(people, str(path)).add("jane", "Jane")
    assert "talking to Jane" in people.line("Kitchen", now=0.0, voice={"jane": 0.9})

    again = People(dict(BASE))
    PeopleStore(again, str(path))
    assert again.names["jane"] == "Jane"


def test_remove_unpersists(tmp_path):
    path = tmp_path / "people.json"
    store = PeopleStore(People(dict(BASE)), str(path))
    store.add("jane", "Jane")
    store.remove("jane")
    assert "jane" not in store.people.names
    assert json.loads(path.read_text()) == {}


def test_dropping_someone_from_nix_still_applies(tmp_path):
    path = tmp_path / "people.json"
    PeopleStore(People(dict(BASE)), str(path)).add("jane", "Jane")

    redeployed = People({"kiril": "Kiril"})
    PeopleStore(redeployed, str(path))
    assert "maria" not in redeployed.names
    assert redeployed.names["jane"] == "Jane"
