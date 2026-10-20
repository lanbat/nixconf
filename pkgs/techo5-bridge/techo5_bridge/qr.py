"""The sign-in code: the who page's link for the room (pkgs/assistant-router
tap.py) as a QR code, for the device to show full screen."""
from __future__ import annotations

import io

import segno


def png(link: str) -> bytes:
    out = io.BytesIO()
    # Large enough to scan across a kitchen from a phone; the quiet zone in white.
    segno.make(link, error="m").save(out, kind="png", scale=12, border=4, dark="#000000", light="#ffffff")
    return out.getvalue()
