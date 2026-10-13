"""The Stack-chan bridge's decisions (pkgs/lva-stackchan), without a device or LVA.

Brain turns Linux Voice Assistant events, the device's messages and the clock
into lines for the device and commands for LVA; these feed it scripted turns
and check what comes out.

Run with: nix build .#checks.x86_64-linux.stackchan-bridge
"""
import asyncio
import base64
import hashlib
import json
import math
import os
import ssl
import stat
import struct
import sys
import tempfile
import time
import unittest

os.environ["TZ"] = "UTC"
time.tzset()
sys.path.insert(0, sys.argv.pop(1))

from stackchan_bridge import (  # noqa: E402
    Brain,
    Bridge,
    Config,
    DEFAULT_SOUND_GEOMETRY,
    DOA_CHANNELS,
    DOA_RATE,
    DOA_WINDOW_S,
    MicWindow,
    WifiLink,
    _angle_moved,
    phones_payload,
    ensure_identity,
    envelope_level,
    estimate_direction,
    parse_geometry,
    read_wifi_credentials,
    wifi_payload,
)

NOON = 1_800_000_000 - 1_800_000_000 % 86400 + 12 * 3600  # 12:00 UTC
MIDNIGHT = NOON + 12 * 3600  # 00:00 UTC, the next day


def device(actions):
    return [a[1] for a in actions if a[0] == "device"]


def lva(actions):
    return [a[1] for a in actions if a[0] == "lva"]


CORE = ("present", "present_since_s", "asleep", "battery_low", "ear")


