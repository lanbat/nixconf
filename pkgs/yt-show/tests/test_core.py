"""core.py: what a YouTube address is, and what the Show can play."""
from yt_show.core import duration, page, pick_stream, video_id, watch_url


def test_video_id_from_every_link_shape():
    assert video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://youtu.be/dQw4w9WgXcQ?si=abc") == "dQw4w9WgXcQ"
    assert video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share") == "dQw4w9WgXcQ"
    assert video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_video_id_rejects_what_is_not_one_video():
    assert video_id("https://www.youtube.com/playlist?list=PL123") is None
    assert video_id("https://www.youtube.com/@Computerphile/videos") is None
    assert video_id("") is None
    assert video_id("not a link") is None


def test_watch_url():
    assert watch_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_duration():
    assert duration(59) == "0:59"
    assert duration(605) == "10:05"
    assert duration(3725) == "1:02:05"
    assert duration(None) == ""
    assert duration("nope") == ""
    assert duration(-1) == ""


def test_page_numbers_from_one_and_says_when_another_follows():
    items = [{"id": f"vid{i:08d}", "title": f"Title {i}", "seconds": i} for i in range(7)]
    first, more = page(items, 1, 5)
    assert [r.n for r in first] == [1, 2, 3, 4, 5]
    assert [r.title for r in first] == [f"Title {i}" for i in range(5)]
    assert more is True
    second, more = page(items, 2, 5)
    assert [r.n for r in second] == [6, 7]
    assert more is False


def test_page_skips_a_result_that_is_not_a_video_id():
    items = [{"id": "bad", "title": "Not a video"}, {"id": "dQw4w9WgXcQ", "title": "Real"}]
    results, _ = page(items, 1, 5)
    assert [r.id for r in results] == ["dQw4w9WgXcQ"]


def test_pick_stream_prefers_720p_and_wants_one_muxed_file():
    formats = [
        {"itag": "137", "url": "https://x/1080-video-only"},
        {"itag": "18", "url": "https://x/360"},
        {"itag": "22", "url": "https://x/720"},
    ]
    assert pick_stream(formats) == ("22", "https://x/720")
    assert pick_stream([{"itag": "18", "url": "https://x/360"}]) == ("18", "https://x/360")
    assert pick_stream([{"itag": "137", "url": "https://x/1080"}]) is None
    assert pick_stream([]) is None
    assert pick_stream(None) is None
