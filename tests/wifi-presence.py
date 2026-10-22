"""The wifi-presence service's parts (pkgs/wifi-presence), without a network.

The kernel filter is checked by the kernel itself: attached to a Unix
datagram socket pair (no privileges needed), fake Ethernet frames go through
it and only the listed phones' come out.

Run with: nix build .#checks.x86_64-linux.wifi-presence
"""
import socket
import struct
import sys
import unittest

sys.path.insert(0, sys.argv.pop(1))

from wifi_presence import (  # noqa: E402
    MAX_DEVICES,
    MAX_PHONES,
    Presence,
    Service,
    arp_request,
    attach_filter,
    bpf_source_macs,
    mac_bytes,
    merge_phones,
    normalise_mac,
    parse_frame,
    parse_phones,
    sweep_targets,
)

ALEX = "3a:1f:00:aa:bb:01"
SAM = "6e:02:11:22:33:44"
OTHER = "3a:1f:00:aa:bb:02"  # differs from Alex's in the last byte only
OTHER_HI = "3b:1f:00:aa:bb:01"  # differs in the first byte only


def frame(src: str, ethertype: int = 0x0806, payload: bytes = b"") -> bytes:
    return b"\xff" * 6 + mac_bytes(src) + struct.pack("!H", ethertype) + payload


def arp_reply(src: str, ip: str) -> bytes:
    body = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 2) + mac_bytes(src) + socket.inet_aton(ip) \
        + b"\x00" * 6 + socket.inet_aton("192.0.2.10")
    return frame(src, 0x0806, body)


class Addresses(unittest.TestCase):
    def test_spellings(self):
        for text in ("3A:1F:00:AA:BB:01", "3a-1f-00-aa-bb-01", "3a1f.00aa.bb01", "3a1f00aabb01"):
            self.assertEqual(normalise_mac(text), ALEX)
        self.assertIsNone(normalise_mac("3a:1f:00:aa:bb"))
        self.assertIsNone(normalise_mac("nonsense"))

    def test_phones_from_deploy_data(self):
        phones = parse_phones('{"alex": ["3A-1F-00-AA-BB-01", "bad"], "sam": ["6e:02:11:22:33:44"], "x": "no"}')
        self.assertEqual(phones, {ALEX: "alex", SAM: "sam"})
        self.assertEqual(parse_phones("not json"), {})


class KernelFilter(unittest.TestCase):
    """The BPF program, run by this machine's kernel."""

    def passes(self, macs, frames, arp_any=False):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            attach_filter(b, bpf_source_macs(macs, arp_any=arp_any))
            for f in frames:
                a.send(f)
            b.setblocking(False)
            got = []
            while True:
                try:
                    got.append(b.recv(2048))
                except BlockingIOError:
                    return got
        finally:
            a.close()
            b.close()

    def test_only_listed_phones_pass(self):
        frames = [frame(ALEX), frame(OTHER), frame(SAM), frame(OTHER_HI), frame("00:00:00:00:00:00")]
        got = self.passes([ALEX, SAM], frames)
        self.assertEqual([parse_frame(f)[0] for f in got], [ALEX, SAM])

    def test_each_position_in_the_list(self):
        macs = [f"02:00:00:00:00:{i:02x}" for i in range(10)]
        for mac in macs:
            self.assertEqual(len(self.passes(macs, [frame(mac)])), 1, mac)
        self.assertEqual(self.passes(macs, [frame("02:00:00:00:00:ff")]), [])

    def test_arp_from_anyone_passes_when_asked_and_nothing_else_does(self):
        arp = arp_reply(OTHER, "192.0.2.77")
        ipv4 = frame(OTHER, 0x0800, b"x" * 40)
        for macs in ([], [ALEX], [f"02:00:00:00:00:{i:02x}" for i in range(MAX_PHONES)]):
            got = self.passes(macs, [arp, ipv4], arp_any=True)
            self.assertEqual(got, [arp], len(macs))  # ARP, not the traffic of a device nobody listed
            self.assertEqual(self.passes(macs, [arp, ipv4]), [])  # off by default
        # The listed phones' own frames still pass, ARP or not.
        got = self.passes([ALEX], [frame(ALEX, 0x0800, b"x" * 40), arp], arp_any=True)
        self.assertEqual(len(got), 2)

    def test_nobody_listed_passes_nothing(self):
        self.assertEqual(self.passes([], [frame(ALEX)]), [])

    def test_frames_are_cut_short(self):
        got = self.passes([ALEX], [frame(ALEX, payload=b"x" * 1000)])
        self.assertEqual(len(got[0]), 128)  # headers only: no traffic kept

    def test_too_many(self):
        with self.assertRaises(ValueError):
            bpf_source_macs([f"02:00:00:00:{i // 256:02x}:{i % 256:02x}" for i in range(MAX_PHONES + 1)])
        macs = [f"02:00:00:00:00:{i:02x}" for i in range(MAX_PHONES)]
        self.assertEqual(len(self.passes(macs, [frame(macs[0])])), 1)  # the farthest jump fits


