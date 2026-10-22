"""The duck service's voice boost (pkgs/lva-snapcast-duck): how loud the room
is, how much louder a reply gets, and which streams it touches, without
PipeWire (pw-dump and wpctl are fakes).

Run with: nix build .#checks.x86_64-linux.lva-snapcast-duck
"""
import asyncio
import json
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, sys.argv.pop(1))
os.environ.setdefault("ADAPT_MIC_NODE", "")

import duck  # noqa: E402


def tone(db, n=16000):
    amp = 32767 * 10 ** (db / 20) * math.sqrt(2)
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n)))


class Levels(unittest.TestCase):
    def test_a_tone_reads_its_level(self):
        self.assertAlmostEqual(duck.level_db(tone(-30)), -30, delta=0.5)
        self.assertEqual(duck.level_db(b"\x00\x00" * 100), -120.0)
        self.assertEqual(duck.level_db(b""), -120.0)

    def test_the_room_is_the_median(self):
        room = duck.Room()
        self.assertIsNone(room.loudness())
        for db in (-60, -40, -58, -20, -57, -59):  # one spike, one door slam
            room.add(db)
        self.assertEqual(room.loudness(), -57)  # the last five: -40 -58 -20 -57 -59


class Boost(unittest.TestCase):
    def test_quiet_rooms_get_nothing_loud_ones_up_to_the_cap(self):
        self.assertEqual(duck.boost_db(None), 0.0)
        self.assertEqual(duck.boost_db(-60, quiet_db=-55, max_boost_db=8), 0.0)
        self.assertEqual(duck.boost_db(-45, quiet_db=-55, max_boost_db=8), 5.0)
        self.assertEqual(duck.boost_db(-10, quiet_db=-55, max_boost_db=8), 8.0)

    def test_never_past_full_scale(self):
        self.assertAlmostEqual(duck.voice_gain(6, 0.4), 10 ** (6 / 20))  # +6 dB fits under full scale
        self.assertAlmostEqual(duck.voice_gain(8, 0.4), 2.5)  # +8 dB would pass it: 0.4 x 2.5 = 1
        self.assertAlmostEqual(duck.voice_gain(8, 0.8), 1.25)  # capped: 0.8 x 1.25 = 1
        self.assertEqual(duck.voice_gain(8, 1.0), 1.0)
        self.assertEqual(duck.voice_gain(0, 0.5), 1.0)

    def test_lva_volume_from_its_preferences(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"volume": 0.4}, f)
        try:
            duck.LVA_PREFS = f.name
            self.assertEqual(duck.lva_volume(), 0.4)
            duck.LVA_PREFS = "/nonexistent"
            self.assertEqual(duck.lva_volume(), 1.0)
        finally:
            os.unlink(f.name)