def core(msg):
    """A router message with its state cut to what the presence tests check
    (the rest is for Home Assistant, tested in RobotInHomeAssistant)."""
    if isinstance(msg, dict) and isinstance(msg.get("state"), dict):
        return {**msg, "state": {k: v for k, v in msg["state"].items() if k in CORE}}
    return msg


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
        return [core(a[1]) for a in actions if a[0] == "router"]

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
                         [{"state": {"present": True, "present_since_s": 0, "asleep": False, "battery_low": False, "ear": False}}])
        self.assertEqual(self.router(b.on_device({"face": "new"}, NOON + 1)), [])
        self.assertEqual(self.router(b.on_device({"face": "lost"}, NOON + 90)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": False, "battery_low": False, "ear": False}}])

    def test_state_on_connect(self):
        b = Brain(Config(), now=NOON)
        b.on_device({"face": "new"}, NOON)
        self.assertEqual(core({"state": b.router_state(NOON + 125)})["state"], {"present": True, "present_since_s": 125, "asleep": False, "battery_low": False, "ear": False})


class Nap(unittest.TestCase):
    """The robot naps by itself when nobody has been around (firmware)."""

    def router(self, actions):
        return [core(a[1]) for a in actions if a[0] == "router"]

    def test_the_nap_time_goes_to_the_robot(self):
        b = Brain(Config(nap_after_s=600), now=NOON)
        state = merged(b.on_device({"hello": {"fw": "1.0.0", "proto": 1}}, NOON))
        self.assertEqual(state["config"]["nap_after_s"], 600)
        self.assertEqual(Config().nap_after_s, 900)
        self.assertEqual(Config.from_env({"NAP_AFTER_S": "0"}).nap_after_s, 0)

    def test_a_nap_is_sleep_to_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.router(b.on_device({"rest": "nap"}, NOON)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": True, "battery_low": False, "ear": False}}])
        self.assertEqual(self.router(b.on_device({"rest": "nap"}, NOON + 1)), [])
        self.assertEqual(self.router(b.on_device({"rest": "awake"}, NOON + 60)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": False, "battery_low": False, "ear": False}}])
        self.assertEqual(b.on_device({"rest": "snoring"}, NOON + 61), [])


class Battery(unittest.TestCase):
    def router(self, actions):
        return [core(a[1]) for a in actions if a[0] == "router"]

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


WIFI = wifi_payload("Home", "hunter22", "192.0.2.12", 8771, "ab" * 32, "cd" * 16)


class WifiProvisioning(unittest.TestCase):
    """The Wi-Fi details reach the robot over USB, only when it lacks them."""

    def hello(self, brain, **hello):
        return brain.on_device({"hello": {"fw": "1.2.0", "proto": 1, **hello}}, NOON)

    def wifi_lines(self, actions):
        return [line["wifi"] for line in device(actions) if "wifi" in line]

    def test_a_robot_without_them_gets_them_over_usb(self):
        b = Brain(Config(), now=NOON, wifi=WIFI)
        self.assertEqual(self.wifi_lines(self.hello(b, link="usb", wifi="")), [WIFI])

    def test_a_robot_with_them_gets_nothing(self):
        b = Brain(Config(), now=NOON, wifi=WIFI)
        self.assertEqual(self.wifi_lines(self.hello(b, link="usb", wifi=WIFI["id"])), [])

    def test_changed_details_are_sent_again(self):
        b = Brain(Config(), now=NOON, wifi=WIFI)
        self.assertEqual(self.wifi_lines(self.hello(b, link="usb", wifi="0123456789ab")), [WIFI])

    def test_never_over_wifi(self):
        b = Brain(Config(), now=NOON, wifi=WIFI)
        self.assertEqual(self.wifi_lines(self.hello(b, link="wifi", wifi="")), [])

    def test_without_wifi_here_the_robot_forgets_its_details(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.wifi_lines(self.hello(b, link="usb", wifi=WIFI["id"])), [None])
        self.assertEqual(self.wifi_lines(self.hello(b, link="usb", wifi="")), [])

    def test_old_firmware_is_left_alone(self):
        b = Brain(Config(), now=NOON, wifi=WIFI)
        out = b.on_device({"hello": {"fw": "1.1.0", "proto": 1}}, NOON)
        self.assertEqual(self.wifi_lines(out), [])
        self.assertIn("mood", merged(out))  # still the whole state

    def test_the_id_follows_every_detail(self):
        ids = {wifi_payload(*args)["id"] for args in (
            ("Home", "hunter22", "192.0.2.12", 8771, "ab" * 32, "cd" * 16),
            ("Home", "hunter23", "192.0.2.12", 8771, "ab" * 32, "cd" * 16),
            ("Home", "hunter22", "192.0.2.13", 8771, "ab" * 32, "cd" * 16),
            ("Home", "hunter22", "192.0.2.12", 8771, "ab" * 32, "ce" * 16),
        )}
        self.assertEqual(len(ids), 4)
        self.assertEqual(WIFI["id"], wifi_payload("Home", "hunter22", "192.0.2.12", 8771, "ab" * 32, "cd" * 16)["id"])

    def test_credentials_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stackchan-wifi")
            with open(path, "w") as f:
                f.write("ssid=My Home\npassword=pa=ss word\n# a comment\n")
            self.assertEqual(read_wifi_credentials(path), ("My Home", "pa=ss word"))
            with open(path, "w") as f:
                f.write("password=only\n")
            self.assertIsNone(read_wifi_credentials(path))
            self.assertIsNone(read_wifi_credentials(os.path.join(d, "missing")))


class WifiLinkTest(unittest.TestCase):
    """The bridge's side of the Wi-Fi link, over real TLS on the loopback."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.identity = ensure_identity(cls.dir.name, os.environ.get("OPENSSL", "openssl"))

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_identity_is_made_once_and_kept_private(self):
        cert, key, token, fingerprint = self.identity
        self.assertEqual(ensure_identity(self.dir.name), self.identity)
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
        self.assertEqual(len(token), 32)
        with open(cert) as f:
            self.assertEqual(fingerprint, hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest())

    def run_robot(self, hello, then=()):
        """Dial the bridge as the robot does, pinning its certificate; the
        lines the bridge sent back, and the bridge."""
        cert, key, token, fingerprint = self.identity
        link = WifiLink("127.0.0.1", 0, cert, key, token, fingerprint)
        bridge = Bridge(Config(), device="/nonexistent", lva_url="", wifi=link, wifi_details=None)

        async def scenario():
            server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            server_ctx.load_cert_chain(cert, key)
            server = await asyncio.start_server(bridge.wifi_client, "127.0.0.1", 0, ssl=server_ctx)
            port = server.sockets[0].getsockname()[1]
            robot_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            robot_ctx.check_hostname = False
            robot_ctx.verify_mode = ssl.CERT_NONE  # pinned below instead, as on the robot
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=robot_ctx)
            peer = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
            self.assertEqual(hashlib.sha256(peer).hexdigest(), fingerprint)
            writer.write(json.dumps({"hello": hello}).encode() + b"\n")
            for line in then:
                writer.write(json.dumps(line).encode() + b"\n")
            await writer.drain()
            got = []
            try:
                while True:
                    raw = await asyncio.wait_for(reader.readline(), 0.5)
                    if not raw:
                        break
                    got.append(json.loads(raw))
            except asyncio.TimeoutError:
                pass
            connected = bridge.wifi_writer is not None
            # The bridge never sends the Wi-Fi details over Wi-Fi.
            await bridge.send_device({"wifi": WIFI})
            try:
                while True:
                    raw = await asyncio.wait_for(reader.readline(), 0.3)
                    if not raw:
                        break
                    got.append(json.loads(raw))
            except asyncio.TimeoutError:
                pass
            writer.close()
            server.close()
            await server.wait_closed()
            return got, connected

        return asyncio.run(scenario())

    def test_the_right_token_gets_the_whole_state(self):
        token = self.identity[2]
        got, connected = self.run_robot({"fw": "1.2.0", "proto": 1, "link": "wifi", "wifi": "x", "token": token},
                                        then=[{"touch": "stroke", "zone": "back"}])
        self.assertTrue(connected)
        lines = {k: v for line in got for k, v in line.items()}
        self.assertIn("config", lines)
        self.assertIn("timers", lines)
        self.assertEqual(lines.get("gesture"), "nod")  # the stroke, over Wi-Fi
        self.assertNotIn("wifi", lines)

    def test_a_wrong_token_is_dropped(self):
        got, connected = self.run_robot({"fw": "1.2.0", "proto": 1, "link": "wifi", "token": "0" * 32})
        self.assertEqual(got, [])
        self.assertFalse(connected)

    def test_no_token_is_dropped(self):
        got, connected = self.run_robot({"fw": "1.2.0", "proto": 1, "link": "wifi"})
        self.assertEqual(got, [])
        self.assertFalse(connected)


class Ear(unittest.TestCase):
    """The robot records a sentence for voice-id, only with a router."""

    def brain(self, ear=True, router=True):
        b = Brain(Config(), now=NOON)
        b.on_device({"hello": {"fw": "1.3.0", "proto": 1, "ear": ear}}, NOON)
        b.router = router
        return b

    def ears(self, actions):
        return [a[1] for a in actions if a[0] == "ear"]

    def test_from_wake_word_to_transcript(self):
        b = self.brain()
        wake = b.on_lva("wake_word_detected", {}, NOON)
        self.assertIn({"record": True}, device(wake))
        self.assertEqual(self.ears(wake), ["start"])
        heard = b.on_lva("stt_text", {"text": "What's the weather?"}, NOON + 3)
        self.assertIn({"record": False}, device(heard))
        self.assertEqual(self.ears(heard), ["What's the weather?"])

    def test_nothing_said_is_dropped(self):
        b = self.brain()
        b.on_lva("wake_word_detected", {}, NOON)
        self.assertEqual(self.ears(b.on_lva("idle", {}, NOON + 5)), ["drop"])
        b.on_lva("wake_word_detected", {}, NOON + 10)
        self.assertEqual(self.ears(b.on_lva("pipeline_error", {}, NOON + 11)), ["drop"])

    def test_no_ear_or_no_router_no_recording(self):
        for b in (self.brain(ear=False), self.brain(router=False)):
            out = b.on_lva("wake_word_detected", {}, NOON)
            self.assertNotIn({"record": True}, device(out))
            self.assertEqual(self.ears(out), [])

    def test_the_router_hears_about_the_ear(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"hello": {"fw": "1.3.0", "proto": 1, "ear": True}}, NOON)
        self.assertIn(("router", {"state": b.router_state(NOON)}), out)
        self.assertTrue(b.router_state(NOON)["ear"])


class EarBridge(unittest.TestCase):
    """The bridge keeps a sentence's audio only until it hands it over."""

    class Router:
        def __init__(self):
            self.sent = []

        async def send(self, text):
            self.sent.append(json.loads(text))

    def run_lines(self, lines, then):
        bridge = Bridge(Config(), device="/nonexistent", lva_url="")
        bridge.router_ws = self.Router()

        async def go():
            await bridge.ear("start")
            for line in lines:
                await bridge.on_line(json.dumps(line).encode())
            await bridge.ear(then)

        asyncio.run(go())
        return bridge

    def test_a_sentence_goes_to_the_router_and_is_dropped(self):
        second = base64.b64encode(b"\x01\x00" * 16000).decode()  # one second
        bridge = self.run_lines([{"audio": second}, {"audio": "not base64!"}], "Good morning")
        sent = bridge.router_ws.sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["voice_sample"]["text"], "Good morning")
        self.assertEqual(len(base64.b64decode(sent[0]["voice_sample"]["pcm"])), 32000)
        self.assertEqual(bridge.ear_audio, bytearray())  # nothing kept
        self.assertFalse(bridge.ear_on)

    def test_too_short_or_dropped_sends_nothing(self):
        short = base64.b64encode(b"\x01\x00" * 4000).decode()  # a quarter second
        self.assertEqual(self.run_lines([{"audio": short}], "Hi").router_ws.sent, [])
        second = base64.b64encode(b"\x01\x00" * 16000).decode()
        self.assertEqual(self.run_lines([{"audio": second}], "drop").router_ws.sent, [])

    def test_audio_outside_a_recording_is_ignored(self):
        bridge = Bridge(Config(), device="/nonexistent", lva_url="")
        asyncio.run(bridge.on_line(json.dumps({"audio": "AQA="}).encode()))
        self.assertEqual(bridge.ear_audio, bytearray())


class Phones(unittest.TestCase):
    """The robot as a room sensor: keys over USB, sightings to the router."""

    PHONES = phones_payload("alex=A1B2C3D4-E5F6-0718-293A-4B5C6D7E8F90,bad=xyz",
                            "sam ec0234a357c8ad05341010a60a397d9b  # Sam's iPhone\nsam nothex\n")

    def test_payload(self):
        p = self.PHONES
        self.assertEqual(p["beacons"], {"a1b2c3d4e5f60718293a4b5c6d7e8f90": "alex"})
        self.assertEqual(p["irks"], {"sam": ["ec0234a357c8ad05341010a60a397d9b"]})
        self.assertEqual(len(p["id"]), 12)
        self.assertIsNone(phones_payload("", ""))
        self.assertNotEqual(phones_payload("alex=a1b2c3d4e5f60718293a4b5c6d7e8f91", "")["id"], p["id"])

    def lines(self, actions, key):
        return [line[key] for line in device(actions) if key in line]

    def test_given_over_usb_when_they_differ(self):
        b = Brain(Config(), now=NOON, phones=self.PHONES)
        hello = {"fw": "1.4.0", "proto": 1, "link": "usb", "phones": ""}
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [self.PHONES])
        hello["phones"] = self.PHONES["id"]
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [])
        hello.update(link="wifi", phones="")
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [])  # never over Wi-Fi

    def test_forgotten_when_there_are_none(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"hello": {"fw": "1.4.0", "proto": 1, "link": "usb", "phones": "abc"}}, NOON)
        self.assertEqual(self.lines(out, "phones"), [None])
        out = b.on_device({"hello": {"fw": "1.3.0", "proto": 1, "link": "usb"}}, NOON)  # older firmware
        self.assertEqual(self.lines(out, "phones"), [])

    def test_sightings_go_to_the_router_cleaned(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"seen": [{"person": "alex", "rssi": -70}, {"person": "x" * 41, "rssi": -60},
                                    {"person": "sam", "rssi": "loud"}, "junk"]}, NOON)
        self.assertEqual([a[1] for a in out if a[0] == "router"],
                         [{"seen": [{"person": "alex", "rssi": -70, "via": "ble"}]}])