class Frames(unittest.TestCase):
    def test_arp_reply_gives_the_address(self):
        self.assertEqual(parse_frame(arp_reply(ALEX, "192.0.2.50")), (ALEX, "192.0.2.50"))

    def test_ipv4_and_dhcp(self):
        ip = bytes(12) + socket.inet_aton("192.0.2.51") + bytes(4)
        self.assertEqual(parse_frame(frame(ALEX, 0x0800, ip)), (ALEX, "192.0.2.51"))
        dhcp = bytes(12) + socket.inet_aton("0.0.0.0") + bytes(4)
        self.assertEqual(parse_frame(frame(ALEX, 0x0800, dhcp)), (ALEX, None))

    def test_vlan_and_junk(self):
        tagged = b"\xff" * 6 + mac_bytes(ALEX) + b"\x81\x00\x00\x05\x08\x06" + arp_reply(ALEX, "192.0.2.52")[14:]
        self.assertEqual(parse_frame(tagged), (ALEX, "192.0.2.52"))
        self.assertIsNone(parse_frame(b"short"))
        self.assertEqual(parse_frame(frame(ALEX, 0x86dd)), (ALEX, None))  # IPv6: heard, no address

    def test_arp_request_layout(self):
        req = arp_request(mac_bytes(SAM), "192.0.2.10", "192.0.2.50")
        self.assertEqual(len(req), 42)
        self.assertEqual(req[:6], b"\xff" * 6)
        self.assertEqual(struct.unpack("!H", req[20:22])[0], 1)  # a request
        self.assertEqual(socket.inet_ntoa(req[38:42]), "192.0.2.50")


class Home(unittest.TestCase):
    def test_heard_then_held_then_gone(self):
        p = Presence({ALEX: "alex", SAM: "sam"}, hold_s=600)
        p.heard(ALEX, "192.0.2.50", now=0.0)
        p.heard(OTHER, "192.0.2.99", now=0.0)  # not a listed phone
        self.assertEqual(p.home(now=0.0), ["alex"])
        self.assertEqual(p.address, {ALEX: "192.0.2.50"})
        p.heard(SAM, None, now=500.0)
        self.assertEqual(p.home(now=550.0), ["alex", "sam"])
        self.assertEqual(p.home(now=601.0), ["sam"])  # Alex's phone went quiet
        self.assertEqual(p.home(now=1101.0), [])

    def test_other_devices_are_kept_only_when_asked(self):
        off = Presence({ALEX: "alex"})
        off.heard(OTHER, None, now=0.0)
        self.assertEqual(off.devices(now=1.0), [])
        p = Presence({ALEX: "alex"}, hold_s=600, others=True, own="02:aa:aa:aa:aa:aa")
        p.heard(OTHER, "192.0.2.99", now=10.0)
        p.heard(SAM, None, now=20.0)
        p.heard(ALEX, None, now=30.0)                  # a household phone is not "other"
        p.heard("02:aa:aa:aa:aa:aa", None, now=30.0)   # this host
        p.heard("01:00:5e:00:00:fb", None, now=30.0)   # a multicast address is no device
        self.assertEqual(p.devices(now=40.0), [SAM, OTHER])  # newest first
        self.assertEqual(p.devices(now=700.0), [])           # gone quiet
        self.assertEqual(p.home(now=40.0), ["alex"])

    def test_devices_are_capped(self):
        p = Presence({}, others=True)
        for i in range(MAX_DEVICES + 50):
            p.heard(f"02:00:00:00:{i // 256:02x}:{i % 256:02x}", None, now=float(i))
        self.assertEqual(len(p.devices(now=float(MAX_DEVICES + 50))), MAX_DEVICES)

    def test_two_phones_one_person(self):
        p = Presence({ALEX: "alex", OTHER: "alex"})
        p.heard(OTHER, None, now=0.0)
        self.assertEqual(p.home(now=1.0), ["alex"])

    def test_sweep_targets(self):
        targets = sweep_targets("192.0.2.0/24", "192.0.2.10")
        self.assertEqual(len(targets), 253)
        self.assertNotIn("192.0.2.10", targets)
        self.assertEqual(len(sweep_targets("10.0.0.0/8", "10.0.0.1")), 4096)


class Learned(unittest.TestCase):
    def test_the_profile_wins_and_junk_is_dropped(self):
        merged = merge_phones({ALEX: "alex"}, {ALEX.upper(): "sam", "6E-02-11-22-33-44": "sam",
                                               "nope": "x", OTHER: 5, OTHER_HI: "y" * 41})
        self.assertEqual(merged, {ALEX: "alex", SAM: "sam"})
        self.assertEqual(merge_phones({ALEX: "alex"}, "junk"), {ALEX: "alex"})
        many = {f"02:00:00:00:{i // 256:02x}:{i % 256:02x}": "x" for i in range(100)}
        self.assertEqual(len(merge_phones({ALEX: "alex"}, many)), MAX_PHONES)

    def test_learned_phones_pass_the_filter(self):
        service = Service("lo", "192.0.2.0/24", {ALEX: "alex"}, "ws://127.0.0.1:1/v1/body")
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            service.sock = b
            attach_filter(b, bpf_source_macs([ALEX]))
            service.learned({SAM: "sam"})
            self.assertEqual(service.presence.phones, {ALEX: "alex", SAM: "sam"})
            a.send(frame(SAM))
            a.send(frame(OTHER))
            b.setblocking(False)
            self.assertEqual(parse_frame(b.recv(2048))[0], SAM)
            with self.assertRaises(BlockingIOError):
                b.recv(2048)
            service.learned({})  # forgotten: gone again
            self.assertEqual(service.presence.phones, {ALEX: "alex"})
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
