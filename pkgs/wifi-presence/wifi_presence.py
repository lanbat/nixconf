#!/usr/bin/env python3
"""Who is home: the household's phones on the LAN, for the assistant router.

Free-standing on the network: nothing is asked of the router. A phone is
known by its Wi-Fi address on the home network (people.<key>.wifiMacs: the
fixed private address Android and iOS keep per network), and counts as home
for HOLD_S after it was last heard. It is heard two ways, on one raw socket
whose kernel filter passes only the listed addresses' frames:

- passively: phones broadcast now and then (ARP, DHCP, mDNS);
- actively: an ARP request to each phone's last address every SWEEP_S, and
  to the whole subnet every FULL_SWEEP_S to find them. Sleeping phones still
  answer ARP; many ignore ping.

Phones also come from the router: a phone held to a robot opens a page that
knows whose it is and learns its address (assistant-router tap.py). The
router tells this service those, {"phones": {mac: person}}, when it connects
and on each change; the profile's own addresses win, and the kernel filter is
rebuilt.

The router hears the full set of who is home every minute, as a "house-sensor"
on its body socket: {"home": [{"person": "alex"}]}. Being home is a mild hint
for who is speaking (pkgs/assistant-router people.py), never authority over
anything (docs/people.md). Only person keys leave this service; no address,
no traffic is kept.
"""
from __future__ import annotations

import asyncio
import ctypes
import fcntl
import ipaddress
import json
import os
import re
import socket
import struct
import sys
import time

PROTOCOL = 1
HOLD_S = 600.0         # a phone heard this recently is home
SWEEP_S = 60.0         # ARP each phone's last address
FULL_SWEEP_S = 300.0   # ARP the whole subnet, to find phones that moved
REPORT_S = 60.0        # the router forgets the set after 3 minutes
MAX_HOSTS = 4096       # a larger subnet is swept only in its first 4096 addresses
SWEEP_GAP_S = 0.01     # between ARP requests: about 100 a second
MAX_PHONES = 60        # the kernel filter's jumps are 8 bits

ETH_P_ALL = 0x0003
ETH_P_ARP = 0x0806
ETH_P_IP = 0x0800
SIOCGIFADDR = 0x8915
SO_ATTACH_FILTER = 26

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def log(msg: str) -> None:
    print(f"wifi-presence: {msg}", file=sys.stderr, flush=True)


def normalise_mac(text: str) -> str | None:
    """aa:bb:cc:dd:ee:ff from any common spelling, or None."""
    hexes = re.sub(r"[^0-9a-f]", "", str(text).lower())
    if len(hexes) != 12:
        return None
    mac = ":".join(hexes[i:i + 2] for i in range(0, 12, 2))
    return mac if _MAC.match(mac) else None


def parse_phones(text: str) -> dict[str, str]:
    """{mac: person} from JSON {"person": ["mac", ...]}; bad entries dropped."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for person, macs in data.items():
            for mac in macs if isinstance(macs, list) else []:
                m = normalise_mac(mac)
                if m:
                    out[m] = str(person)
    return out


def merge_phones(static: dict[str, str], learned) -> dict[str, str]:
    """The profile's phones, then those learned from taps, up to MAX_PHONES."""
    out = dict(static)
    if isinstance(learned, dict):
        for mac, person in sorted(learned.items()):
            m = normalise_mac(mac)
            if m and m not in out and isinstance(person, str) and 0 < len(person) <= 40 \
                    and len(out) < MAX_PHONES:
                out[m] = person
    return out


def mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def bpf_source_macs(macs: list[str], snap: int = 128) -> list[tuple[int, int, int, int]]:
    """A classic BPF program passing frames whose Ethernet source is one of
    `macs` (the first `snap` bytes of each), dropping everything else."""
    LD_W_ABS, LDH_ABS, JEQ_K, RET_K = 0x20, 0x28, 0x15, 0x06
    if len(macs) > MAX_PHONES:
        raise ValueError(f"at most {MAX_PHONES} addresses (BPF jumps are 8 bits)")
    prog: list[tuple[int, int, int, int]] = []
    n = len(macs)
    for i, mac in enumerate(macs):
        hi, lo = struct.unpack("!HI", mac_bytes(mac))
        # Four instructions per MAC. Jumps count from the next instruction:
        # a mismatch falls to the next MAC's check (or the drop), a match
        # jumps over the remaining checks and the drop to the accept.
        to_accept = 4 * (n - i - 1) + 1
        prog.append((LD_W_ABS, 0, 0, 8))   # source bytes 2..5 (frame 8..11)
        prog.append((JEQ_K, 0, 2, lo))
        prog.append((LDH_ABS, 0, 0, 6))    # source bytes 0..1 (frame 6..7)
        prog.append((JEQ_K, to_accept, 0, hi))
    prog.append((RET_K, 0, 0, 0))     # drop
    prog.append((RET_K, 0, 0, snap))  # accept
    return prog


def attach_filter(sock: socket.socket, prog: list[tuple[int, int, int, int]]) -> None:
    raw = b"".join(struct.pack("HBBI", *ins) for ins in prog)
    buf = ctypes.create_string_buffer(raw)
    fprog = struct.pack("HL", len(prog), ctypes.addressof(buf))
    sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, fprog)
    # The kernel copies the program; the buffer need not outlive this call.


def parse_frame(frame: bytes) -> tuple[str, str | None] | None:
    """(source MAC, its IPv4 address if the frame says) from an Ethernet frame."""
    if len(frame) < 14:
        return None
    src = ":".join(f"{b:02x}" for b in frame[6:12])
    ethertype = struct.unpack("!H", frame[12:14])[0]
    off = 14
    if ethertype == 0x8100 and len(frame) >= 18:  # one VLAN tag
        ethertype = struct.unpack("!H", frame[16:18])[0]
        off = 18
    ip = None
    if ethertype == ETH_P_ARP and len(frame) >= off + 28:
        sender = frame[off + 14:off + 18]
        ip = socket.inet_ntoa(sender)
    elif ethertype == ETH_P_IP and len(frame) >= off + 20:
        ip = socket.inet_ntoa(frame[off + 12:off + 16])
    if ip == "0.0.0.0":  # DHCP before it has an address
        ip = None
    return src, ip


def arp_request(src_mac: bytes, src_ip: str, target_ip: str) -> bytes:
    broadcast = b"\xff" * 6
    eth = broadcast + src_mac + struct.pack("!H", ETH_P_ARP)
    arp = struct.pack("!HHBBH", 1, ETH_P_IP, 6, 4, 1) + src_mac + socket.inet_aton(src_ip) \
        + b"\x00" * 6 + socket.inet_aton(target_ip)
    return eth + arp


class Presence:
    """Who is home, from when each phone was last heard."""

    def __init__(self, phones: dict[str, str], hold_s: float = HOLD_S) -> None:
        self.phones = phones
        self.hold_s = hold_s
        self.last: dict[str, float] = {}     # mac -> when heard
        self.address: dict[str, str] = {}    # mac -> IPv4, for the targeted ARP

    def heard(self, mac: str, ip: str | None, now: float) -> None:
        if mac not in self.phones:
            return
        self.last[mac] = now
        if ip:
            self.address[mac] = ip

    def home(self, now: float) -> list[str]:
        return sorted({self.phones[m] for m, t in self.last.items() if now - t <= self.hold_s})


def interface_address(name: str) -> tuple[bytes, str]:
    """(MAC, IPv4) of a local interface."""
    with open(f"/sys/class/net/{name}/address", encoding="ascii") as f:
        mac = mac_bytes(f.read().strip())
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", name.encode()[:15]))
    finally:
        s.close()
    return mac, socket.inet_ntoa(packed[20:24])


