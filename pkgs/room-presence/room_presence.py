#!/usr/bin/env python3
"""Whose phone is in the room: a Bluetooth scan on a voice satellite.

Listens to Bluetooth LE advertisements and recognises the phones of the
profile's people (lanbat.deployment.people), then tells the assistant router
who is nearby (pkgs/assistant-router people.py), as a "room-sensor" on its
body socket: {"seen": [{"person": "alex", "rssi": -67}]}. Two ways to know a
phone, since phones change their Bluetooth address every few minutes:

- Android: the Home Assistant app's BLE transmitter sends an iBeacon with a
  fixed UUID, one per person (people.<key>.bleBeacon).
- Any phone (iPhone, Android without the app): a resolvable private address
  is checked against the phone's Identity Resolving Key (IRK). The router
  hands the keys of the phones paired once to every room sensor
  ({"irks": {key: [32 hex]}}, kept in memory only); the room-presence-irks
  secret, lines of "<person key> <32 hex digits>", adds more. A host bonded
  with the phone (the one it was paired with) resolves its addresses itself,
  and BlueZ reports the phone's identity address: the bond's IRK names it.
  `room-presence --paired` prints the IRKs of phones paired with this host.

On a host that serves Wi-Fi (lanbat.wifi.accessPoint) it also reads the access
point's clients (`iw dev <AP_INTERFACE> station dump`) every REPORT_SECONDS and
reports the phones among them by their Wi-Fi address, {"via": "wifi"}: the
profile's (WIFI_PHONES, people.<key>.wifiMacs) and those the router learned
from taps ({"phones": {mac: key}}, as it tells wifi-presence).

A phone is a hint that someone is near, not that they are speaking, and never
authority over anything (docs/people.md). Nothing but person keys and signal
strengths leaves the Pi.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import statistics
import sys
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PROTOCOL = 1
APPLE = 0x004C
REPORT_SECONDS = 5
WINDOW_SECONDS = 30
BONDS = "/var/lib/bluetooth/*/*/info"
BONDS_SECONDS = 60  # how often the bonds are read again (a phone may pair)


def log(msg: str) -> None:
    print(f"room-presence: {msg}", file=sys.stderr, flush=True)


# ── recognising a phone ────────────────────────────────────────────────────
def _ah(irk: bytes, prand: bytes) -> bytes:
    """Bluetooth's random address hash: the low 24 bits of AES-128(irk, prand)."""
    enc = Cipher(algorithms.AES(irk), modes.ECB()).encryptor()
    return (enc.update(bytes(13) + prand) + enc.finalize())[-3:]


def resolves(address: str, irk: bytes) -> bool:
    """Whether a resolvable private address belongs to the device with `irk`.
    Keys are tried as given and byte-reversed: tools disagree on the order."""
    try:
        raw = bytes.fromhex(address.replace(":", ""))
    except ValueError:
        return False
    if len(raw) != 6 or raw[0] >> 6 != 0b01:
        return False
    prand, hashed = raw[:3], raw[3:]
    return any(_ah(key, prand) == hashed for key in (irk, irk[::-1]))


def parse_ibeacon(manufacturer_data: dict) -> str | None:
    """An iBeacon's UUID (32 lower-case hex digits), or None."""
    data = manufacturer_data.get(APPLE)
    if not data or len(data) < 23 or data[0] != 0x02 or data[1] != 0x15:
        return None
    return bytes(data[2:18]).hex()


def parse_beacons(text: str) -> dict[str, str]:
    """"alex=<uuid>,sam=<uuid>" -> {uuid: key}; dashes and case don't matter."""
    out = {}
    for item in (text or "").split(","):
        key, _, uuid = item.strip().partition("=")
        uuid = uuid.replace("-", "").strip().lower()
        if key.strip() and re.fullmatch(r"[0-9a-f]{32}", uuid):
            out[uuid] = key.strip()
    return out


def parse_irks(text: str) -> dict[str, list[bytes]]:
    """Lines of "<key> <32 hex>" (a person may have several devices)."""
    out: dict[str, list[bytes]] = {}
    for line in (text or "").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and re.fullmatch(r"[0-9A-Fa-f]{32}", parts[1]):
            out.setdefault(parts[0], []).append(bytes.fromhex(parts[1]))
    return out


class Tracker:
    """Signal strengths per person over the last window, reported as a median."""

    def __init__(self, window_s: float = WINDOW_SECONDS) -> None:
        self.window_s = window_s
        self._seen: dict[str, list[tuple[float, int]]] = {}

    def observe(self, person: str, rssi: int, now: float) -> None:
        self._seen.setdefault(person, []).append((now, int(rssi)))

    def report(self, now: float) -> list[dict]:
        out = []
        for person in sorted(self._seen):
            recent = [(t, r) for t, r in self._seen[person] if now - t <= self.window_s]
            self._seen[person] = recent
            if recent:
                out.append({"person": person, "rssi": int(statistics.median(r for _, r in recent))})
        self._seen = {p: v for p, v in self._seen.items() if v}
        return out


