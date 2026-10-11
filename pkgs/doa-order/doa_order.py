#!/usr/bin/env python3
"""Find the microphone channel order for Stack-chan sound direction.

The bridge works out the voice's direction from the satellite's four
microphones, but only if it knows where each *channel* is:
lanbat.stackchan.soundMicGeometry is four "x,y" points (y forward, x to the
robot's right), one per channel in order. This records the four channels from
the microphone node, you clap three times (straight ahead, to the robot's
right, to its left), and it prints which channel is which corner — a string
ready to paste as soundMicGeometry.

Only which corner each channel is matters for the angle (the estimate is
scale-invariant), so the spacing it prints is a 1 cm placeholder.
"""
import argparse
import math
import os
import struct
import subprocess
import sys

RATE = 48000
CHANNELS = 4

# The four corners, label -> (x, y) in metres: y forward, x to the robot's right.
CORNERS = {
    "front-right": (0.01, 0.01),
    "front-left": (-0.01, 0.01),
    "back-right": (0.01, -0.01),
    "back-left": (-0.01, -0.01),
}


def parse_geometry(geometry):
    """Four "x,y" points in metres (y forward, x to the right), or None."""
    points = []
    for part in str(geometry).split(";"):
        xy = part.split(",")
        if len(xy) != 2:
            return None
        try:
            points.append((float(xy[0]), float(xy[1])))
        except ValueError:
            return None
    return points if len(points) == 4 else None


def quadrant(x, y):
    """Which corner a point is in: front/back by y, right/left by x."""
    return ("front" if y >= 0 else "back") + "-" + ("right" if x >= 0 else "left")


def record(node, rate, channels, duration):
    """`duration` seconds of the node's `channels` as s16, one list of floats
    per channel. None when pw-record or the node is unavailable."""
    cmd = [
        os.environ.get("PW_RECORD", "pw-record"),
        "--rate", str(rate), "--channels", str(channels), "--format", "s16",
        "-P", f"{{ node.name = lva-stackchan-doa-order target.object = {node} }}",
        "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("pw-record not found: run this from the package (nix run) or set PW_RECORD",
              file=sys.stderr)
        return None
    total = int(duration * rate) * channels * 2
    raw = b""
    try:
        while len(raw) < total:
            chunk = proc.stdout.read(min(65536, total - len(raw)))
            if not chunk:
                break
            raw += chunk
    finally:
        proc.terminate()
        proc.wait()
    n = len(raw) // (2 * channels)
    if n == 0:
        print(f"no audio from {node!r}: is the microphone node up? (pw-cli ls Node)",
              file=sys.stderr)
        return None
    values = struct.unpack(f"<{n * channels}h", raw[: n * 2 * channels])
    frames = [[] for _ in range(channels)]
    for frame in range(n):
        for ch in range(channels):
            frames[ch].append(values[frame * channels + ch] / 32768.0)
    return frames


def find_claps(frames, rate, min_gap_s, frac=0.2):
    """Global sample indices of the loud onsets (claps), in time order. The
    threshold is a fraction of the loudest sample, so it rides the mic's gain."""
    count = len(frames)
    env = [max(abs(frames[ch][i]) for ch in range(count)) for i in range(len(frames[0]))]
    peak = max(env)
    if peak <= 0:
        return []
    threshold = frac * peak
    min_gap = int(min_gap_s * rate)
    onsets = []
    i = 0
    while i < len(env):
        if env[i] >= threshold:
            onsets.append(i)
            i += min_gap
        else:
            i += 1
    return onsets


def channel_peak_lags(frames, onset, rate, window_s):
    """For each channel, ms from the first channel's peak to its own peak,
    searching around the onset. Lower means nearer the sound."""
    window = int(window_s * rate)
    peaks = []
    for ch in frames:
        lo = max(0, onset - window)
        hi = min(len(ch), onset + window)
        best = max(range(hi - lo), key=lambda k: abs(ch[lo + k]))
        peaks.append(lo + best)
    first = min(peaks)
    return [(p - first) / rate * 1000.0 for p in peaks]


def order_from_claps(front, right, left):
    """The four channels' corners, in channel order, from per-channel peak
    lags (lower = nearer) for a clap ahead, to the right, and to the left. The
    two nearest to the ahead clap are the front row, the two nearest to the
    right clap the right column; a corner is where a row and a column meet. A
    channel the claps don't pin down is None."""
    def nearest_two(lags):
        return {i for i, _ in sorted(enumerate(lags), key=lambda p: p[1])[:2]}
    front_set = nearest_two(front)
    right_set = nearest_two(right)
    left_set = nearest_two(left)
    order = []
    for ch in range(len(front)):
        is_front = ch in front_set
        if is_front and ch in right_set:
            order.append("front-right")
        elif is_front and ch in left_set:
            order.append("front-left")
        elif (not is_front) and ch in right_set:
            order.append("back-right")
        elif (not is_front) and ch in left_set:
            order.append("back-left")
        else:
            order.append(None)
    return order


def suggested_geometry(order):
    """A soundMicGeometry string (point i = channel i's corner), or None when
    the order is incomplete. The 1 cm spacing is a placeholder."""
    if any(corner is None for corner in order):
        return None
    return ";".join(f"{x},{y}" for x, y in (CORNERS[c] for c in order))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default=os.environ.get("SOUND_SOURCE", "lanbat_ps_eye_capture"),
                    help="the PipeWire node with the four microphones")
    ap.add_argument("--duration", type=float, default=10.0, help="seconds to record (default 10)")
    ap.add_argument("--claps", type=int, default=3,
                    help="claps to look for (default 3: ahead, right, left)")
    ap.add_argument("--rate", type=int, default=RATE)
    ap.add_argument("--geometry", default=None,
                    help="the current soundMicGeometry, to check against")
    args = ap.parse_args(argv)

    print(f"listening on {args.node} for {args.duration:.0f} s…")
    print(f"clap {args.claps} times, slowly and clearly: straight ahead, then to the "
          "robot's right, then to its left.")
    frames = record(args.node, args.rate, CHANNELS, args.duration)
    if frames is None:
        return 1

    onsets = find_claps(frames, args.rate, min_gap_s=0.4)
    if len(onsets) < args.claps:
        print(f"heard {len(onsets)} clap(s), needed {args.claps} — try again, louder and slower")
        return 1
    labels = ["straight ahead", "to the robot's right", "to the robot's left"][: args.claps]
    lags = []
    for number, (label, onset) in enumerate(zip(labels, onsets[: args.claps]), start=1):
        lags.append(channel_peak_lags(frames, onset, args.rate, window_s=0.03))
        print(f"\nclap {number} ({label}) — ms from the nearest mic to each channel's peak:")
        for ch, ms in enumerate(lags[-1]):
            print(f"  ch{ch}  {ms:6.2f}")

    order = order_from_claps(*lags)
    print("\nchannel -> corner:")
    for ch, corner in enumerate(order):
        print(f"  ch{ch}  {corner or '?'}")

    geom = suggested_geometry(order)
    if geom:
        print(f'\nsoundMicGeometry = "{geom}"')
        if args.geometry:
            current = parse_geometry(args.geometry)
            if current is None:
                print("could not parse --geometry")
            else:
                mismatches = [ch for ch in range(4)
                              if order[ch] is not None and quadrant(*current[ch]) != order[ch]]
                if mismatches:
                    print(f"note: this differs from the current setting on channel(s) "
                          f"{', '.join('ch' + str(ch) for ch in mismatches)}")
                else:
                    print("this matches the current setting")
    else:
        print("\nincomplete — read the tables above and set soundMicGeometry by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
