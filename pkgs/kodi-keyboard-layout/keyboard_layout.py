#!/usr/bin/env python3
"""Keep Kodi's physical keyboard layout in step with its on-screen keyboard.

Kodi switches locale.activekeyboardlayout when the on-screen keyboard's layout
button is pressed, but it leaves the physical keyboard's xkb layout alone.
This follows the active on-screen layout and sets input.libinputkeyboardlayout
to the matching xkb layout over Kodi's password-less TCP JSON-RPC; Kodi applies
it live through libinput, so no restart is needed.

The xkb root installed for Kodi (pkgs/xkb-phonetic-root) makes "bg" the
phonetic (ЯВЕРТЪ) layout, the same layout as the on-screen one; Kodi cannot
name a variant itself. modules/storage/tv-box.nix sets XKB_CONFIG_ROOT on the
tv-kodi service and runs this as kodi-keyboard-layout.
"""

import json
import os
import socket
import time

KODI_HOST = os.environ.get("KODI_HOST", "127.0.0.1")
KODI_PORT = int(os.environ.get("KODI_PORT", "9090"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1"))
ACTIVE_SETTING = "locale.activekeyboardlayout"
PHYSICAL_SETTING = "input.libinputkeyboardlayout"

# On-screen layout name (Kodi's identifier "<language> <layout>") to xkb
# layout name. A name not listed here leaves the physical keyboard alone.
PHYSICAL = {
    "English QWERTY": "us",
    "Bulgarian ЯВЕРТЪ": "bg",
}


def log(message: str) -> None:
    print(f"kodi-keyboard-layout: {message}", flush=True)


def rpc(method: str, params: dict):
    """One JSON-RPC call on Kodi's TCP port; returns the reply or None."""
    message = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    try:
        with socket.create_connection((KODI_HOST, KODI_PORT), timeout=2.0) as sock:
            sock.sendall(message)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                try:
                    return json.loads(data.decode())
                except ValueError:
                    # The reply can arrive in pieces; keep reading.
                    continue
    except (OSError, ValueError):
        return None
    return None


def active_layout():
    reply = rpc("Settings.GetSettingValue", {"setting": ACTIVE_SETTING})
    if not isinstance(reply, dict):
        return None
    result = reply.get("result")
    return result.get("value") if isinstance(result, dict) else None


def set_physical(layout: str) -> bool:
    reply = rpc(
        "Settings.SetSettingValue",
        {"setting": PHYSICAL_SETTING, "value": layout},
    )
    return isinstance(reply, dict) and reply.get("result") is True


def main() -> None:
    applied = None
    while True:
        layout = active_layout()
        target = PHYSICAL.get(layout) if layout is not None else None
        if target is not None and target != applied:
            if set_physical(target):
                log(f"{layout} -> {target}")
                applied = target
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