DUMP = [
    {"id": 1, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.binary": "snapclient"}}},
    {"id": 2, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.binary": "python3.14"}}},
    {"id": 3, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.binary": "pipewire"}}},
    {"id": 10, "type": "PipeWire:Interface:Node",
     "info": {"props": {"client.id": 1, "media.class": "Stream/Output/Audio"}}},
    {"id": 20, "type": "PipeWire:Interface:Node",
     "info": {"props": {"client.id": 2, "media.class": "Stream/Output/Audio"}}},
    {"id": 21, "type": "PipeWire:Interface:Node",
     "info": {"props": {"client.id": 2, "media.class": "Stream/Input/Audio"}}},
    {"id": 30, "type": "PipeWire:Interface:Node",
     "info": {"props": {"client.id": 3, "media.class": "Stream/Output/Audio"}}},
]


class Streams(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.old = duck.run
        duck.run = self.fake_run

    def tearDown(self):
        duck.run = self.old

    def fake_run(self, *args):
        self.calls.append(args)
        return json.dumps(DUMP) if args[0] == duck.PW_DUMP else ""

    def test_the_voice_is_lvas_playback_only(self):
        self.assertEqual(duck.voice_nodes(), [20])
        self.assertEqual(duck.snapcast_nodes(), [10])

    def test_a_reply_in_a_loud_room_is_boosted_then_put_back(self):
        room = duck.Room()
        room.add(-35)  # 20 dB above quiet: +8 dB, the cap
        voice = duck.Voice(room)
        duck.LVA_PREFS = "/nonexistent"  # LVA at full volume: no room to boost

        async def go(base):
            duck.lva_volume = lambda: base
            duck.follow(voice, "tts_speaking")
            if voice.task:
                await voice.task
            boosted = [c for c in self.calls if c[0] == duck.WPCTL and c[1] == "set-volume"]
            duck.follow(voice, "tts_finished")
            mark = len(self.calls)
            await voice.releasing
            back = [c for c in self.calls[mark:] if c[0] == duck.WPCTL][-1]
            duck.follow(voice, "idle")
            return boosted, back

        real = (duck.lva_volume, duck.RELEASE_DELAY, duck.RELEASE_FADE)
        duck.RELEASE_DELAY = duck.RELEASE_FADE = 0.02
        try:
            boosted, back = asyncio.run(go(0.4))
        finally:
            duck.lva_volume, duck.RELEASE_DELAY, duck.RELEASE_FADE = real
        self.assertTrue(boosted)
        self.assertEqual(boosted[-1][2], "20")
        self.assertAlmostEqual(float(boosted[-1][3]), 2.5, places=2)  # +8 dB, capped at full scale
        self.assertEqual(back[1:], ("set-volume", "20", "1.000"))
        self.assertFalse(voice.busy)
        self.assertEqual(voice.boosted, {})

    def volumes_since(self, mark):
        return [float(c[3]) for c in self.calls[mark:] if c[0] == duck.WPCTL and c[1] == "set-volume"]

    def test_the_boost_stays_while_the_last_word_plays_then_ramps_down(self):
        # The end of a reply dropped several dB when the boost came off in one
        # step as the player's "finished" arrived, ahead of the audio buffers.
        room = duck.Room()
        room.add(-35)
        voice = duck.Voice(room)

        async def go():
            duck.follow(voice, "tts_speaking")
            await voice.task
            duck.follow(voice, "tts_finished")
            mark = len(self.calls)
            await asyncio.sleep(0.1)  # inside the delay: nothing changes
            during = self.volumes_since(mark)
            await voice.releasing
            return during, self.volumes_since(mark)

        real = (duck.lva_volume, duck.RELEASE_DELAY, duck.RELEASE_FADE, duck.STEP)
        duck.lva_volume = lambda: 0.4
        duck.RELEASE_DELAY, duck.RELEASE_FADE, duck.STEP = 0.3, 0.1, 0.02
        try:
            during, after = asyncio.run(go())
        finally:
            duck.lva_volume, duck.RELEASE_DELAY, duck.RELEASE_FADE, duck.STEP = real
        self.assertEqual(during, [])
        self.assertGreater(len(after), 2)  # a ramp, not one step
        self.assertEqual(after, sorted(after, reverse=True))
        self.assertEqual(after[-1], 1.0)

    def test_a_second_reply_before_the_release_keeps_the_boost(self):
        # A continued conversation: the next reply starts inside the delay.
        room = duck.Room()
        room.add(-35)
        voice = duck.Voice(room)

        async def go():
            duck.follow(voice, "tts_speaking")
            await voice.task
            duck.follow(voice, "tts_finished")
            mark = len(self.calls)
            await asyncio.sleep(0.05)
            duck.follow(voice, "tts_speaking")
            await asyncio.sleep(0.3)  # past where the release would have run
            return self.volumes_since(mark)

        real = (duck.lva_volume, duck.RELEASE_DELAY, duck.RELEASE_FADE)
        duck.lva_volume = lambda: 0.4
        duck.RELEASE_DELAY, duck.RELEASE_FADE = 0.15, 0.05
        try:
            seen = asyncio.run(go())
        finally:
            duck.lva_volume, duck.RELEASE_DELAY, duck.RELEASE_FADE = real
        self.assertTrue(all(v > 2.4 for v in seen), seen)  # never dips toward 1.0
        self.assertIn(20, voice.boosted)

    def test_a_quiet_room_touches_nothing(self):
        room = duck.Room()
        room.add(-70)
        voice = duck.Voice(room)
        duck.follow(voice, "tts_speaking")
        self.assertIsNone(voice.task)
        self.assertTrue(voice.busy)  # and the room isn't read while it answers


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
