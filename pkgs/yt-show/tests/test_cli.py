"""__main__._config: every source's settings reach the Config."""
from argparse import Namespace

from yt_show.__main__ import _config


def test_the_cli_carries_every_sources_settings():
    a = Namespace(
        invidious="http://invidious", invidious_public="https://youtube.example",
        ytdlp="yt-dlp", ffmpeg="ffmpeg", cache_dir="/cache", media_url_base="https://yt.example",
        no_remux=False, max_minutes=15, remux_seconds=30, cache_days=7, max_cache_mb=4096,
        ma_url="http://127.0.0.1:8095", ma_token_file="/run/ma-token", ma_limit=40,
        jellyfin_url="http://127.0.0.1:8096", jellyfin_public_url="http://192.0.2.1:8096",
        jellyfin_key_file="/run/jellyfin-key", jellyfin_limit=20,
    )
    cfg = _config(a)
    assert cfg.invidious_url == "http://invidious"
    assert cfg.ma_url == "http://127.0.0.1:8095" and cfg.ma_token_file == "/run/ma-token"
    assert cfg.jellyfin_url == "http://127.0.0.1:8096"
    assert cfg.jellyfin_public_url == "http://192.0.2.1:8096"
    assert cfg.jellyfin_key_file == "/run/jellyfin-key"
