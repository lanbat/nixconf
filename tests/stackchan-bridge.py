"""The Stack-chan bridge's decisions (pkgs/lva-stackchan), without a device or LVA.

Brain turns Linux Voice Assistant events, the device's messages and the clock
into lines for the device and commands for LVA; these feed it scripted turns
and check what comes out.

Run with: nix build .#checks.x86_64-linux.stackchan-bridge
"""
import math
import os
import struct
import sys
import time
import unittest

os.environ["TZ"] = "UTC"
time.tzset()
sys.path.insert(0, sys.argv.pop(1))

from stackchan_bridge import (  # noqa: E402
    Brain,
    Config,
    DEFAULT_SOUND_GEOMETRY,
    DOA_CHANNELS,
    DOA_RATE,
    DOA_WINDOW_S,
    MicWindow,
    _angle_moved,
    envelope_level,
    estimate_direction,
    parse_geometry,
)

NOON = 1_800_000_000 - 1_800_000_000 % 86400 + 12 * 3600  # 12:00 UTC
MIDNIGHT = NOON + 12 * 3600  # 00:00 UTC, the next day


def device(actions):
    return [a[1] for a in actions if a[0] == "device"]


def lva(actions):
    return [a[1] for a in actions if a[0] == "lva"]


def merged(actions):
    out = {}
    for line in device(actions):
        out.update(line)
    return out


class Turn(unittest.TestCase):
    def setUp(self):
        self.brain = Brain(Config(), now=NOON)

    def test_a_turn_from_wake_word_to_idle(self):
        b = self.brain
        wake = merged(b.on_lva("wake_word_detected", {}, NOON))
        self.assertEqual(wake["mood"], "listening")
        self.assertEqual(wake["look"], "user")
        self.assertEqual(wake["caption"]["who"], "status")

        said = merged(b.on_lva("stt_text", {"text": "what time is it"}, NOON))
        self.assertEqual(said["caption"], {"text": "what time is it", "who": "user", "ms": 8000})

        thinking = merged(b.on_lva("thinking", {}, NOON))
        self.assertEqual(thinking["mood"], "thinking")
        self.assertEqual(thinking["look"], "up")

        reply = b.on_lva("tts_text", {"text": "It is noon."}, NOON)
        self.assertEqual(merged(reply)["caption"]["who"], "assistant")
        self.assertEqual(merged(reply)["mood"], "happy")

        self.assertIn(("mouth", True), b.on_lva("tts_speaking", {}, NOON))
        finished = b.on_lva("tts_finished", {}, NOON)
        self.assertIn(("mouth", False), finished)
        self.assertEqual(merged(finished)["mouth"], 0)

        idle = merged(b.on_lva("idle", {}, NOON))
        self.assertEqual(idle["mood"], "neutral")
        self.assertEqual(idle["look"], "track")
        self.assertFalse(b.in_turn)

    def test_reply_moods(self):
        b = self.brain
        cases = {
            "Sorry, I couldn't find a device called kitchen.": "sad",
            "I can't do that.": "sad",
            "Wow, that's a big number!": "happy",
            "Turned on the light.": "happy",
            "Which light do you mean?": "curious",
        }
        for text, mood in cases.items():
            with self.subTest(text=text):
                self.assertEqual(merged(b.on_lva("tts_text", {"text": text}, NOON))["mood"], mood)

    def test_errors_and_connection(self):
        b = self.brain
        err = merged(b.on_lva("pipeline_error", {"reason": "stt-no-text"}, NOON))
        self.assertEqual(err["mood"], "confused")
        self.assertEqual(err["gesture"], "shake")
        off = merged(b.on_lva("disconnected", {}, NOON))
        self.assertEqual(off["status"]["online"], False)
        on = merged(b.on_lva("zeroconf", {"status": "connected"}, NOON))
        self.assertEqual(on["status"]["online"], True)

    def test_muted(self):
        b = self.brain
        self.assertEqual(merged(b.on_lva("muted", {"muted": True}, NOON))["status"]["muted"], True)
        self.assertEqual(merged(b.on_lva("muted", {"muted": False}, NOON))["status"]["muted"], False)

    def test_snapshot_restores_status(self):
        snap = merged(self.brain.on_lva("snapshot", {"muted": True, "ha_connected": False}, NOON))
        self.assertEqual(snap["status"], {"muted": True, "online": False})

    def test_captions_can_be_turned_off(self):
        b = Brain(Config(captions=False), now=NOON)
        for event, data in [("wake_word_detected", {}), ("stt_text", {"text": "hi"}), ("tts_text", {"text": "Hello."})]:
            self.assertNotIn("caption", merged(b.on_lva(event, data, NOON)))

    def test_continued_conversation_stays_in_turn(self):
        b = self.brain
        b.on_lva("wake_word_detected", {}, NOON)
        b.on_lva("tts_finished", {}, NOON)
        again = merged(b.on_lva("listening", {}, NOON))
        self.assertEqual(again["mood"], "listening")
        self.assertTrue(b.in_turn)


