#!/usr/bin/env python3
"""Linux Voice Assistant, restarted when it stays disconnected from Home Assistant
(modules/core/voice-satellite-lva.nix).

LVA logs "Disconnected from Home Assistant; waiting for reconnection" when the
connection drops, and "Connected to Home Assistant - Configuration handshake
completed" when Home Assistant's side comes back. Usually that takes seconds.
On 2026-11-24 (Pi 5) LVA logged the disconnect while Home Assistant kept its
original TCP connection open and answering keep-alives: Home Assistant saw
nothing to reconnect, LVA waited for a reconnection that never came, and the
satellite was offline for three days until it was restarted.

Every INTERVAL_S seconds this reads the unit's journal for its current run. When
the last of those two events is the disconnect and it is at least GRACE_S old,
the unit restarts, which gives Home Assistant a fresh connection to make. The
restart starts a new run with no events yet, so a Home Assistant that is down
for hours is restarted for once and then left alone.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

DISCONNECTED = "Disconnected from Home Assistant"
CONNECTED = "Connected to Home Assistant - Configuration handshake completed"


def disconnected_since(lines: list[str]) -> float | None:
    """When the connection was last lost, if that is its latest event.

    `lines` are journal lines in `short-unix` format ("<seconds>.<micro> host
    unit[pid]: message"), oldest first. None when LVA is connected, or has not
    reported either event yet.
    """
    since = None
    for line in lines:
        if CONNECTED in line:
            since = None
        elif DISCONNECTED in line:
            try:
                since = float(line.split(" ", 1)[0])
            except ValueError:
                continue
    return since


def stuck(lines: list[str], now: float, grace_s: float) -> bool:
    since = disconnected_since(lines)
    return since is not None and now - since >= grace_s


def log(message: str) -> None:
    print(f"lva-connection-watchdog: {message}", flush=True)


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def check(env: dict[str, str], grace_s: float) -> None:
    unit, systemctl, journalctl = env["UNIT"], env["SYSTEMCTL"], env["JOURNALCTL"]
    if run(env, systemctl, "is-active", "--quiet", unit).returncode != 0:
        return
    invocation = run(env, systemctl, "show", "-p", "InvocationID", "--value", unit).stdout.strip()
    if not invocation:
        return
    journal = run(env, journalctl, "-o", "short-unix", "--no-pager", f"_SYSTEMD_INVOCATION_ID={invocation}")
    if journal.returncode != 0:
        return
    lines = journal.stdout.splitlines()
    if stuck(lines, time.time(), grace_s):
        log(f"{unit} has been disconnected from Home Assistant for over {int(grace_s)} s; restarting it")
        run(env, systemctl, "restart", unit)


def main() -> None:
    env = dict(os.environ)
    grace_s = float(env.get("GRACE_S", "300"))
    interval_s = float(env.get("INTERVAL_S", "30"))
    log(f"watching {env['UNIT']}; restart after {int(grace_s)} s disconnected")
    while True:
        try:
            check(env, grace_s)
        except OSError as e:
            log(f"check failed: {e}")
        time.sleep(interval_s)


if __name__ == "__main__":
    sys.exit(main())
