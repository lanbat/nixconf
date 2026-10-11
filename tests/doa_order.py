#!/usr/bin/env python3
"""Unit tests for the doa-order channel-order discovery tool.

The analysis (find_claps, channel_peak_lags, order_from_claps, the geometry
helpers) is pure, so it is tested against synthetic claps that reuse the same
per-mic shift convention as the bridge's tests: a channel's trace is the
reference shifted by (x*ux + y*uy)/speed at the sample rate, with the source
angle positive to the left.
"""
import math
import sys
import unittest

sys.path.insert(0, sys.argv.pop(1))
import doa_order as doa  # noqa: E402

RATE = 48000
SPEED = 343.0
# Channel order (the default geometry): ch0 back-left, ch1 back-right,
# ch2 front-right, ch3 front-left.
POINTS = [(-0.01, -0.01), (0.01, -0.01), (0.01, 0.01), (-0.01, 0.01)]
EXPECTED = ["back-left", "back-right", "front-right", "front-left"]
GEOM = "-0.01,-0.01;0.01,-0.01;0.01,0.01;-0.01,0.01"
N = 24000


def _ref_wave(fs):
    # A clap: 30 ms of a decaying 2 kHz tone, indexed from its start.
    return [math.exp(-k / 300.0) * math.sin(2 * math.pi * 2000 * k / fs) for k in range(1440)]


def burst(theta_deg, at, n=N, fs=RATE, points=POINTS, speed=SPEED, ref=None):
    """A short transient arriving from theta_deg (+ left) at sample `at`."""
    ref = ref or _ref_wave(fs)
    m = len(ref)
    ux, uy = -math.sin(math.radians(theta_deg)), math.cos(math.radians(theta_deg))
    out = []
    for x, y in points:
        shift = int(round((x * ux + y * uy) / speed * fs))
        ch = [0.0] * n
        for i in range(n):
            k = i - at + shift
            if 0 <= k < m:
                ch[i] = ref[k]
        out.append(ch)
    return out


def three_claps(n=N):
    """One recording with claps ahead, to the right, and to the left."""
    ref = _ref_wave(RATE)
    frames = [[0.0] * n for _ in range(4)]
    for theta, at in ((0, 1000), (-90, 12000), (90, 23000)):
        b = burst(theta, at, n=n, ref=ref)
        for ch in range(4):
            for i in range(n):
                frames[ch][i] += b[ch][i]
    return frames


class Geometry(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(doa.parse_geometry(GEOM), POINTS)
        self.assertIsNone(doa.parse_geometry("0,0;0,0"))
        self.assertIsNone(doa.parse_geometry("a,b;c,d;0,0;0,0"))

    def test_quadrant(self):
        self.assertEqual(doa.quadrant(0.01, 0.01), "front-right")
        self.assertEqual(doa.quadrant(-0.01, 0.01), "front-left")
        self.assertEqual(doa.quadrant(0.01, -0.01), "back-right")
        self.assertEqual(doa.quadrant(-0.01, -0.01), "back-left")

    def test_suggested_geometry_roundtrip(self):
        self.assertEqual(doa.suggested_geometry(EXPECTED), GEOM)
        self.assertIsNone(doa.suggested_geometry(["front-right", None, "front-left", "back-left"]))


class OrderFromClaps(unittest.TestCase):
    def test_clean(self):
        front = [2.0, 2.0, 0.0, 0.0]  # ch2, ch3 nearest the ahead clap
        right = [2.0, 0.0, 0.0, 2.0]  # ch1, ch2 nearest the right clap
        left = [0.0, 2.0, 2.0, 0.0]   # ch0, ch3 nearest the left clap
        self.assertEqual(doa.order_from_claps(front, right, left), EXPECTED)

    def test_unpinned_channel_is_none(self):
        front = [0.0, 1.0, 1.0, 2.0]
        right = [1.0, 0.0, 1.0, 2.0]
        left = [1.0, 1.0, 0.0, 2.0]   # ch3 is never nearest
        self.assertIsNone(doa.order_from_claps(front, right, left)[3])


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = three_claps()

    def test_finds_three_claps(self):
        onsets = doa.find_claps(self.frames, RATE, min_gap_s=0.2)
        self.assertEqual(len(onsets), 3)
        self.assertEqual(onsets, sorted(onsets))

    def test_recovers_channel_order(self):
        onsets = doa.find_claps(self.frames, RATE, min_gap_s=0.2)
        lags = [doa.channel_peak_lags(self.frames, o, RATE, window_s=0.03) for o in onsets]
        order = doa.order_from_claps(*lags)
        self.assertEqual(order, EXPECTED)
        self.assertEqual(doa.suggested_geometry(order), GEOM)


if __name__ == "__main__":
    unittest.main()