class Touch(unittest.TestCase):
    def test_tap_starts_a_conversation(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"touch": "tap", "zone": "middle"}, NOON)
        self.assertEqual(lva(out), [{"command": "start_listening"}])

    def test_tap_during_a_turn_stops_it(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        out = b.on_device({"touch": "tap", "zone": "front"}, NOON)
        self.assertEqual(lva(out), [{"command": "stop_pipeline"}])

    def test_any_touch_silences_a_ringing_timer(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("timer_ringing", {"id": "t1", "name": "", "total_seconds": 60, "seconds_left": 0}, NOON)
        out = b.on_device({"touch": "stroke", "zone": "back"}, NOON)
        self.assertEqual(lva(out), [{"command": "stop_timer_ringing"}])

    def test_stroke_is_affection_not_a_command(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"touch": "stroke", "zone": "back"}, NOON)
        self.assertEqual(lva(out), [])
        self.assertEqual(merged(out)["mood"], "happy")

    def test_touch_to_talk_can_be_turned_off(self):
        b = Brain(Config(touch_to_talk=False), now=NOON)
        out = b.on_device({"touch": "tap", "zone": "middle"}, NOON)
        self.assertEqual(lva(out), [])
        self.assertEqual(merged(out)["mood"], "happy")


class Timers(unittest.TestCase):
    def timer(self, left, tid="t1", total=300, name="pasta"):
        return {"id": tid, "name": name, "total_seconds": total, "seconds_left": left}

    def test_timer_is_shown_and_counted_down_locally(self):
        b = Brain(Config(), now=NOON)
        shown = merged(b.on_lva("timer_ticking", self.timer(300), NOON))
        self.assertEqual(
            shown["timers"],
            [{"id": "t1", "name": "pasta", "remaining_s": 300, "total_s": 300, "ringing": False}],
        )
        later = merged(b.tick(NOON + 100))
        self.assertEqual(later["timers"][0]["remaining_s"], 200)

    def test_ringing_then_stopped(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("timer_ticking", self.timer(5), NOON)
        ring = merged(b.on_lva("timer_ringing", self.timer(0), NOON + 5))
        self.assertTrue(ring["timers"][0]["ringing"])
        self.assertEqual(ring["mood"], "surprised")
        self.assertEqual(ring["gesture"], "wiggle")
        done = merged(b.on_lva("idle", {}, NOON + 9))
        self.assertEqual(done["timers"], [])

    def test_idle_in_the_middle_of_a_turn_is_a_cancel(self):
        # LVA reports a cancelled timer as a bare idle, which arrives between
        # the transcript and the reply.
        b = Brain(Config(), now=NOON)
        b.on_lva("timer_ticking", self.timer(300), NOON)
        b.on_lva("wake_word_detected", {}, NOON + 10)
        b.on_lva("stt_text", {"text": "cancel the timer"}, NOON + 11)
        out = merged(b.on_lva("idle", {}, NOON + 12))
        self.assertEqual(out["timers"], [])
        self.assertTrue(b.in_turn)  # the reply is still to come

    def test_idle_after_a_turn_keeps_the_timer(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        b.on_lva("stt_text", {"text": "set a timer for 5 minutes"}, NOON)
        b.on_lva("timer_ticking", self.timer(300), NOON)
        b.on_lva("tts_text", {"text": "Timer set."}, NOON)
        b.on_lva("tts_finished", {}, NOON)
        out = merged(b.on_lva("idle", {}, NOON + 1))
        self.assertEqual(len(out.get("timers", b.timers_payload(NOON + 1))), 1)

    def test_a_timer_that_never_rang_is_dropped_a_minute_after_it_ended(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("timer_ticking", self.timer(10), NOON)
        self.assertEqual(len(b.timers_payload(NOON + 30)), 1)
        self.assertEqual(merged(b.tick(NOON + 75))["timers"], [])


class Night(unittest.TestCase):
    def test_falls_asleep_at_night_and_wakes_in_the_morning(self):
        b = Brain(Config(night_start="23:00", night_end="07:00"), now=NOON)
        self.assertEqual(merged(b.tick(MIDNIGHT))["sleep"], True)
        self.assertEqual(b.tick(MIDNIGHT + 60), [])  # only on the change
        self.assertEqual(merged(b.tick(MIDNIGHT + 8 * 3600))["sleep"], False)

    def test_a_turn_wakes_it_and_it_goes_back_to_sleep(self):
        b = Brain(Config(night_start="23:00", night_end="07:00"), now=MIDNIGHT)
        self.assertEqual(merged(b.on_lva("wake_word_detected", {}, MIDNIGHT))["sleep"], False)
        self.assertEqual(merged(b.on_lva("idle", {}, MIDNIGHT + 10))["sleep"], True)

    def test_no_night_when_unset(self):
        b = Brain(Config(night_start="", night_end=""), now=MIDNIGHT)
        self.assertNotIn("sleep", merged(b.tick(MIDNIGHT + 60)))


class Device(unittest.TestCase):
    def test_hello_gets_the_whole_state(self):
        b = Brain(Config(brightness=150), now=NOON)
        b.on_lva("muted", {"muted": True}, NOON)
        state = merged(b.on_device({"hello": {"fw": "1.0.0", "proto": 1}}, NOON))
        self.assertEqual(state["status"]["muted"], True)
        self.assertEqual(state["config"]["brightness"], 150)
        self.assertEqual(state["mood"], "neutral")
        self.assertEqual(state["sleep"], False)
        self.assertIn("timers", state)

    def test_wrong_protocol_is_reported(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"hello": {"fw": "9.0.0", "proto": 99}}, NOON)
        self.assertTrue(any(a[0] == "log" and "protocol" in a[1] for a in out))


class Router(unittest.TestCase):
    """The assistant router's side (pkgs/assistant-router body.py)."""

    def router(self, actions):
        return [a[1] for a in actions if a[0] == "router"]

    def test_the_routers_mood_wins_over_the_guess(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        acted = merged(b.on_router({"act": {"mood": "excited", "gesture": "dance"}}, NOON + 1))
        self.assertEqual(acted["mood"], "excited")
        self.assertEqual(acted["gesture"], "dance")
        # "Sorry" would make the guess sad; the router said excited.
        reply = merged(b.on_lva("tts_text", {"text": "Sorry, I just love dancing!"}, NOON + 2))
        self.assertEqual(reply["mood"], "excited")

    def test_the_routers_mood_is_forgotten_after_a_while(self):
        b = Brain(Config(), now=NOON)
        b.on_router({"act": {"mood": "excited"}}, NOON)
        self.assertEqual(merged(b.on_lva("tts_text", {"text": "Sorry."}, NOON + 30))["mood"], "sad")

    def test_look_commands(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(merged(b.on_router({"act": {"look": "user"}}, NOON))["look"], "user")
        self.assertEqual(merged(b.on_router({"act": {"gesture": "look_at_user"}}, NOON))["look"], "user")
        self.assertEqual(merged(b.on_router({"act": {"gesture": "look_around"}}, NOON))["gesture"], "look_around")

    def test_unknown_values_are_ignored(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(b.on_router({"act": {"mood": "furious", "gesture": "backflip", "look": "away"}}, NOON), [])
        self.assertEqual(b.on_router({"hello": 1}, NOON), [])

    def test_a_capture_request_reaches_the_device(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(device(b.on_router({"capture": True}, NOON)), [{"capture": True}])
        # An act and a capture never arrive together; a bare act is unaffected.
        self.assertEqual(device(b.on_router({"act": {"mood": "happy"}}, NOON)), [{"mood": "happy"}])

    def test_a_captured_face_reaches_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.router(b.on_device({"face_image": "aW1hZ2U="}, NOON)), [{"face_image": "aW1hZ2U="}])

    def test_a_failed_capture_reaches_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.router(b.on_device({"face_image": None}, NOON)), [{"face_image": None}])

    def test_a_face_image_does_not_disturb_presence(self):
        b = Brain(Config(), now=NOON)
        # face_image is not the periodic "face" presence report.
        self.assertNotIn("state", self.router(b.on_device({"face_image": "aW1hZ2U="}, NOON))[0])

    def test_go_to_sleep_by_day_lasts_until_the_night_changes(self):
        b = Brain(Config(night_start="23:00", night_end="07:00"), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        b.on_router({"act": {"sleep": True}}, NOON + 1)
        self.assertEqual(merged(b.on_lva("idle", {}, NOON + 3))["sleep"], True)  # after the reply
        self.assertNotIn("sleep", merged(b.tick(NOON + 60)))  # still day: it stays asleep
        self.assertEqual(merged(b.tick(MIDNIGHT))["sleep"], True)
        self.assertEqual(merged(b.tick(MIDNIGHT + 8 * 3600))["sleep"], False)

    def test_wake_up_at_night(self):
        b = Brain(Config(night_start="23:00", night_end="07:00"), now=MIDNIGHT)
        b.on_lva("wake_word_detected", {}, MIDNIGHT)
        b.on_router({"act": {"sleep": False}}, MIDNIGHT + 1)
        self.assertNotIn("sleep", merged(b.on_lva("idle", {}, MIDNIGHT + 3)))
        self.assertFalse(b.asleep)

    def test_presence_is_reported_on_change(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.router(b.on_device({"face": "new"}, NOON)),
                         [{"state": {"present": True, "present_since_s": 0, "asleep": False, "battery_low": False}}])
        self.assertEqual(self.router(b.on_device({"face": "new"}, NOON + 1)), [])
        self.assertEqual(self.router(b.on_device({"face": "lost"}, NOON + 90)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": False, "battery_low": False}}])

    def test_state_on_connect(self):
        b = Brain(Config(), now=NOON)
        b.on_device({"face": "new"}, NOON)
        self.assertEqual(b.router_state(NOON + 125), {"present": True, "present_since_s": 125, "asleep": False, "battery_low": False})


class Nap(unittest.TestCase):
    """The robot naps by itself when nobody has been around (firmware)."""

    def router(self, actions):
        return [a[1] for a in actions if a[0] == "router"]

    def test_the_nap_time_goes_to_the_robot(self):
        b = Brain(Config(nap_after_s=600), now=NOON)
        state = merged(b.on_device({"hello": {"fw": "1.0.0", "proto": 1}}, NOON))
        self.assertEqual(state["config"]["nap_after_s"], 600)
        self.assertEqual(Config().nap_after_s, 900)
        self.assertEqual(Config.from_env({"NAP_AFTER_S": "0"}).nap_after_s, 0)

    def test_a_nap_is_sleep_to_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.router(b.on_device({"rest": "nap"}, NOON)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": True, "battery_low": False}}])
        self.assertEqual(self.router(b.on_device({"rest": "nap"}, NOON + 1)), [])
        self.assertEqual(self.router(b.on_device({"rest": "awake"}, NOON + 60)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": False, "battery_low": False}}])
        self.assertEqual(b.on_device({"rest": "snoring"}, NOON + 61), [])


class Battery(unittest.TestCase):
    def router(self, actions):
        return [a[1] for a in actions if a[0] == "router"]

    def test_a_low_battery_reaches_the_router_and_the_journal(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"battery": {"level": 12, "mv": 3500, "charging": False, "low": True}}, NOON)
        self.assertEqual(self.router(out)[0]["state"]["battery_low"], True)
        self.assertTrue(any(a[0] == "log" and "battery" in a[1] for a in out))
        # Only changes are passed on.
        self.assertEqual(self.router(b.on_device({"battery": {"level": 11, "low": True}}, NOON + 30)), [])
        out = b.on_device({"battery": {"level": 30, "charging": True, "low": False}}, NOON + 600)
        self.assertEqual(self.router(out)[0]["state"]["battery_low"], False)

    def test_a_bad_battery_line_is_ignored(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(b.on_device({"battery": "full"}, NOON), [])
        self.assertEqual(b.on_device({"battery": {"low": "yes"}}, NOON), [])


class Envelope(unittest.TestCase):
    def test_silence_is_closed_and_speech_opens(self):
        quiet = b"\x00\x00" * 400
        loud = (b"\x00\x40" + b"\x00\xc0") * 200  # +/-16384
        self.assertEqual(envelope_level(quiet), 0.0)
        self.assertGreater(envelope_level(loud), 0.4)
        self.assertLessEqual(envelope_level(loud), 1.0)


def wave(theta_deg, n=2048, fs=DOA_RATE, geom=DEFAULT_SOUND_GEOMETRY, speed=343.0):
    """A plane wave arriving at theta_deg (0 straight ahead, + to the left) at
    the four mics: each mic's trace is the reference shifted by the extra path
    the wave travels to reach it, in samples."""
    th = math.radians(theta_deg)
    ux, uy = -math.sin(th), math.cos(th)  # toward the source

    def ref(k):
        return (math.sin(2 * math.pi * 997 * k / fs)
                + 0.5 * math.sin(2 * math.pi * 1321 * k / fs)
                + 0.3 * math.sin(2 * math.pi * 713 * k / fs))

    out = []
    for x, y in parse_geometry(geom):
        shift = (x * ux + y * uy) / speed * fs
        out.append([ref(i + shift) for i in range(n)])
    return out


class Geometry(unittest.TestCase):
    def test_four_points_parse(self):
        pts = parse_geometry("-0.01,-0.01;0.01,-0.01;0.01,0.01;-0.01,0.01")
        self.assertEqual(pts, [(-0.01, -0.01), (0.01, -0.01), (0.01, 0.01), (-0.01, 0.01)])

    def test_not_four_points_is_none(self):
        self.assertIsNone(parse_geometry("0,0;0,1;1,0"))
        self.assertIsNone(parse_geometry("0,0;0,1;1,0;1,1;0,2"))

    def test_bad_token_is_none(self):
        self.assertIsNone(parse_geometry("0,0;0,1;1,0;1,x"))
        self.assertIsNone(parse_geometry("0;1;2;3"))


class Direction(unittest.TestCase):
    def test_a_wave_at_a_known_angle_is_found(self):
        for theta in (0, 30, -30, 60, -60, 90):
            with self.subTest(theta=theta):
                est = estimate_direction(wave(theta), DOA_RATE, DEFAULT_SOUND_GEOMETRY)
                self.assertIsNotNone(est)
                angle, confidence = est
                self.assertLess(abs(angle - theta), 2.0)
                self.assertGreater(confidence, 0.25)

    def test_silence_is_no_direction(self):
        self.assertIsNone(estimate_direction([[0.0] * 2048 for _ in range(4)], DOA_RATE, DEFAULT_SOUND_GEOMETRY))

    def test_too_few_samples_is_no_direction(self):
        self.assertIsNone(estimate_direction([c[:300] for c in wave(30)], DOA_RATE, DEFAULT_SOUND_GEOMETRY))

    def test_unrelated_mics_is_no_direction(self):
        import random
        rng = random.Random(7)
        noise = [[rng.uniform(-1.0, 1.0) for _ in range(1000)] for _ in range(4)]
        self.assertIsNone(estimate_direction(noise, DOA_RATE, DEFAULT_SOUND_GEOMETRY))

    def test_bad_geometry_is_no_direction(self):
        self.assertIsNone(estimate_direction(wave(30), DOA_RATE, "nope"))
        self.assertIsNone(estimate_direction(wave(30)[:3], DOA_RATE, DEFAULT_SOUND_GEOMETRY))


class Mic(unittest.TestCase):
    def frame(self, *values):
        return b"".join(struct.pack("<h", v) for v in values)

    def test_empty_window_is_none(self):
        self.assertIsNone(MicWindow(4, 8).snapshot())

    def test_a_partial_frame_keeps_it_none(self):
        w = MicWindow(4, 8)
        w.push(self.frame(1, 2, 3, 4) + self.frame(5, 6))  # second frame incomplete
        self.assertIsNone(w.snapshot())

    def test_a_full_window_keeps_the_last_frames(self):
        w = MicWindow(4, 8)
        for i in range(12):
            w.push(self.frame(100 + i, 101 + i, 102 + i, 103 + i))
        snap = w.snapshot()
        self.assertEqual(len(snap), 4)
        self.assertEqual(len(snap[0]), 8)
        self.assertEqual(snap[0][0], 104 / 32768.0)  # the fifth frame pushed (12 - 8)
        self.assertEqual(snap[3][-1], (103 + 11) / 32768.0)  # the last frame


class AngleMoved(unittest.TestCase):
    def test_same_and_half_turns(self):
        self.assertEqual(_angle_moved(10, 10), 0.0)
        self.assertEqual(_angle_moved(0, 180), 180.0)
        self.assertEqual(_angle_moved(90, 0), 90.0)

    def test_across_zero(self):
        self.assertEqual(_angle_moved(350, 10), 20.0)
        self.assertEqual(_angle_moved(-170, 20), 170.0)


class Sound(unittest.TestCase):
    """The head turns to the voice, but only when it has moved on."""

    def setUp(self):
        self.b = Brain(Config(sound_turn_min_deg=15.0), now=NOON)

    def yaw(self, angle, confidence=0.9):
        out = self.b.on_sound(angle, confidence, NOON)
        yaws = [a[1]["yaw"] for a in out if a[0] == "device" and "yaw" in a[1]]
        return yaws[0] if yaws else None

    def test_it_turns_when_the_voice_is_far_enough(self):
        self.assertIsNone(self.yaw(10))  # only 10 degrees on: not far enough
        self.assertEqual(self.yaw(20), 200)  # 20 from straight ahead: turn
        self.assertEqual(self.b.sound_angle, 20.0)

    def test_small_wobbles_do_not_return(self):
        self.assertEqual(self.yaw(20), 200)
        self.assertIsNone(self.yaw(25))  # 5 degrees on: a wobble
        self.assertEqual(self.yaw(40), 400)  # 20 more: turn again

    def test_the_offset_shifts_the_target(self):
        b = Brain(Config(sound_turn_min_deg=15.0, sound_offset_deg=5.0), now=NOON)
        out = b.on_sound(10, 0.9, NOON)  # target 15: far enough from 0
        self.assertEqual([a[1]["yaw"] for a in out if a[0] == "device"], [150])

    def test_the_yaw_is_clamped_to_the_servo(self):
        self.assertEqual(self.yaw(80), 700)  # clamped to +70 degrees
        self.assertEqual(self.yaw(-80), -700)  # clamped to -70 degrees

    def test_around_the_back(self):
        self.assertEqual(self.yaw(20), 200)
        self.assertEqual(self.yaw(-170), -700)  # 170 degrees on, clamped


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
