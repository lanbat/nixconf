"""ma.py: mapping Music Assistant's media items to results."""
from types import SimpleNamespace

from yt_show.ma import _item


def test_a_track_carries_its_artists_and_length():
    item = SimpleNamespace(uri="library://track/1", name="So What", duration=545,
                           artists=[SimpleNamespace(name="Miles Davis")])
    out = _item("track", item)
    assert out == {"source": "music-assistant", "kind": "track", "id": "library://track/1",
                   "title": "So What", "channel": "Miles Davis", "seconds": 545}


def test_a_radio_station_carries_its_provider():
    item = SimpleNamespace(uri="library://radio/1", name="BBC Radio 4", provider="RadioBrowser")
    out = _item("radio", item)
    assert out["kind"] == "radio" and out["channel"] == "RadioBrowser"


def test_an_album_carries_its_track_count():
    item = SimpleNamespace(uri="library://album/1", name="Kind of Blue", track_count=5,
                           artists=[SimpleNamespace(name="Miles Davis")])
    out = _item("album", item)
    assert out["count"] == 5 and out["channel"] == "Miles Davis"


def test_an_item_without_a_uri_or_a_name_is_dropped():
    assert _item("radio", SimpleNamespace(uri="", name="x")) is None
    assert _item("radio", SimpleNamespace(uri="library://radio/1", name="")) is None
    assert _item("radio", SimpleNamespace(uri="not a uri", name="x")) is None
