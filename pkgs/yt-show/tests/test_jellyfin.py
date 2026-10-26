"""jellyfin.py: the library mapping and the transcoded stream address."""
from yt_show.jellyfin import Jellyfin, _item


def test_a_movie_a_series_and_an_episode():
    movie = _item({"Id": "m1", "Name": "The Matrix", "Type": "Movie",
                   "RunTimeTicks": 136 * 10_000_000, "ProductionYear": 1999})
    assert movie == {"source": "jellyfin", "kind": "movie", "id": "m1",
                     "title": "The Matrix", "channel": "", "seconds": 136}
    series = _item({"Id": "s1", "Name": "The Wire", "Type": "Series", "ChildCount": 60})
    assert series["kind"] == "series" and series["count"] == 60
    episode = _item({"Id": "e1", "Name": "Pilot", "Type": "Episode",
                     "SeriesName": "The Wire", "RunTimeTicks": 59 * 10_000_000})
    assert episode["kind"] == "episode" and episode["channel"] == "The Wire"


def test_an_item_without_an_id_or_a_name_is_dropped():
    assert _item({"Id": "", "Name": "x"}) is None
    assert _item({"Id": "x", "Name": ""}) is None
    assert _item("not a dict") is None


def test_the_stream_is_the_transcoder_on_the_screens_address(tmp_path):
    key = tmp_path / "key"
    key.write_text("secret-key\n")
    jellyfin = Jellyfin("http://127.0.0.1:8096", "http://192.168.1.10:8096", str(key))
    url = jellyfin.stream("abc123")
    assert url.startswith("http://192.168.1.10:8096/Videos/abc123/stream.mp4?")
    assert "VideoCodec=h264" in url and "AudioCodec=aac" in url
    assert "MaxHeight=720" in url and "api_key=secret-key" in url


def test_a_description_and_a_picture_from_the_library():
    item = _item({"Id": "m1", "Name": "The Matrix", "Type": "Movie",
                  "Overview": "A hacker learns the truth."},
                 "http://192.168.1.10:8096", "secret-key")
    assert item["description"] == "A hacker learns the truth."
    assert item["thumb"] == ("http://192.168.1.10:8096/Items/m1/Images/Primary"
                             "?maxWidth=480&quality=90&api_key=secret-key")
