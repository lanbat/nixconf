"""The satellite camera client (pkgs/satellite-camera), without a camera or a router.

Run with: nix build .#checks.x86_64-linux.satellite-camera
"""
import asyncio
import base64
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, sys.argv.pop(1))

import satellite_camera as sc  # noqa: E402


class Motion(unittest.TestCase):
    def test_the_mean_change_per_pixel(self):
        self.assertEqual(sc.motion(bytes([10, 10, 10, 10]), bytes([10, 10, 10, 10])), 0.0)
        self.assertEqual(sc.motion(bytes([0, 0, 0, 0]), bytes([8, 8, 0, 0])), 4.0)

    def test_frames_of_another_size_or_none_are_no_movement(self):
        self.assertEqual(sc.motion(b"", bytes([1, 2])), 0.0)
        self.assertEqual(sc.motion(bytes([1]), bytes([1, 2])), 0.0)


class GateRules(unittest.TestCase):
    def gate(self):
        g = sc.Gate(threshold=6.0, gap=20.0, again=60.0)
        g.faces = True
        return g

    def test_nothing_moves_nothing_is_sent(self):
        g = self.gate()
        g.moving(100.0, 1.0)
        self.assertFalse(g.due(100.0))

    def test_movement_sends_a_frame_and_then_waits_the_gap(self):
        g = self.gate()
        g.moving(100.0, 9.0)
        self.assertTrue(g.due(100.0))
        g.sent(100.0)
        g.moving(103.0, 9.0)
        self.assertFalse(g.due(103.0))
        g.moving(121.0, 9.0)
        self.assertTrue(g.due(121.0))

    def test_without_a_face_library_nothing_is_sent(self):
        g = self.gate()
        g.faces = False
        g.moving(100.0, 50.0)
        self.assertFalse(g.due(100.0))

    def test_a_frame_goes_only_while_it_is_moving_now(self):
        g = self.gate()
        g.moving(100.0, 9.0)
        self.assertFalse(g.due(100.0 + 3 * sc.SAMPLE_S))  # it stopped a while ago

    def test_once_someone_is_named_it_waits_longer(self):
        g = self.gate()
        g.moving(100.0, 9.0)
        g.sent(100.0)
        g.answered(True)
        g.moving(125.0, 9.0)
        self.assertFalse(g.due(125.0))
        g.moving(165.0, 9.0)
        self.assertTrue(g.due(165.0))

    def test_unnamed_frames_in_a_row_slow_it_down_until_it_is_still(self):
        g = self.gate()
        for _ in range(sc.UNNAMED_MAX):
            g.answered(False)
        g.sent(100.0)
        g.moving(125.0, 9.0)
        self.assertFalse(g.due(125.0))  # 20 s would have been enough before
        g.moving(165.0, 9.0)
        self.assertTrue(g.due(165.0))
        g.still(165.0 + sc.STILL_S + 1)  # a quiet minute: the next person starts afresh
        g.moving(300.0, 9.0)
        g.sent(240.0)
        self.assertTrue(g.due(300.0))


FAKE_FFMPEG = """#!/bin/sh
# stands in for ffmpeg: a grey frame is 19200 bytes of 7, a jpeg is a few bytes
case "$*" in
  *gray*) head -c 19200 /dev/zero | tr '\\000' '\\007' ;;
  *) printf 'JPEGDATA' ;;
esac
"""


def fake(directory, body=FAKE_FFMPEG):
    path = os.path.join(directory, "ffmpeg")
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class Grabbing(unittest.TestCase):
    def test_a_grey_sample_and_a_jpeg(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = fake(d)
            grey = asyncio.run(sc.grab(ffmpeg, "/dev/video0", sc.SAMPLE_SIZE, grey=True))
            self.assertEqual(len(grey), 160 * 120)
            self.assertEqual(asyncio.run(sc.grab(ffmpeg, "/dev/video0", sc.SEND_SIZE, grey=False)), b"JPEGDATA")

    def test_a_camera_that_fails_gives_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = fake(d, "#!/bin/sh\nexit 1\n")
            self.assertIsNone(asyncio.run(sc.grab(ffmpeg, "/dev/video0", sc.SEND_SIZE, grey=False)))
            self.assertIsNone(asyncio.run(sc.grab("/nonexistent/ffmpeg", "/dev/video0", sc.SEND_SIZE, grey=False)))

    def test_a_short_grey_frame_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = fake(d, "#!/bin/sh\nprintf 'short'\n")
            self.assertIsNone(asyncio.run(sc.grab(ffmpeg, "/dev/video0", sc.SAMPLE_SIZE, grey=True)))


class Socket:
    def __init__(self, incoming=()):
        self.sent = []
        self._incoming = list(incoming)

    async def send(self, text):
        self.sent.append(json.loads(text))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return json.dumps(self._incoming.pop(0))


class Talking(unittest.TestCase):
    def camera(self, directory):
        return sc.Camera(fake(directory), "/dev/video0", sc.Gate(), sample_s=0.0)

    def test_the_router_asking_for_a_face_is_answered_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            camera = self.camera(d)
            ws = Socket([{"body_hello": {"faces": True}}, {"capture": True}])
            asyncio.run(sc.listen(ws, camera))
            self.assertTrue(camera.gate.faces)
            self.assertEqual(ws.sent, [{"face_image": base64.b64encode(b"JPEGDATA").decode()}])

    def test_a_named_answer_is_remembered_by_the_gate(self):
        with tempfile.TemporaryDirectory() as d:
            camera = self.camera(d)
            asyncio.run(sc.listen(Socket([{"face_result": {"person": "Kiril"}}]), camera))
            self.assertTrue(camera.gate._named)
            asyncio.run(sc.listen(Socket([{"face_result": {"person": None}}]), camera))
            self.assertFalse(camera.gate._named)

    def test_movement_sends_a_probe_marked_seen(self):
        with tempfile.TemporaryDirectory() as d:
            # Alternating frames: the first sample is the baseline, the second differs.
            script = ("#!/bin/sh\ncase \"$*\" in\n  *gray*) n=$(cat \"$0.n\" 2>/dev/null || echo 0); "
                      "echo $((n+1)) > \"$0.n\"; v=$((n % 2 * 90)); "
                      "head -c 19200 /dev/zero | tr '\\000' \"\\\\$(printf '%03o' $v)\" ;;\n"
                      "  *) printf 'JPEGDATA' ;;\nesac\n")
            camera = sc.Camera(fake(d, script), "/dev/video0", sc.Gate(), sample_s=0.0)
            camera.gate.faces = True
            ws = Socket()
            clock = iter(range(1000, 100000, 1))

            async def run():
                task = asyncio.create_task(sc.watch(ws, camera, clock=lambda: float(next(clock))))
                for _ in range(200):
                    await asyncio.sleep(0.02)
                    if ws.sent:
                        break
                task.cancel()

            asyncio.run(run())
            self.assertEqual(ws.sent[0], {"face_probe": base64.b64encode(b"JPEGDATA").decode(), "why": "seen"})


if __name__ == "__main__":
    unittest.main()
