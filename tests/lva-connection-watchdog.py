"""The connection watchdog (pkgs/lva-connection-watchdog): when a satellite that
logged a lost connection counts as stuck, without systemd.

Run with: nix build .#checks.x86_64-linux.lva-connection-watchdog
"""
import sys
import unittest

sys.path.insert(0, sys.argv.pop(1))

import watchdog  # noqa: E402


def line(ts, message):
    return f"{ts}.123456 binturong linux-voice-assistant[1]: 2026-11-24 INFO:linux_voice_assistant.satellite:{message}"


DOWN = line(1000, "Disconnected from Home Assistant; waiting for reconnection")
UP = line(1020, "✅ Connected to Home Assistant - Configuration handshake completed")
NOISE = line(1010, "Voice event: type=VOICE_ASSISTANT_RUN_START, data={}")


class Watchdog(unittest.TestCase):
    def test_no_events_is_not_stuck(self):
        self.assertIsNone(watchdog.disconnected_since([]))
        self.assertIsNone(watchdog.disconnected_since([NOISE]))
        self.assertFalse(watchdog.stuck([NOISE], 10_000, 300))

    def test_connected_is_not_stuck(self):
        self.assertIsNone(watchdog.disconnected_since([UP]))
        self.assertIsNone(watchdog.disconnected_since([DOWN, UP]))
        self.assertFalse(watchdog.stuck([DOWN, UP], 10_000, 300))

    def test_reconnecting_within_the_grace_is_not_stuck(self):
        self.assertEqual(watchdog.disconnected_since([UP, NOISE, DOWN]), 1000.123456)
        self.assertFalse(watchdog.stuck([UP, DOWN], 1000 + 299, 300))

    def test_still_disconnected_after_the_grace_is_stuck(self):
        self.assertTrue(watchdog.stuck([UP, DOWN], 1000 + 301, 300))
        self.assertTrue(watchdog.stuck([DOWN, UP, NOISE, DOWN], 1000 + 301, 300))

    def test_a_later_disconnect_restarts_the_clock(self):
        down2 = line(5000, "Disconnected from Home Assistant; waiting for reconnection")
        self.assertFalse(watchdog.stuck([DOWN, UP, down2], 5100, 300))
        self.assertTrue(watchdog.stuck([DOWN, UP, down2], 5301, 300))

    def test_a_garbled_timestamp_is_ignored(self):
        self.assertIsNone(watchdog.disconnected_since(["x Disconnected from Home Assistant"]))


if __name__ == "__main__":
    unittest.main()
