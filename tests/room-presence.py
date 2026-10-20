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


class FromTheRouter(unittest.TestCase):
    def test_the_routers_keys_recognise_a_phone_with_the_secrets(self):
        from room_presence import Recogniser
        r = Recogniser({}, {"sam": [bytes(16)]})
        self.assertIsNone(r.person(SPEC_ADDRESS, {}))
        self.assertEqual(r.from_router({"alex": [SPEC_IRK.hex()], "bad": ["xyz"], 3: ["00" * 16]}), 2)
        self.assertEqual(r.person(SPEC_ADDRESS, {}), "alex")
        self.assertEqual(r.from_router({}), 1)  # alex forgotten; the secret's key stays
        self.assertIsNone(r.person(SPEC_ADDRESS, {}))


STATIONS = """Station 7e:f9:41:00:00:01 (on wlp0s20f0u1)
\tinactive time:\t402 ms
\tsignal:  \t-34 [-34, -43] dBm
\tsignal avg:\t-35 [-37, -40] dBm
\ttx bitrate:\t130.0 MBit/s MCS 15
Station 44:1B:F6:00:00:02 (on wlp0s20f0u1)
\tsignal:  \t-66 [-68, -66] dBm
Station 02:00:00:00:00:03 (on wlp0s20f0u1)
\tconnected time:\t89 seconds
"""


class AccessPoint(unittest.TestCase):
    """A host serving Wi-Fi measures its clients: the phones among them."""

    def test_station_dump(self):
        from room_presence import parse_stations
        self.assertEqual(parse_stations(STATIONS), {"7e:f9:41:00:00:01": -35, "44:1b:f6:00:00:02": -66})
        self.assertEqual(parse_stations(""), {})

    def test_the_profiles_and_the_routers_phones(self):
        from room_presence import Recogniser
        r = Recogniser({}, {}, wifi={"7E-F9-41-00-00-01": "alex"})
        self.assertEqual(r.wifi_person("7e:f9:41:00:00:01"), "alex")
        self.assertIsNone(r.wifi_person("44:1b:f6:00:00:02"))
        self.assertEqual(r.wifi_from_router({"44:1b:f6:00:00:02": "sam", "junk": "x", "7e:f9:41:00:00:01": "eve"}), 2)
        self.assertEqual(r.wifi_person("44:1B:F6:00:00:02"), "sam")
        self.assertEqual(r.wifi_person("7e:f9:41:00:00:01"), "alex")  # the profile's wins
        self.assertEqual(r.wifi_from_router({}), 1)

    def test_reported_as_wifi(self):
        from room_presence import Recogniser, Tracker, observe_stations, sightings
        r = Recogniser({}, {}, wifi={"7e:f9:41:00:00:01": "alex"})
        ble, wifi = Tracker(), Tracker()
        ble.observe("sam", -70, 100.0)
        observe_stations(parse_stations_for_test(), r, wifi, 100.0)
        self.assertEqual(sightings(ble, wifi, 101.0),
                         [{"person": "sam", "rssi": -70}, {"person": "alex", "rssi": -35, "via": "wifi"}])


def parse_stations_for_test():
    from room_presence import parse_stations
    return parse_stations(STATIONS)


class Bonded(unittest.TestCase):
    """A host bonded with a phone (the one it paired with) resolves the
    phone's addresses itself, and BlueZ reports the phone's identity address."""

    def bonds(self, tmp):
        import os
        for adapter, phone, key in (("AA:AA:AA:AA:AA:01", "DC:E5:5B:EE:2C:BB", SPEC_IRK.hex().upper()),
                                    ("AA:AA:AA:AA:AA:01", "11:22:33:44:55:66", None)):
            os.makedirs(f"{tmp}/{adapter}/{phone}")
            with open(f"{tmp}/{adapter}/{phone}/info", "w") as f:
                f.write("[General]\nName=F2j45i\n")
                if key:
                    f.write(f"\n[IdentityResolvingKey]\nKey={key}\n")
        return f"{tmp}/*/*/info"

    def test_a_bonded_phones_identity_address_is_its_owners(self):
        import tempfile
        from room_presence import Recogniser
        with tempfile.TemporaryDirectory() as tmp:
            r = Recogniser({}, {}, bonds=self.bonds(tmp))
            self.assertIsNone(r.person("DC:E5:5B:EE:2C:BB", {}))  # nobody's key yet
            r.from_router({"alex": [SPEC_IRK.hex()]})
            self.assertEqual(r.person("DC:E5:5B:EE:2C:BB", {}), "alex")
            self.assertEqual(r.person("dc:e5:5b:ee:2c:bb", {}), "alex")
            self.assertIsNone(r.person("11:22:33:44:55:66", {}))  # bonded, no key
            self.assertEqual(r.person(SPEC_ADDRESS, {}), "alex")  # the other rooms' way still works

    def test_no_bonds_directory_is_fine(self):
        from room_presence import Recogniser
        r = Recogniser({}, {"alex": [SPEC_IRK]}, bonds="/nonexistent/*/*/info")
        self.assertIsNone(r.person("DC:E5:5B:EE:2C:BB", {}))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
