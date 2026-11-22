#!/usr/bin/env python3
"""Apply per-client latency offsets to a running snapserver.

Snapserver keeps each client's latency in its own state file and offers no
configuration for it, only the control API (Client.SetLatency). This reads the
wanted offsets from CLIENT_LATENCIES (a JSON object of client name or id to
milliseconds), asks snapserver for its clients over the control port and sets
the ones that differ. A client is matched by its id (the MAC address snapclient
reports) or by the host name it reports, ignoring case.

It is idempotent and meant to run repeatedly: a client snapserver has not seen
yet is skipped and picked up on a later run, and snapserver remembers a client
it has seen, so a client that is offline is still set.
"""

import json
import os
import socket
import sys

HOST = os.environ.get("SNAPCAST_HOST", "127.0.0.1")
PORT = int(os.environ.get("SNAPCAST_PORT", "1705"))
WANTED = {name.lower(): ms for name, ms in json.loads(os.environ["CLIENT_LATENCIES"]).items()}


def log(msg: str) -> None:
    print(f"snapcast-client-latency: {msg}", flush=True)


class Control:
    """Line-delimited JSON-RPC over snapserver's TCP control port."""

    def __init__(self) -> None:
        self.sock = socket.create_connection((HOST, PORT), timeout=5.0)
        self.reader = self.sock.makefile("r", encoding="utf-8")
        self.next_id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        request = {"id": self.next_id, "jsonrpc": "2.0", "method": method}
        if params is not None:
            request["params"] = params
        self.sock.sendall((json.dumps(request) + "\r\n").encode())
        while True:
            line = self.reader.readline()
            if not line:
                raise ConnectionError("snapserver closed the control connection")
            reply = json.loads(line)
            # Snapserver also sends notifications (no id); skip them.
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply["result"]


def main() -> int:
    try:
        control = Control()
        status = control.call("Server.GetStatus")
    except OSError as exc:
        log(f"snapserver is not reachable yet: {exc}")
        return 0  # the timer tries again

    clients = [
        client
        for group in status["server"]["groups"]
        for client in group["clients"]
    ]
    matched: set[str] = set()
    for client in clients:
        names = {
            client["id"].lower(),
            client.get("host", {}).get("name", "").lower(),
            client.get("config", {}).get("name", "").lower(),
        }
        for name in names & WANTED.keys():
            matched.add(name)
            wanted = WANTED[name]
            current = client["config"]["latency"]
            if current == wanted:
                continue
            control.call("Client.SetLatency", {"id": client["id"], "latency": wanted})
            log(f"{name}: latency {current} -> {wanted} ms")
    for name in sorted(WANTED.keys() - matched):
        log(f"{name}: no such client yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
