"""Phones in the room (pkgs/room-presence), without a radio or a router.

Run with: nix build .#checks.x86_64-linux.room-presence
"""
import sys
import unittest

sys.path.insert(0, sys.argv.pop(1))

from room_presence import (  # noqa: E402
    Tracker,
    parse_beacons,
    parse_ibeacon,
    parse_irks,
    resolves,
)

# Bluetooth Core Specification, Vol 3 Part H, Appendix D.7: ah() sample data.
SPEC_IRK = bytes.fromhex("ec0234a357c8ad05341010a60a397d9b")
SPEC_ADDRESS = "70:81:94:0D:FB:AA"  # prand 0x708194, hash 0x0dfbaa


def ibeacon(uuid_hex, major=1, minor=2, tx=-59):
    return bytes([0x02, 0x15]) + bytes.fromhex(uuid_hex) + major.to_bytes(2, "big") \
        + minor.to_bytes(2, "big") + (tx & 0xFF).to_bytes(1, "big")


UUID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


class Rpa(unittest.TestCase):
    def test_the_specification_sample_resolves(self):
        self.assertTrue(resolves(SPEC_ADDRESS, SPEC_IRK))

    def test_a_reversed_key_resolves_too(self):
        # Keys copied from some tools come least significant byte first.
        self.assertTrue(resolves(SPEC_ADDRESS, SPEC_IRK[::-1]))

    def test_another_address_does_not(self):
        self.assertFalse(resolves("70:81:94:0D:FB:AB", SPEC_IRK))

    def test_only_resolvable_private_addresses(self):
        # Top two bits 01 mark a resolvable private address; 11 is static random.
        self.assertFalse(resolves("F0:81:94:0D:FB:AA", SPEC_IRK))
        self.assertFalse(resolves("not an address", SPEC_IRK))


class Beacon(unittest.TestCase):
    def test_an_ibeacon_gives_its_uuid(self):
        self.assertEqual(parse_ibeacon({0x004C: ibeacon(UUID)}), UUID)

    def test_other_apple_data_is_not_a_beacon(self):
        self.assertIsNone(parse_ibeacon({0x004C: bytes([0x10, 0x05, 0x01, 0x18])}))
        self.assertIsNone(parse_ibeacon({0x0075: ibeacon(UUID)}))
        self.assertIsNone(parse_ibeacon({}))


class Config(unittest.TestCase):
    def test_beacons(self):
        self.assertEqual(parse_beacons("alex=A1B2C3D4-E5F6-0718-293A-4B5C6D7E8F90, sam=00000000000000000000000000000001"),
                         {UUID: "alex", "00000000000000000000000000000001": "sam"})
        self.assertEqual(parse_beacons(""), {})
        self.assertEqual(parse_beacons("broken,=x,alex=nothex"), {})

    def test_irks(self):
        text = "# Alex's iPhone\nalex ec0234a357c8ad05341010a60a397d9b\n\nsam zz\nbad\n"
        self.assertEqual(parse_irks(text), {"alex": [SPEC_IRK]})


class Tracking(unittest.TestCase):
    def test_a_person_seen_close_is_reported_with_a_median_rssi(self):
        t = Tracker(window_s=60)
        for rssi in (-70, -90, -72):
            t.observe("alex", rssi, now=10.0)
        self.assertEqual(t.report(now=15.0), [{"person": "alex", "rssi": -72}])

    def test_people_who_left_are_dropped(self):
        t = Tracker(window_s=60)
        t.observe("alex", -60, now=0.0)
        t.observe("sam", -60, now=50.0)
        self.assertEqual(t.report(now=70.0), [{"person": "sam", "rssi": -60}])
        self.assertEqual(t.report(now=200.0), [])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