def sweep_targets(subnet: str, own_ip: str) -> list[str]:
    net = ipaddress.ip_network(subnet, strict=False)
    out = []
    for host in net.hosts():
        if str(host) != own_ip:
            out.append(str(host))
        if len(out) >= MAX_HOSTS:
            break
    return out


class Service:
    def __init__(self, interface: str, subnet: str, phones: dict[str, str], router_url: str) -> None:
        self.interface = interface
        self.subnet = subnet
        self.static = dict(phones)
        self.presence = Presence(dict(phones))
        self.router_url = router_url
        self.sock: socket.socket | None = None
        self.own_mac = b""
        self.own_ip = ""
        self.reported: list[str] | None = None
        self.ws = None

    def open(self) -> None:
        self.own_mac, self.own_ip = interface_address(self.interface)
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        attach_filter(sock, bpf_source_macs(sorted(self.presence.phones)))
        sock.bind((self.interface, 0))
        sock.setblocking(False)
        self.sock = sock

    def learned(self, phones) -> None:
        """The phones the router learned from taps: the whole set, each time."""
        merged = merge_phones(self.static, phones)
        if merged == self.presence.phones:
            return
        self.presence.phones = merged
        if self.sock is not None:
            attach_filter(self.sock, bpf_source_macs(sorted(merged)))
        log(f"listening for {len(merged)} phones ({len(merged) - len(self.static)} learned from taps)")

    async def listen(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and "phones" in msg:
                self.learned(msg["phones"])

    def on_readable(self) -> None:
        while True:
            try:
                frame = self.sock.recv(256)
            except BlockingIOError:
                return
            parsed = parse_frame(frame)
            if parsed:
                self.presence.heard(parsed[0], parsed[1], time.time())

    async def sweep_loop(self) -> None:
        last_full = 0.0
        while True:
            now = time.time()
            if now - last_full >= FULL_SWEEP_S:
                last_full = now
                targets = sweep_targets(self.subnet, self.own_ip)
            else:
                targets = sorted(set(self.presence.address.values()))
            for ip in targets:
                try:
                    self.sock.send(arp_request(self.own_mac, self.own_ip, ip))
                except OSError as err:
                    log(f"ARP to {ip}: {err}")
                    break
                await asyncio.sleep(SWEEP_GAP_S)
            await asyncio.sleep(SWEEP_S)

    async def report_loop(self) -> None:
        import websockets

        delay = 1
        while True:
            try:
                async with websockets.connect(self.router_url) as ws:
                    delay = 1
                    await ws.send(json.dumps({"hello": {"room": "house", "kind": "house-sensor", "proto": PROTOCOL}}))
                    log(f"telling {self.router_url} who is home")
                    listening = asyncio.create_task(self.listen(ws))
                    try:
                        while not listening.done():
                            home = self.presence.home(time.time())
                            if home != self.reported:
                                log(f"home: {', '.join(home) or 'nobody'}")
                                self.reported = home
                            await ws.send(json.dumps({"home": [{"person": p} for p in home]}))
                            await asyncio.wait([listening], timeout=REPORT_S)
                        listening.result()  # the router hung up: why
                    finally:
                        listening.cancel()
            except (OSError, websockets.exceptions.WebSocketException) as err:
                log(f"router unreachable ({err}); retrying")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def run(self) -> None:
        self.open()
        log(f"listening on {self.interface} ({self.own_ip}) for {len(self.presence.phones)} phones")
        asyncio.get_running_loop().add_reader(self.sock.fileno(), self.on_readable)
        await asyncio.gather(self.sweep_loop(), self.report_loop())


def main() -> None:
    phones = parse_phones(os.environ.get("PHONES", "{}"))
    if not phones:
        log("no phones in the profile (people.<key>.wifiMacs); waiting for taps")
    service = Service(
        interface=os.environ["INTERFACE"],
        subnet=os.environ["SUBNET"],
        phones=phones,
        router_url=os.environ["ROUTER_BODY_URL"],
    )
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
