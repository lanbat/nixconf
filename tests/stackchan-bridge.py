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
            # Whole words, and the happy idioms that use them.
            "I can't wait to hear it!": "happy",
            "We couldn't be happier.": "happy",
            "The terror of the deep is a great film.": "happy",  # "error" inside a word
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

    def test_a_tap_on_the_screen_is_not_a_tap_on_the_head(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"touch": "tap", "zone": "screen"}, NOON)
        self.assertEqual(lva(out), [])
        self.assertEqual(merged(out)["mood"], "happy")

    def test_a_happy_moment_passes(self):
        # A stroke makes it happy for a moment, not for good (its idle
        # looks, the wander and the look round, wait for a neutral face).
        b = Brain(Config(), now=NOON)
        self.assertEqual(merged(b.on_device({"touch": "stroke", "zone": "back"}, NOON))["mood"], "happy")
        self.assertNotIn("mood", merged(b.tick(NOON + 5)))
        self.assertEqual(merged(b.tick(NOON + 11))["mood"], "neutral")
        self.assertEqual(merged(b.on_device({"hello": {"fw": "1.11.0", "proto": 1}}, NOON + 12))["mood"], "neutral")

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

    def test_by_day_the_next_wake_word_or_a_tap_wakes_it_for_good(self):
        b = Brain(Config(night_start="23:00", night_end="07:00", touch_to_talk=False), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        b.on_router({"act": {"sleep": True}}, NOON + 1)
        b.on_lva("idle", {}, NOON + 3)
        self.assertEqual(merged(b.on_lva("wake_word_detected", {}, NOON + 100))["sleep"], False)
        self.assertNotIn("sleep", merged(b.on_lva("idle", {}, NOON + 110)))  # awake now, no "wake up" needed
        b.on_router({"act": {"sleep": True}}, NOON + 200)
        self.assertEqual(merged(b.on_device({"touch": "tap", "zone": "middle"}, NOON + 300))["sleep"], False)
        # At night a turn still wakes it for that turn only.
        b2 = Brain(Config(night_start="23:00", night_end="07:00"), now=MIDNIGHT)
        b2.on_lva("wake_word_detected", {}, MIDNIGHT)
        self.assertEqual(merged(b2.on_lva("idle", {}, MIDNIGHT + 10))["sleep"], True)

    def test_a_wake_word_and_then_silence_is_not_left_hanging(self):
        b = Brain(Config(), now=NOON)
        b.on_lva("wake_word_detected", {}, NOON)
        out = device(b.on_lva("idle", {}, NOON + 8))
        self.assertIn({"caption": None}, out)  # "Listening…" goes
        self.assertIn({"gesture": "tilt"}, out)  # a little "hm?"

    def test_a_look_round_when_asked_but_not_asleep_or_talking(self):
        b = Brain(Config(night_start="23:00", night_end="07:00"), now=NOON)
        self.assertIn({"scan": True}, device(b.on_router({"act": {"scan": True}}, NOON)))
        b.on_lva("wake_word_detected", {}, NOON + 1)
        self.assertNotIn({"scan": True}, device(b.on_router({"act": {"scan": True}}, NOON + 2)))
        night = Brain(Config(night_start="23:00", night_end="07:00"), now=MIDNIGHT)
        self.assertNotIn({"scan": True}, device(night.on_router({"act": {"scan": True}}, MIDNIGHT)))

    def test_with_captions_off_the_greeting_has_none_either(self):
        b = Brain(Config(captions=False), now=NOON)
        out = device(b.on_router({"act": {"greet": {"name": "Kiril"}}}, NOON))
        self.assertIn({"greet": True}, out)
        self.assertFalse([d for d in out if "caption" in d])

    def test_wake_up_at_night(self):
        b = Brain(Config(night_start="23:00", night_end="07:00"), now=MIDNIGHT)
        b.on_lva("wake_word_detected", {}, MIDNIGHT)
        b.on_router({"act": {"sleep": False}}, MIDNIGHT + 1)
        self.assertNotIn("sleep", merged(b.on_lva("idle", {}, MIDNIGHT + 3)))
        self.assertFalse(b.asleep)

    def test_presence_is_reported_on_change(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.router(b.on_device({"face": "new"}, NOON)),
                         [{"state": {"present": True, "present_since_s": 0, "asleep": False, "battery_low": False, "ear": False}},
                          {"event": {"face": "new"}}])
        self.assertEqual(self.router(b.on_device({"face": "new"}, NOON + 1)), [])
        self.assertEqual(self.router(b.on_device({"face": "lost"}, NOON + 90)),
                         [{"state": {"present": False, "present_since_s": 0, "asleep": False, "battery_low": False, "ear": False}},
                          {"event": {"face": "lost"}}])

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


SQUARE = "-0.01,-0.01;0.01,-0.01;0.01,0.01;-0.01,0.01"


class Direction(unittest.TestCase):
    def test_a_wave_at_a_known_angle_is_found(self):
        for theta in (0, 30, -30, 60, -60, 90):
            with self.subTest(theta=theta):
                est = estimate_direction(wave(theta, geom=SQUARE), DOA_RATE, SQUARE)
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

    def test_mics_in_a_line_give_left_and_right(self):
        # The PlayStation Eye's line: no front or back, the sign and angle are right.
        for theta in (0, 30, -30, 60, -60, 80, -80):
            with self.subTest(theta=theta):
                angle, confidence = estimate_direction(wave(theta), DOA_RATE, DEFAULT_SOUND_GEOMETRY)
                self.assertLess(abs(angle - theta), 10.0)
                self.assertGreater(confidence, 0.25)

    def test_a_voice_behind_a_line_reads_as_its_mirror_in_front(self):
        angle, _ = estimate_direction(wave(150), DOA_RATE, DEFAULT_SOUND_GEOMETRY)
        self.assertLess(abs(angle - 30), 10.0)

    def test_the_line_the_other_way_round_turns_the_other_way(self):
        reversed_line = ";".join(reversed(DEFAULT_SOUND_GEOMETRY.split(";")))
        angle, _ = estimate_direction(wave(40), DOA_RATE, reversed_line)
        self.assertLess(abs(angle + 40), 10.0)

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
        self.assertEqual([a[1]["yaw"] for a in out if a[0] == "device" and "yaw" in a[1]], [150])

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

    def run_robot(self, hello, then=(), phones=None):
        """Dial the bridge as the robot does, pinning its certificate; the
        lines the bridge sent back, and the bridge."""
        cert, key, token, fingerprint = self.identity
        link = WifiLink("127.0.0.1", 0, cert, key, token, fingerprint)
        bridge = Bridge(Config(), device="/nonexistent", lva_url="", wifi=link, wifi_details=None, phones=phones)

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

    def test_the_phones_go_over_wifi_to_firmware_that_takes_them(self):
        token = self.identity[2]
        phones = phones_payload("", "sam ec0234a357c8ad05341010a60a397d9b\n")
        got, _ = self.run_robot({"fw": "1.9.7", "proto": 1, "link": "wifi", "phones": "", "token": token},
                                phones=phones)
        self.assertIn({"phones": phones}, got)
        got, _ = self.run_robot({"fw": "1.9.6", "proto": 1, "link": "wifi", "phones": "", "token": token},
                                phones=phones)
        self.assertNotIn("phones", [k for line in got for k in line])

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

    def test_not_over_wifi(self):
        b = self.brain()
        b.link = "wifi"
        wake = b.on_lva("wake_word_detected", {}, NOON)
        self.assertNotIn({"record": True}, device(wake))
        self.assertEqual(self.ears(wake), [])

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


class DirectionListener(unittest.TestCase):
    """The four mics are recorded only while the robot is on this Pi's USB."""

    def test_recording_follows_the_usb_link(self):
        import stackchan_bridge as sb
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            spawns = os.path.join(tmp, "spawns")
            fake = os.path.join(tmp, "pw-record")
            with open(fake, "w") as f:
                # Python, not sh and cat: the Nix build sandbox has neither on PATH.
                f.write(f"#!{sys.executable}\nimport sys\nopen({spawns!r}, 'a').write('x\\n')\n"
                        "while True:\n    sys.stdout.buffer.write(bytes(4096))\n")
            os.chmod(fake, 0o755)
            old = (os.environ.get("PW_RECORD"), sb.DOA_USB_POLL_S)
            os.environ["PW_RECORD"], sb.DOA_USB_POLL_S = fake, 0.02
            bridge = Bridge(Config(), device="/nonexistent", lva_url="")

            def count():
                return len(open(spawns).read().splitlines()) if os.path.exists(spawns) else 0

            async def go():
                task = asyncio.create_task(bridge.doa_loop())
                await asyncio.sleep(0.3)
                on_wifi = count()  # the robot isn't on USB: nothing recorded
                bridge.serial_writer = object()
                for _ in range(150):
                    if count() and bridge.mic_window is not None:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.2)
                on_usb, window = count(), bridge.mic_window is not None
                bridge.serial_writer = None
                for _ in range(150):  # stopping the recorder can take a moment in a sandbox
                    if bridge.mic_window is None:
                        break
                    await asyncio.sleep(0.02)
                gone = bridge.mic_window is None
                task.cancel()
                return on_wifi, on_usb, window, gone

            try:
                on_wifi, on_usb, window, gone = asyncio.run(go())
            finally:
                if old[0] is None:
                    os.environ.pop("PW_RECORD", None)
                else:
                    os.environ["PW_RECORD"] = old[0]
                sb.DOA_USB_POLL_S = old[1]
            self.assertEqual(on_wifi, 0)
            self.assertEqual(on_usb, 1)
            self.assertTrue(window)
            self.assertTrue(gone)


