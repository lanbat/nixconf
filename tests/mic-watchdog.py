"""The microphone watchdog (pkgs/mic-watchdog): which card it watches, and when
a storm of failed reads resets it, without PipeWire.

Run with: nix build .#checks.x86_64-linux.mic-watchdog
"""
import sys
import unittest

sys.path.insert(0, sys.argv.pop(1))

import watchdog  # noqa: E402

FAIL = "spa.alsa: surround40:3c: snd_pcm_mmap_commit error 98 86 98: Broken pipe"
DUMP = [
    {"id": 65, "type": "PipeWire:Interface:Device",
     "info": {"props": {"device.api": "v4l2", "device.vendor.id": "0x1415", "device.product.id": "0x2000"}}},
    {"id": 75, "type": "PipeWire:Interface:Device",
     "info": {"props": {"device.api": "alsa", "api.alsa.card": "3", "device.vendor.id": "0x1415",
                        "device.product.id": "0x2000"},
              "params": {"Profile": [{"index": 1, "name": "input:analog-surround-40"}]}}},
    {"id": 76, "type": "PipeWire:Interface:Device",
     "info": {"props": {"device.api": "alsa", "api.alsa.card": "0", "device.vendor.id": "0x0d8c",
                        "device.product.id": "0x0102"},
              "params": {"Profile": [{"index": 1}]}}},
]


class FindTheMicrophone(unittest.TestCase):
    def test_its_sound_card_not_its_camera(self):
        self.assertEqual(watchdog.find_mic(DUMP, "1415:2000"), (75, "3", 1))
        self.assertEqual(watchdog.find_mic(DUMP, "1415:2000".upper()), (75, "3", 1))

    def test_none_when_it_is_not_there(self):
        self.assertIsNone(watchdog.find_mic(DUMP, "046d:0825"))
        self.assertIsNone(watchdog.find_mic([], "1415:2000"))
        self.assertIsNone(watchdog.find_mic({"not": "a list"}, "1415:2000"))
        bare = [dict(DUMP[1], info={"props": DUMP[1]["info"]["props"]})]  # no active profile
        self.assertIsNone(watchdog.find_mic(bare, "1415:2000"))


class Storm(unittest.TestCase):
    def storm(self):
        s = watchdog.Storm(errors=30, window_s=60, cooldown_s=300)
        s.watch("3")
        return s

    def test_a_storm_of_failed_reads_resets_once(self):
        s = self.storm()
        self.assertFalse(any(s.add(FAIL, t) for t in range(29)))
        self.assertTrue(s.add(FAIL, 29.5))
        self.assertFalse(any(s.add(FAIL, 30 + t / 25) for t in range(200)))  # cooling down

    def test_again_after_the_cooldown(self):
        s = self.storm()
        for t in range(30):
            s.add(FAIL, t)
        self.assertTrue(any(s.add(FAIL, 400 + t / 25) for t in range(40)))

    def test_a_few_failures_now_and_then_are_not_a_storm(self):
        s = self.storm()
        self.assertFalse(any(s.add(FAIL, t * 5.0) for t in range(200)))  # one every 5 s

    def test_other_cards_and_playback_are_not_the_microphone(self):
        s = self.storm()
        other = FAIL.replace(":3c:", ":4c:")
        playback = FAIL.replace(":3c:", ":3p:")
        noise = "spa.alsa: surround40:3c: follower delay:609 target:131 thr:86 resample:1, resync"
        self.assertFalse(any(s.add(line, t / 25) for t in range(100) for line in (other, playback, noise)))

    def test_a_new_card_number_starts_counting_again(self):
        s = self.storm()
        for t in range(20):
            s.add(FAIL, t)
        s.watch("4")
        self.assertFalse(any(s.add(FAIL, 20 + t) for t in range(40)))  # card 3's lines: not this card
        self.assertTrue(any(s.add(FAIL.replace(":3c:", ":4c:"), 60 + t / 25) for t in range(30)))

    def test_nothing_without_a_card(self):
        s = watchdog.Storm(errors=1, window_s=60, cooldown_s=0)
        self.assertFalse(s.add(FAIL, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