def read_bonds(pattern: str = BONDS) -> list[tuple[str, str, str]]:
    """(address, name, IRK as 32 lower-case hex) of each device bonded with
    this host that handed over an IRK (BlueZ's info files)."""
    out = []
    for info in sorted(glob.glob(pattern)):
        name, irk = "", ""
        try:
            with open(info, encoding="utf-8", errors="replace") as f:
                section = ""
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        section = line
                    elif line.startswith("Name=") and not name:
                        name = line[5:]
                    elif section == "[IdentityResolvingKey]" and line.startswith("Key="):
                        irk = line[4:].lower()
        except OSError:
            continue
        if re.fullmatch(r"[0-9a-f]{32}", irk):
            out.append((os.path.basename(os.path.dirname(info)).upper(), name, irk))
    return out


def normalise_mac(text) -> str | None:
    """aa:bb:cc:dd:ee:ff from any common spelling, or None."""
    hexes = re.sub(r"[^0-9a-f]", "", str(text).lower())
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2)) if len(hexes) == 12 else None


def parse_stations(text: str) -> dict[str, int]:
    """{mac: signal} from `iw dev <ap> station dump`: the average signal, or
    the last one; a client with neither is left out."""
    out: dict[str, int] = {}
    mac = None
    for line in (text or "").splitlines():
        m = re.match(r"Station ([0-9A-Fa-f:]{17})", line)
        if m:
            mac = m.group(1).lower()
            continue
        s = re.match(r"\s*signal( avg)?:\s*(-?\d+)", line)
        if mac and s and (s.group(1) or mac not in out):
            out[mac] = int(s.group(2))
    return out


class Recogniser:
    def __init__(self, beacons: dict[str, str], irks: dict[str, list[bytes]], bonds: str = BONDS,
                 wifi: dict[str, str] | None = None) -> None:
        self.file_wifi = {m: p for m, p in ((normalise_mac(k), v) for k, v in (wifi or {}).items()) if m}
        self.wifi = dict(self.file_wifi)
        self.beacons, self.file_irks = beacons, irks
        self.irks = dict(irks)
        self.bonds = bonds
        self._addresses: dict[str, str | None] = {}  # an address, once resolved
        self._bonded: dict[str, str] = {}  # a bonded device's identity address -> person
        self._bonds_read = float("-inf")

    def from_router(self, keys) -> int:
        """The router's keys ({key: [32 hex]}), with the secret's; how many."""
        merged = {k: list(v) for k, v in self.file_irks.items()}
        if isinstance(keys, dict):
            for key, irks in keys.items():
                for irk in irks if isinstance(irks, list) else []:
                    if isinstance(key, str) and isinstance(irk, str) and re.fullmatch(r"[0-9A-Fa-f]{32}", irk):
                        merged.setdefault(key, []).append(bytes.fromhex(irk))
        self.irks = merged
        self._addresses.clear()
        self._bonds_read = float("-inf")
        return sum(map(len, merged.values()))

    def wifi_from_router(self, phones) -> int:
        """The router's learned phones ({mac: key}), the profile's winning; how many."""
        merged: dict[str, str] = {}
        if isinstance(phones, dict):
            for mac, key in phones.items():
                m = normalise_mac(mac)
                if m and isinstance(key, str) and key:
                    merged[m] = key
        merged.update(self.file_wifi)
        self.wifi = merged
        return len(merged)

    def wifi_person(self, mac: str) -> str | None:
        return self.wifi.get(normalise_mac(mac) or "")

    def _bonded_person(self, address: str) -> str | None:
        now = time.monotonic()
        if now - self._bonds_read > BONDS_SECONDS:
            self._bonds_read = now
            owner = {irk: key for key, irks in self.irks.items() for k in irks for irk in (k, k[::-1])}
            self._bonded = {a: owner[bytes.fromhex(irk)] for a, _, irk in read_bonds(self.bonds)
                            if bytes.fromhex(irk) in owner}
        return self._bonded.get(address.upper())

    def person(self, address: str, manufacturer_data: dict) -> str | None:
        uuid = parse_ibeacon(manufacturer_data)
        if uuid is not None:
            return self.beacons.get(uuid)
        if address not in self._addresses:
            if len(self._addresses) > 4096:
                self._addresses.clear()
            self._addresses[address] = next(
                (key for key, keys in self.irks.items() for irk in keys if resolves(address, irk)), None)
        return self._addresses[address] or self._bonded_person(address)


def observe_stations(stations: dict[str, int], recogniser: Recogniser, tracker: Tracker, now: float) -> None:
    for mac, signal in stations.items():
        person = recogniser.wifi_person(mac)
        if person is not None:
            tracker.observe(person, signal, now)


def sightings(ble: Tracker, wifi: Tracker | None, now: float) -> list[dict]:
    """The report: Bluetooth sightings, then Wi-Fi ones marked as such (the
    router compares signals only between sensors of one kind)."""
    return ble.report(now) + ([dict(s, via="wifi") for s in wifi.report(now)] if wifi else [])