class RobotInHomeAssistant(unittest.TestCase):
    """What the router needs to show the robot, and the commands it sends."""

    def test_the_state_has_the_robot(self):
        b = Brain(Config(), now=NOON)
        b.on_device({"hello": {"fw": "1.5.0", "proto": 1, "link": "wifi"}}, NOON)
        b.on_device({"battery": {"level": 87, "mv": 4100, "charging": True, "low": False}}, NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        state = b.router_state(NOON)
        self.assertEqual({k: state[k] for k in ("battery", "charging", "link", "mood", "fw", "brightness")},
                         {"battery": 87, "charging": True, "link": "wifi", "mood": "listening",
                          "fw": "1.5.0", "brightness": 180})

    def test_touches_are_events(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"touch": "stroke", "zone": "back"}, NOON)
        self.assertIn(("router", {"event": {"touch": "stroke", "zone": "back"}}), out)
        out = b.on_device({"touch": "tap", "zone": "elsewhere"}, NOON)
        self.assertIn(("router", {"event": {"touch": "tap", "zone": None}}), out)

    def test_commands_from_home_assistant(self):
        b = Brain(Config(), now=NOON)
        lines = device(b.on_router({"act": {"caption": "  Dinner is ready  ", "brightness": 90,
                                            "led": {"rgb": [0, 0, 255]}}}, NOON))
        self.assertIn({"caption": {"text": "Dinner is ready", "who": "status", "ms": 15000}}, lines)
        self.assertIn({"config": {"brightness": 90}}, lines)
        self.assertIn({"led": {"rgb": [0, 0, 255]}}, lines)
        self.assertEqual(b.config.brightness, 90)
        self.assertEqual(device(b.on_router({"act": {"led": None}}, NOON)), [{"led": None}])

    def test_bad_commands_are_dropped(self):
        b = Brain(Config(), now=NOON)
        for act in ({"brightness": 300}, {"brightness": "dim"}, {"led": {"rgb": [0, 0]}},
                    {"led": {"rgb": [0, 0, 999]}}, {"caption": "   "}, {"caption": 5}):
            self.assertEqual(device(b.on_router({"act": act}, NOON)), [], act)

    def test_the_router_hears_each_change_once(self):
        class Router:
            def __init__(self):
                self.sent = []

            async def send(self, text):
                self.sent.append(json.loads(text))

        bridge = Bridge(Config(), device="/nonexistent", lva_url="")
        bridge.router_ws = Router()
        battery = {"battery": {"level": 50, "mv": 3900, "charging": False, "low": False}}
        asyncio.run(bridge.act(bridge.brain.on_device(battery, NOON)))
        asyncio.run(bridge.act(bridge.brain.on_device(battery, NOON)))  # nothing new
        states = [m["state"] for m in bridge.router_ws.sent if "state" in m]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["battery"], 50)


