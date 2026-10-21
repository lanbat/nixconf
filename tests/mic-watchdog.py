"""The microphone watchdog (pkgs/mic-watchdog): which card it watches, and when
a storm of failed reads resets it, without PipeWire.

Run with: nix build .#checks.x86_64-linux.mic-watchdog
"""
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

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


class SilentStall(unittest.TestCase):
    """A capture that gives only zeros (2026-10-21), and the ladder it climbs."""

    def ladder(self, checks=3):
        return watchdog.Silence(checks=checks, notice_s=1800)

    def run_checks(self, s, n, silent=True, t0=0.0, every=30.0):
        return [s.add(silent, t0 + i * every) for i in range(n)]

    def test_zeros_and_short_reads_are_silent_sound_is_not(self):
        n = 64000
        self.assertTrue(watchdog.is_silent(bytes(n), n))
        self.assertTrue(watchdog.is_silent(b"", n))  # nothing came in the time allowed
        self.assertTrue(watchdog.is_silent(b"\x01" * 100, n))  # too little, however loud
        self.assertFalse(watchdog.is_silent(bytes(n - 1) + b"\x01", n))  # one LSB of noise is a live mic
        self.assertFalse(watchdog.is_silent(b"\xfe\xff" * (n // 2), n))

    def test_three_silent_checks_reopen_the_card(self):
        s = self.ladder()
        self.assertEqual(self.run_checks(s, 3), [None, None, "reset"])

    def test_sound_between_silent_checks_starts_the_count_again(self):
        s = self.ladder()
        self.assertEqual(self.run_checks(s, 2), [None, None])
        self.assertIsNone(s.add(False, 60))
        self.assertEqual(self.run_checks(s, 3, t0=90), [None, None, "reset"])

    def test_the_ladder_climbs_one_step_per_run_of_silent_checks(self):
        s = self.ladder()
        steps = [a for a in self.run_checks(s, 12) if a]
        self.assertEqual(steps, ["reset", "reauthorize", "replug"])

    def test_it_asks_for_the_plug_to_be_pulled_now_and_then_not_every_time(self):
        s = self.ladder()
        steps = [a for a in self.run_checks(s, 6 + 3 * 100) if a]  # 100 more runs, 90 s each, 2.5 hours
        self.assertEqual(steps[:3], ["reset", "reauthorize", "replug"])
        self.assertEqual(steps[3:], ["replug"] * (len(steps) - 3))
        self.assertTrue(4 <= len(steps) - 2 <= 8)  # ~ every 30 min over 2.5 h, not 100 times

    def test_sound_puts_it_back_at_the_bottom_of_the_ladder(self):
        s = self.ladder()
        self.run_checks(s, 6)  # reset, reauthorize
        s.add(False, 200)
        self.assertEqual(self.run_checks(s, 3, t0=230), [None, None, "reset"])

    def test_a_reopen_by_the_storm_check_gives_it_a_fresh_run_but_keeps_the_step(self):
        s = self.ladder()
        self.run_checks(s, 5)  # two into the second run
        s.reopened()
        self.assertEqual(self.run_checks(s, 3, t0=200), [None, None, "reauthorize"])

    def test_the_storm_check_waits_out_a_reset_made_by_the_silent_check(self):
        s = watchdog.Storm(errors=30, window_s=60, cooldown_s=300)
        s.watch("3")
        s.cooled(1000.0)
        fail = "spa.alsa: surround40:3c: snd_pcm_mmap_commit error 98 86 98: Broken pipe"
        self.assertFalse(any(s.add(fail, 1001 + t / 25) for t in range(100)))
        self.assertTrue(any(s.add(fail, 1400 + t / 25) for t in range(100)))


class Sample(unittest.TestCase):
    """sample() against a stand-in for pw-record."""

    def fake(self, d, body):
        path = Path(d) / "pw-record"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return {"PW_RECORD": str(path), **os.environ}

    def test_reads_what_it_is_given_and_no_more(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fake(d, "head -c 200000 /dev/zero\n")
            self.assertEqual(len(watchdog.sample(env, "node", seconds=2.0)), 64000)

    def test_a_recorder_that_says_nothing_is_cut_off(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fake(d, "sleep 60\n")
            data = watchdog.sample(env, "node", seconds=0.2)  # allowed 0.2 + 4 s
            self.assertEqual(data, b"")

    def test_a_recorder_that_exits_early(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fake(d, "printf abc\n")
            self.assertEqual(watchdog.sample(env, "node", seconds=2.0), b"abc")

    def test_it_asks_for_the_node_mono_16k(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fake(d, 'echo "$@" >&2; echo "$@" > "$OUT"\n')
            env["OUT"] = str(Path(d) / "args")
            watchdog.sample(env, "lanbat_ps_eye_capture", seconds=0.1)
            self.assertEqual((Path(d) / "args").read_text().split(),
                             ["--target", "lanbat_ps_eye_capture", "--rate", "16000", "--channels", "1",
                              "--format", "s16", "-"])


class FindTheUsbDevice(unittest.TestCase):
    def tree(self, d):
        for name, vid, pid in (("1-1", "0d8c", "0314"), ("3-2.2", "1415", "2000"), ("usb1", "1d6b", "0002")):
            (Path(d) / name).mkdir()
            (Path(d) / name / "idVendor").write_text(vid + "\n")
            (Path(d) / name / "idProduct").write_text(pid + "\n")
        (Path(d) / "1-0:1.0").mkdir()  # an interface: no IDs of its own

    def test_by_vendor_and_product(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d)
            self.assertEqual(watchdog.usb_device(d, "1415:2000"), Path(d) / "3-2.2")
            self.assertEqual(watchdog.usb_device(d, "1415:2000".upper()), Path(d) / "3-2.2")
            self.assertIsNone(watchdog.usb_device(d, "046d:0825"))

    def test_the_switch_is_flipped_off_then_on(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d)
            (Path(d) / "3-2.2" / "authorized").write_text("1")
            writes = []
            real = Path.write_text
            with unittest.mock.patch.object(Path, "write_text",
                                            lambda self, v, *a, **k: (writes.append((self.name, v)), real(self, v, *a, **k))[1]), \
                    unittest.mock.patch.object(watchdog.time, "sleep", lambda s: None):
                self.assertTrue(watchdog.reauthorize({"USB_SYSFS": d}, "1415:2000"))
            self.assertEqual(writes, [("authorized", "0"), ("authorized", "1")])
            self.assertFalse(watchdog.reauthorize({"USB_SYSFS": d}, "046d:0825"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