class MoreForHomeAssistant(unittest.TestCase):
    def test_the_conversation_phase_and_the_wake_word(self):
        b = Brain(Config(), now=NOON)
        out = b.on_lva("wake_word_detected", {}, NOON)
        self.assertIn(("router", {"event": {"wake": True}}), out)
        self.assertEqual(b.router_state(NOON)["phase"], "listening")
        for event, phase in (("thinking", "thinking"), ("tts_speaking", "speaking"),
                             ("tts_finished", "speaking"), ("idle", "idle")):
            b.on_lva(event, {}, NOON)
            self.assertEqual(b.router_state(NOON)["phase"], phase, event)

    def test_health_is_kept_when_in_range(self):
        b = Brain(Config(), now=NOON)
        b.on_device({"health": {"rssi": -61, "heap_kb": 87, "psram_kb": 5000, "nfc": "tag", "yaw": -12}}, NOON)
        s = b.router_state(NOON)
        self.assertEqual((s["rssi"], s["heap_kb"], s["psram_kb"], s["nfc_mode"], s["head_yaw"]),
                         (-61, 87, 5000, "tag", -12))
        b.on_device({"health": {"rssi": 5, "heap_kb": "x", "nfc": "card", "yaw": 400}}, NOON)
        s = b.router_state(NOON)
        self.assertEqual((s["rssi"], s["heap_kb"], s["nfc_mode"], s["head_yaw"]), (None, None, None, None))

    def test_the_voice_direction_is_kept_even_without_a_turn(self):
        b = Brain(Config(), now=NOON)
        b.on_sound(5.4, 0.9, NOON)  # within the turn threshold: no turn
        self.assertEqual(b.router_state(NOON)["voice_dir"], 5)

    def test_new_controls(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(b.on_router({"act": {"yaw": -30}}, NOON), [("device", {"yaw": -300})])
        self.assertEqual(b.on_router({"act": {"yaw": 99}}, NOON), [])
        self.assertEqual(b.on_router({"act": {"nap": True}}, NOON), [("device", {"nap": True})])
        self.assertEqual(b.on_router({"act": {"listen": "start"}}, NOON), [("lva", {"command": "start_listening"})])
        self.assertEqual(b.on_router({"act": {"listen": "stop"}}, NOON), [("lva", {"command": "stop_pipeline"})])
        self.assertEqual(b.on_router({"act": {"listen": "rm -rf"}}, NOON), [])

    def test_servo_faults_go_to_the_router(self):
        b = Brain(Config(), now=NOON)
        self.assertIn(("router", {"event": {"servo_fault": 2}}), b.on_device({"servo_fault": 2}, NOON))

    def test_pi_undervoltage_from_hwmon(self):
        import stackchan_bridge as sb
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(sb.pi_undervolt(root))
            os.makedirs(os.path.join(root, "hwmon1"))
            with open(os.path.join(root, "hwmon1", "name"), "w") as f:
                f.write("rpi_volt\n")
            with open(os.path.join(root, "hwmon1", "in0_lcrit_alarm"), "w") as f:
                f.write("1\n")
            self.assertTrue(sb.pi_undervolt(root))

    def test_the_last_problem_is_kept_short(self):
        import stackchan_bridge as sb
        sb.log("robot write failed: " + "x" * 200)
        self.assertEqual(len(sb.last_error()), 80)
        self.assertTrue(sb.last_error().startswith("robot write failed"))


class Mirror(unittest.TestCase):
    def test_home_assistant_flips_the_voice_direction_and_it_is_kept(self):
        import stackchan_bridge as sb
        import tempfile
        b = Brain(Config(sound_turn_min_deg=1), now=NOON)
        self.assertEqual(b.on_sound(40, 0.9, NOON)[0], ("device", {"yaw": 400}))
        out = b.on_router({"act": {"mirror": True}}, NOON)
        self.assertIn(("save", {"sound_mirror": True}), out)
        self.assertTrue(b.router_state(NOON)["sound_mirror"])
        self.assertEqual(b.on_sound(40, 0.9, NOON)[0], ("device", {"yaw": -400}))
        self.assertEqual(b.on_router({"act": {"mirror": True}}, NOON), [])  # no change, nothing saved
        with tempfile.TemporaryDirectory() as state:
            old = os.environ.get("STATE_DIRECTORY")
            os.environ["STATE_DIRECTORY"] = state
            try:
                sb.save_settings({"sound_mirror": True})
                self.assertEqual(sb.load_settings(), {"sound_mirror": True})
            finally:
                if old is None:
                    os.environ.pop("STATE_DIRECTORY")
                else:
                    os.environ["STATE_DIRECTORY"] = old


class TurnLearning(unittest.TestCase):
    """The turn to a voice is learned from where the robot then sees the person."""

    def samples(self, gain=-2.3, offset=8.0, raws=(-25, -15, -8, 0, 6, 14, 22, 28), noise=(1, -2, 0, 2, -1, 1, -2, 1)):
        return [(v, gain * v + offset + n) for v, n in zip(raws, noise)]

    def test_a_mirrored_short_turning_array_is_recovered(self):
        import stackchan_bridge as sb
        fit = sb.fit_turn(self.samples())
        self.assertIsNotNone(fit)
        self.assertLess(abs(fit["gain"] + 2.3) / 2.3, 0.05)
        self.assertLess(abs(fit["offset"] - 8.0), 2.0)

    def test_outliers_are_dropped(self):
        import stackchan_bridge as sb
        pts = self.samples() + [(10, -60.0)]  # someone else talking
        fit = sb.fit_turn(pts)
        self.assertLess(abs(fit["gain"] + 2.3) / 2.3, 0.05)
        self.assertEqual(fit["n"], 8)

    def test_not_until_it_is_clear(self):
        import stackchan_bridge as sb
        self.assertIsNone(sb.fit_turn(self.samples()[:5]))  # too few
        self.assertIsNone(sb.fit_turn([(v / 4, t) for v, t in self.samples()]))  # too little spread
        self.assertIsNone(sb.fit_turn([(v, 0.1 * v) for v, _ in self.samples()]))  # gain too small to be real

    def brain(self):
        return Brain(Config(sound_turn_min_deg=1.0), now=NOON)

    def test_samples_come_from_the_face_the_robot_sees_after_a_turn(self):
        b = self.brain()
        for i, (v, t) in enumerate(self.samples()):
            out = b.on_sound(v, 0.9, NOON + i * 10)
            self.assertIn(("device", {"face_at": 2500}), out)
            out = b.on_device({"face_at": {"yaw": int(round(t)), "faces": 1}}, NOON + i * 10 + 2)
        self.assertEqual(b.calibration.status(), "calibrated, mirrored")
        self.assertTrue(any(a == ("save", {"turn_samples": b.calibration.samples}) for a in out))
        # From now on the learned turn is used: a voice at +20 raw is -2.3·20+8 = -38.
        out = b.on_sound(20, 0.9, NOON + 200)
        yaw = [a[1]["yaw"] for a in out if a[0] == "device" and "yaw" in a[1]][0]
        self.assertLess(abs(yaw + 380), 30)
        self.assertEqual(b.router_state(NOON)["turn_cal"], "calibrated, mirrored")

    def test_two_faces_or_one_at_the_limit_teach_nothing(self):
        b = self.brain()
        b.on_sound(20, 0.9, NOON)
        b.on_device({"face_at": {"yaw": 30, "faces": 2}}, NOON + 1)
        b.on_sound(20, 0.9, NOON + 10)
        b.on_device({"face_at": {"yaw": 69, "faces": 1}}, NOON + 11)
        self.assertEqual(b.calibration.samples, [])

    def test_nobody_there_looks_once_the_other_way(self):
        b = self.brain()
        b.on_sound(30, 0.9, NOON)  # manual: +30
        out = b.on_device({"face_at": None}, NOON + 3)
        self.assertIn(("device", {"yaw": -300}), out)
        self.assertIn(("device", {"face_at": 2500}), out)
        b.on_device({"face": "new"}, NOON + 4)  # someone there after all
        out = b.on_device({"face_at": {"yaw": -31, "faces": 1}}, NOON + 5)  # found on the other side
        self.assertEqual(b.calibration.samples, [(30.0, -31.0)])

    def test_nobody_either_way_goes_back_to_the_voice_and_stops_looking_away(self):
        # In the dark the camera sees nobody anywhere: it would turn away from every voice.
        b = self.brain()
        b.on_sound(30, 0.9, NOON)
        b.on_device({"face_at": None}, NOON + 3)  # looks at -30
        out = b.on_device({"face_at": None}, NOON + 6)
        self.assertEqual(device(out), [{"yaw": 300}])  # back to the voice
        b.on_sound(-25, 0.9, NOON + 20)
        self.assertEqual(device(b.on_device({"face_at": None}, NOON + 23)), [])  # stays with the voice
        b.on_device({"face": "new"}, NOON + 30)  # it can see someone again
        b.on_device({"face": "lost"}, NOON + 40)
        b.on_sound(30, 0.9, NOON + 50)
        self.assertIn({"yaw": -300}, device(b.on_device({"face_at": None}, NOON + 53)))  # looks away once more

    def test_the_switch_and_the_reset(self):
        b = self.brain()
        b.calibration = __import__("stackchan_bridge").TurnCalibration(self.samples())
        self.assertIn(("save", {"auto_turn": False}), b.on_router({"act": {"auto_turn": False}}, NOON))
        out = b.on_sound(20, 0.9, NOON)  # manual again: +20
        self.assertIn(("device", {"yaw": 200}), out)
        b.on_router({"act": {"reset_turn": True}}, NOON)
        self.assertEqual((b.calibration.samples, b.calibration.status()), ([], "learning (0/6)"))

    def test_samples_survive_a_restart(self):
        import stackchan_bridge as sb
        kept = sb.TurnCalibration(self.samples()).samples
        again = sb.TurnCalibration([list(s) for s in kept] + [["junk"], [1]])
        self.assertEqual(again.samples, kept)
        self.assertIsNotNone(again.fit)


class Restarts(unittest.TestCase):
    """Why the robot last started reaches the journal and Home Assistant."""

    def test_the_hello_says_why_it_started(self):
        b = Brain(Config(), now=NOON)
        out = b.on_device({"hello": {"fw": "1.9.6", "proto": 1, "link": "usb", "reset": "panic", "up_s": 4}}, NOON)
        self.assertIn(("log", "robot firmware 1.9.6, protocol 1, over usb; started by panic, 4 s ago"), out)
        self.assertEqual(b.router_state(NOON)["restart"], "panic")
        b.on_device({"hello": {"fw": "1.9.4", "proto": 1, "link": "usb"}}, NOON)  # older firmware: unknown
        self.assertIsNone(b.router_state(NOON)["restart"])
        b.on_device({"hello": {"fw": "1.9.6", "proto": 1, "reset": "<script>"}}, NOON)
        self.assertIsNone(b.router_state(NOON)["restart"])

    def test_a_crash_report_is_kept_and_boot_noise_is_not(self):
        bridge = Bridge(Config(), device="/nonexistent", lva_url="")
        logged = []
        import stackchan_bridge
        real = stackchan_bridge.log
        stackchan_bridge.log = logged.append
        try:
            async def go():
                await bridge.on_line(b"ESP-ROM:esp32s3-20210327")
                await bridge.on_line(b"Guru Meditation Error: Core  1 panic'ed (LoadProhibited)")
                for _ in range(40):
                    await bridge.on_line(b"Backtrace: 0x4200abcd:0x3fcebd10")
            asyncio.run(go())
        finally:
            stackchan_bridge.log = real
        self.assertEqual(logged[0], "robot says: Guru Meditation Error: Core  1 panic'ed (LoadProhibited)")
        self.assertEqual(len(logged), 30)  # a few lines a minute at most


class BesideTheMics(unittest.TestCase):
    """A robot unplugged only for its power still turns to the voice."""

    def test_on_usb_always_on_wifi_only_when_told(self):
        quiet = Bridge(Config(), device="/nonexistent", lva_url="")
        self.assertFalse(quiet.beside_mics())
        quiet.serial_writer = object()
        self.assertTrue(quiet.beside_mics())  # plugged in
        away = Bridge(Config(), device="/nonexistent", lva_url="")
        away.wifi_writer = object()
        self.assertFalse(away.beside_mics())  # on Wi-Fi: elsewhere in the room
        beside = Bridge(Config(sound_over_wifi=True), device="/nonexistent", lva_url="")
        self.assertFalse(beside.beside_mics())  # not connected at all
        beside.wifi_writer = object()
        self.assertTrue(beside.beside_mics())
        self.assertTrue(Config.from_env({"SOUND_OVER_WIFI": "1"}).sound_over_wifi)
        self.assertFalse(Config.from_env({}).sound_over_wifi)


class CameraView(unittest.TestCase):
    def test_home_assistant_can_show_what_the_camera_sees(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(device(b.on_router({"act": {"debug": "camera"}}, NOON)), [{"debug": "camera"}])
        self.assertEqual(device(b.on_router({"act": {"debug": "off"}}, NOON)), [{"debug": "off"}])
        self.assertEqual(device(b.on_router({"act": {"debug": "nfc"}}, NOON)), [])


class Greeting(unittest.TestCase):
    """The router recognised someone arriving: the robot greets them."""

    def test_someone_it_knows_is_greeted_and_it_calms_down_after(self):
        b = Brain(Config(), now=NOON)
        out = device(b.on_router({"act": {"greet": {"name": "Kiril"}}}, NOON))
        self.assertEqual(out[:2], [{"mood": "happy"}, {"greet": True}])
        self.assertIn({"caption": {"text": "Hi Kiril!", "who": "status", "ms": 8000}}, out)
        self.assertNotIn({"mood": "neutral"}, device(b.tick(NOON + 3)))
        self.assertIn({"mood": "neutral"}, device(b.tick(NOON + 9)))
        self.assertNotIn({"mood": "neutral"}, device(b.tick(NOON + 12)))  # once

    def test_not_asleep_nor_in_a_turn_nor_without_a_name(self):
        b = Brain(Config(), now=NOON)
        b.asleep = True
        self.assertEqual(device(b.on_router({"act": {"greet": {"name": "Kiril"}}}, NOON)), [])
        c = Brain(Config(), now=NOON)
        c.on_lva("wake_word_detected", {}, NOON)
        self.assertEqual(device(c.on_router({"act": {"greet": {"name": "Kiril"}}}, NOON)), [])
        d = Brain(Config(), now=NOON)
        for bad in ({"name": ""}, {"name": 3}, "Kiril", None):
            self.assertEqual(device(d.on_router({"act": {"greet": bad}}, NOON)), [])


class Glance(unittest.TestCase):
    """It shows the router who is in front of it, without anyone speaking."""

    def brain(self, link="usb"):
        b = Brain(Config(), now=NOON)
        b.probes, b.link = True, link
        return b

    @staticmethod
    def probes(actions):
        return [a for a in actions if a[0] == "probe"]

    def test_a_face_coming_into_view_is_shown_to_the_router(self):
        b = self.brain()
        self.assertEqual(self.probes(b.on_device({"face": "new"}, NOON)), [("probe", "seen")])
        b.on_device({"face": "lost"}, NOON + 2)
        self.assertEqual(self.probes(b.on_device({"face": "new"}, NOON + 4)), [])  # flickering: not yet
        b.on_device({"face": "lost"}, NOON + 6)
        self.assertEqual(self.probes(b.on_device({"face": "new"}, NOON + 11)), [("probe", "seen")])

    def test_someone_staying_is_shown_again_every_minute(self):
        b = self.brain()
        b.on_device({"face": "new"}, NOON)
        self.assertEqual(self.probes(b.tick(NOON + 30)), [])
        self.assertEqual(self.probes(b.tick(NOON + 61)), [("probe", "seen")])
        b.on_device({"face": "lost"}, NOON + 70)
        self.assertEqual(self.probes(b.tick(NOON + 200)), [])  # nobody there

    def test_over_wifi_a_minute_apart_and_never_without_a_library_or_asleep(self):
        b = self.brain("wifi")
        self.assertEqual(self.probes(b.on_device({"face": "new"}, NOON)), [("probe", "seen")])
        b.on_device({"face": "lost"}, NOON + 5)
        self.assertEqual(self.probes(b.on_device({"face": "new"}, NOON + 20)), [])
        self.assertEqual(self.probes(b.tick(NOON + 61)), [("probe", "seen")])
        quiet = Brain(Config(), now=NOON)  # the router has no face library
        self.assertEqual(self.probes(quiet.on_device({"face": "new"}, NOON)), [])
        night = self.brain()
        night.asleep = True
        self.assertEqual(self.probes(night.on_device({"face": "new"}, NOON)), [])

    def test_the_probe_says_why(self):
        bridge = Bridge(Config(), device="/nonexistent", lva_url="")
        bridge.router_ws = EarBridge.Router()
        sent = []

        async def send_device(line):
            sent.append(line)

        bridge.send_device = send_device

        async def go():
            await bridge._act([("probe", "seen")])
            for _ in range(100):  # it settles first, then asks for the frame
                if sent:
                    break
                await asyncio.sleep(0.02)
            await bridge.on_line(json.dumps({"face_image": "AAAA"}).encode())
            await bridge.capture_task

        import stackchan_bridge
        logged, real = [], stackchan_bridge.log
        stackchan_bridge.log = logged.append
        try:
            asyncio.run(go())
        finally:
            stackchan_bridge.log = real
        self.assertEqual(sent, [{"capture": True}])
        self.assertEqual(bridge.router_ws.sent, [{"face_probe": "AAAA", "why": "seen"}])
        self.assertTrue(any(line.startswith("face capture: 0 KB in ") and line.endswith(" ms") for line in logged),
                        logged)

    def test_a_capture_with_no_answer_says_so(self):
        import stackchan_bridge
        bridge = Bridge(Config(), device="/nonexistent", lva_url="")

        async def send_device(line):
            pass

        bridge.send_device = send_device
        logged, real, timeout = [], stackchan_bridge.log, stackchan_bridge.CAPTURE_TIMEOUT_S
        stackchan_bridge.log, stackchan_bridge.CAPTURE_TIMEOUT_S = logged.append, 0.05
        try:
            img = asyncio.run(bridge.face_capture())
        finally:
            stackchan_bridge.log, stackchan_bridge.CAPTURE_TIMEOUT_S = real, timeout
        self.assertIsNone(img)
        self.assertEqual(logged, ["face capture failed: no answer in 0 s"])
        self.assertIsNone(bridge.face_waiter)  # the next capture may go


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
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [])  # not to 1.4 over Wi-Fi
        hello["fw"] = "1.9.7"  # firmware that takes them over its Wi-Fi link too
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [self.PHONES])

    IRK = "00112233445566778899aabbccddeeff"

    def test_the_routers_keys_join_the_profiles(self):
        b = Brain(Config(), now=NOON, phones=self.PHONES)
        hello = {"fw": "1.9.7", "proto": 1, "link": "wifi", "phones": self.PHONES["id"]}
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [])
        sent = self.lines(b.on_router({"irks": {"kiril": [self.IRK.upper()], "sam": ["nothex"], "x" * 41: [self.IRK]}},
                                      NOON), "phones")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["irks"], {"sam": ["ec0234a357c8ad05341010a60a397d9b"], "kiril": [self.IRK]})
        self.assertEqual(sent[0]["beacons"], self.PHONES["beacons"])
        self.assertNotEqual(sent[0]["id"], self.PHONES["id"])
        # The robot says it has them: nothing more; the same keys again: nothing.
        hello["phones"] = sent[0]["id"]
        self.assertEqual(self.lines(b.on_device({"hello": hello}, NOON), "phones"), [])
        self.assertEqual(self.lines(b.on_router({"irks": {"kiril": [self.IRK]}}, NOON), "phones"), [])
        # Forgotten at the router: back to the profile's own.
        self.assertEqual(self.lines(b.on_router({"irks": {}}, NOON), "phones"), [self.PHONES])

    def test_not_when_phones_are_off(self):
        b = Brain(Config(router_phones=False), now=NOON)
        b.on_device({"hello": {"fw": "1.9.7", "proto": 1, "link": "usb", "phones": ""}}, NOON)
        self.assertEqual(self.lines(b.on_router({"irks": {"kiril": [self.IRK]}}, NOON), "phones"), [])

    def test_the_routers_keys_alone_and_no_robot(self):
        b = Brain(Config(), now=NOON)
        self.assertEqual(self.lines(b.on_router({"irks": {"kiril": [self.IRK]}}, NOON), "phones"), [])  # none yet
        out = b.on_device({"hello": {"fw": "1.9.7", "proto": 1, "link": "usb", "phones": ""}}, NOON)
        self.assertEqual([p["irks"] for p in self.lines(out, "phones")], [{"kiril": [self.IRK]}])

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