class Senses(unittest.TestCase):
    """The proximity and light sensor: a wave hushes, the rest is for Home Assistant."""

    def test_a_wave_stops_a_ringing_timer_first(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("timer_ringing", {"id": "t1", "name": "Tea", "total_seconds": 60, "seconds_left": 0}, NOON)
        out = b.on_device({"wave": True}, NOON)
        self.assertEqual(lva(out), [{"command": "stop_timer_ringing"}])
        self.assertIn(("router", {"event": {"wave": True}}), out)

    def test_a_wave_stops_the_reply(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        self.assertEqual(lva(b.on_device({"wave": True}, NOON)), [{"command": "stop_pipeline"}])
        idle = Brain(Config(), now=NOON)
        self.assertEqual(lva(idle.on_device({"wave": True}, NOON)), [])  # nothing to stop

    def test_light_and_hand_go_to_the_router(self):
        b = Brain(Config(), now=NOON)
        b.on_device({"senses": {"lux": 37, "prox": 1883, "near": True}}, NOON)
        state = b.router_state(NOON)
        self.assertEqual((state["lux"], state["near"]), (37, True))
        b.on_device({"senses": {"lux": -4, "near": "yes"}}, NOON)  # nonsense: kept as it was
        self.assertEqual((b.router_state(NOON)["lux"], b.router_state(NOON)["near"]), (37, True))

    def test_auto_brightness_goes_to_the_robot(self):
        on = merged(Brain(Config(), now=NOON).full_state(NOON))["config"]
        off = merged(Brain(Config(auto_brightness=False), now=NOON).full_state(NOON))["config"]
        self.assertEqual((on["auto_brightness"], off["auto_brightness"]), (True, False))


class Tags(unittest.TestCase):
    def test_a_tap_goes_to_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertIn(("router", {"event": {"nfc": "0435249AD11290"}}), b.on_device({"nfc": "0435249AD11290"}, NOON))
        for bad in ("0435249ad11290", "XYZ", "1234", 5):
            self.assertEqual([a for a in b.on_device({"nfc": bad}, NOON) if a[0] == "router"], [], bad)

    def test_a_phone_tap_goes_to_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertIn(("router", {"event": {"tapped": True}}), b.on_device({"tapped": True}, NOON))
        self.assertEqual([a for a in b.on_device({"tapped": 1}, NOON) if a[0] == "router"], [])

    def test_the_link_goes_to_the_robot_and_back_after_a_restart(self):
        b = Brain(Config(), now=NOON)
        url = "https://who.example.org/tap/kitchen?n=abc"
        self.assertEqual(b.on_router({"tap": {"url": url}}, NOON), [("device", {"tap": {"url": url}})])
        hello = {"hello": {"fw": "1.8.0", "proto": 1, "link": "usb", "nfc": True}}
        self.assertIn(("device", {"tap": {"url": url}}), b.on_device(hello, NOON))
        self.assertEqual(b.on_router({"tap": None}, NOON), [("device", {"tap": {"url": ""}})])
        self.assertNotIn("tap", merged(b.on_device(hello, NOON)))

    def test_only_https_links_fit_for_a_tag(self):
        b = Brain(Config(), now=NOON)
        for bad in ({"url": "http://who.example.org/"}, {"url": "https://" + "x" * 120}, {"url": 5}, "x"):
            self.assertEqual(b.on_router({"tap": bad}, NOON), [], bad)

    def test_a_card_window(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(b.on_router({"nfc_read": 30}, NOON), [("device", {"nfc_read": 30})])
        for bad in (-1, 121, "30", True):
            self.assertEqual(b.on_router({"nfc_read": bad}, NOON), [], bad)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
