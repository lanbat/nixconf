#!/usr/bin/env python3
"""Snapclient, restarted when it stops being a client of snapserver
(modules/core/snapclient.nix).

snapclient 0.35 can hang when its connection drops, and a hung process looks
healthy to systemd (it ignores SIGTERM too). Run once a minute, this looks at
the TCP connection snapclient holds to snapserver (`ss -tin`) and counts a
check as bad when

  - there is no connection at all,
  - nothing has been sent or received on it for STALE_MS: a working client
    exchanges time-sync messages with the server about every second, music or
    not, so a connection quiet for half a minute is dead, or
  - the receive queue holds more than BACKLOG bytes: snapclient is not reading
    its socket.

The last two are the hang that leaves the socket open: on 2026-10-13 the server
dropped the Pi 5's client at 22:35 and the process sat in a futex for eight days
with 690 kB unread and no traffic in either direction, and a check that only
looked for a connection never fired. Two bad checks in a row restart snapclient.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Connection:
    recv_q: int
    pid: int | None
    last_sent_ms: int | None
    last_received_ms: int | None


# [State] Recv-Q Send-Q Local Peer [users]. `ss` leaves the State column out
# when asked for `state established`, which is how this runs; it is there
# without that filter. A pattern that wanted it never found snapclient's
# connection on a Pi 5 (2026-10-22), and restarted a healthy, idle client every
# two minutes: 638 times in a day.
_HEAD = re.compile(r"^(?:[A-Za-z][\w-]*\s+)?(\d+)\s+\d+\s+\S+\s+\S+(?:\s+(.*))?$")
_PID = re.compile(r"pid=(\d+)")


def parse(output: str) -> list[Connection]:
    """The connections in `ss -Htinp` output, with or without the State
    column: a line per socket, then its indented details."""
    found: list[Connection] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t":
            if not found:
                continue
            for key, attr in (("lastsnd", "last_sent_ms"), ("lastrcv", "last_received_ms")):
                match = re.search(rf"\b{key}:(\d+)", line)
                if match:
                    setattr(found[-1], attr, int(match.group(1)))
            continue
        head = _HEAD.match(line.strip())
        if head:
            pid = _PID.search(head.group(2) or "")
            found.append(Connection(int(head.group(1)), int(pid.group(1)) if pid else None, None, None))
    return found


def problem(c: Connection, stale_ms: int, backlog: int) -> str | None:
    """What is wrong with one connection, or None when it looks alive."""
    quiet = max(c.last_sent_ms or 0, c.last_received_ms or 0)
    if quiet > stale_ms:
        return f"its connection to snapserver has had no traffic one way for {quiet // 1000} s"
    if c.recv_q > backlog:
        return f"it is not reading its connection ({c.recv_q} bytes waiting)"
    return None


def verdict(connections: list[Connection], pid: int, stale_ms: int, backlog: int) -> str | None:
    """Why snapclient (process `pid`) has no working connection, or None when
    one of its connections looks alive."""
    mine = [c for c in connections if c.pid == pid]
    if not mine:
        return "no connection to snapserver"
    problems = [problem(c, stale_ms, backlog) for c in mine]
    return None if None in problems else problems[0]


def main() -> None:
    env = os.environ
    systemctl = env.get("SYSTEMCTL", "systemctl")
    ss = env.get("SS", "ss")
    unit = env.get("UNIT", "snapclient")
    state = env.get("STATE", "/run/snapclient-watchdog")
    port = env.get("PORT", "1704")
    stale_ms = int(env.get("STALE_MS", "30000"))
    backlog = int(env.get("BACKLOG", "262144"))

    pid = int(subprocess.run([systemctl, "show", "-p", "MainPID", "--value", unit],
                             capture_output=True, text=True, check=False).stdout.strip() or "0")
    if pid == 0:  # not running: systemd's own restart policy has it
        _forget(state)
        return
    out = subprocess.run([ss, "-Htinp", "state", "established", f"( dport = :{port} )"],
                         capture_output=True, text=True, check=False).stdout
    why = verdict(parse(out), pid, stale_ms, backlog)
    if why is None:
        _forget(state)
    elif os.path.exists(state):
        print(f"snapclient: {why}, for two checks; restarting it", flush=True)
        _forget(state)
        subprocess.run([systemctl, "restart", unit], check=False)
    else:
        print(f"snapclient: {why}; checking again in a minute", flush=True)
        open(state, "w").close()


def _forget(state: str) -> None:
    try:
        os.remove(state)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    sys.exit(main())