# ── I/O ────────────────────────────────────────────────────────────────────
async def scan(recogniser: Recogniser, tracker: Tracker) -> None:
    from bleak import BleakScanner

    def seen(device, adv) -> None:
        person = recogniser.person(device.address, adv.manufacturer_data or {})
        if person is not None and adv.rssi is not None:
            tracker.observe(person, adv.rssi, time.time())

    delay = 5
    while True:
        try:
            async with BleakScanner(detection_callback=seen):
                log("scanning")
                delay = 5
                while True:
                    await asyncio.sleep(3600)
        except Exception as err:  # pylint: disable=broad-except
            # The Pi 3's controller vanishes now and then (hosts/pi3/hardware.nix).
            log(f"scan stopped ({err}); retrying in {delay} s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 120)


async def stations(interface: str, recogniser: Recogniser, tracker: Tracker) -> None:
    """The access point's clients every REPORT_SECONDS, the phones among them."""
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "iw", "dev", interface, "station", "dump",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
            observe_stations(parse_stations(out.decode(errors="replace")), recogniser, tracker, time.time())
        except (OSError, asyncio.TimeoutError) as err:
            log(f"access point clients unreadable ({err})")
        await asyncio.sleep(REPORT_SECONDS)


async def listen(ws, recogniser: Recogniser) -> None:
    """The router's messages: the phones' keys and Wi-Fi addresses, now and
    on each change."""
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict) and "irks" in msg:
            log(f"the router knows {recogniser.from_router(msg['irks'])} phone key(s)")
        if isinstance(msg, dict) and "phones" in msg:
            log(f"looking for {recogniser.wifi_from_router(msg['phones'])} phone(s) on the access point")


async def report(tracker: Tracker, url: str, room: str, recogniser: Recogniser | None = None,
                 wifi: Tracker | None = None) -> None:
    import websockets

    delay = 1
    present: set[str] = set()
    while True:
        try:
            async with websockets.connect(url) as ws:
                hello = {"room": room, "kind": "room-sensor", "proto": PROTOCOL}
                if wifi is not None:
                    hello["wifi"] = True  # send the learned phones' Wi-Fi addresses
                await ws.send(json.dumps({"hello": hello}))
                log(f"room sensor of {room} at {url}")
                delay = 1
                listener = asyncio.create_task(listen(ws, recogniser)) if recogniser is not None else None
                while True:
                    if listener is not None and listener.done():
                        listener.result()  # the connection closed: raise and reconnect
                    seen = sightings(tracker, wifi, time.time())
                    now_present = {s["person"] for s in seen}
                    for person in sorted(now_present - present):
                        log(f"{person}'s phone is here")
                    for person in sorted(present - now_present):
                        log(f"{person}'s phone has gone")
                    present = now_present
                    if seen:
                        await ws.send(json.dumps({"seen": seen}))
                    await asyncio.sleep(REPORT_SECONDS)
        except (OSError, websockets.exceptions.WebSocketException) as err:
            log(f"router unreachable ({err}); retrying")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def paired() -> None:
    """The IRKs of the devices paired with this Pi (BlueZ keeps them)."""
    for address, name, irk in read_bonds():
        print(f"{irk}  {name or '?'}  ({address})")


def main() -> None:
    p = argparse.ArgumentParser(prog="room-presence")
    p.add_argument("--paired", action="store_true", help="print the IRKs of devices paired with this Pi")
    a = p.parse_args()
    if a.paired:
        paired()
        return
    irks = {}
    irk_file = os.environ.get("IRK_FILE", "")
    if irk_file:
        with open(irk_file, encoding="utf-8") as f:
            irks = parse_irks(f.read())
    known = set(filter(None, os.environ.get("PEOPLE", "").split(",")))
    beacons = {u: k for u, k in parse_beacons(os.environ.get("BEACONS", "")).items() if k in known}
    irks = {k: v for k, v in irks.items() if k in known}
    log(f"knows {len(beacons)} beacon(s) and {sum(map(len, irks.values()))} key(s)")
    tracker = Tracker()

    ap = os.environ.get("AP_INTERFACE", "")
    wifi_phones = {}
    try:
        for person, macs in json.loads(os.environ.get("WIFI_PHONES", "") or "{}").items():
            for mac in macs if isinstance(macs, list) else []:
                wifi_phones[mac] = person
    except (ValueError, AttributeError):
        pass
    recogniser = Recogniser(beacons, irks, wifi=wifi_phones)
    wifi = Tracker() if ap else None
    if ap:
        log(f"reading the access point's clients on {ap} ({len(recogniser.wifi)} phone(s) from the profile)")

    async def run() -> None:
        tasks = [scan(recogniser, tracker),
                 report(tracker, os.environ["ROUTER_BODY_URL"], os.environ["ROOM"], recogniser, wifi)]
        if wifi is not None:
            tasks.append(stations(ap, recogniser, wifi))
        await asyncio.gather(*tasks)

    asyncio.run(run())


if __name__ == "__main__":
    main()
