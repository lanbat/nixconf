"""The snapclient watchdog (pkgs/snapclient-watchdog): what a bad connection to
snapserver looks like in `ss` output, and when it restarts snapclient, without
a system.

The samples are from a Pi 5 (addresses changed): a hung client (2026-10-21, eight days
after snapserver dropped it) and the same client after a restart.

Run with: nix build .#checks.x86_64-linux.snapclient-watchdog
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, sys.argv.pop(1))

import watchdog  # noqa: E402

HUNG = (
    "ESTAB 691967 0       192.0.2.11:47874 192.0.2.10:1704 users:((\"snapclient\",pid=3068,fd=8))\n"
    "\t cubic wscale:10,10 rto:204 rtt:0.397/0.041 ato:120 mss:1448 pmtu:1500 rcvmss:1448 "
    "advmss:1448 cwnd:10 bytes_sent:1259496 bytes_acked:1259497 bytes_received:1954111 "
    "segs_out:77481 segs_in:41077 data_segs_out:37038 data_segs_in:37939 send 291788413bps "
    "lastsnd:682649172 lastrcv:682635040 lastack:682635388 pacing_rate 583576824bps\n"
)
HEALTHY = (
    "ESTAB 0      0       192.0.2.11:52110 192.0.2.10:1704 users:((\"snapclient\",pid=389574,fd=9))\n"
    "\t cubic wscale:10,10 rto:204 rtt:0.4/0.05 mss:1448 cwnd:10 bytes_sent:1204 "
    "lastsnd:32 lastrcv:20 lastack:20 pacing_rate 583576824bps\n"
)
THIRTY = 30000
BACKLOG = 262144


def verdict(output, pid):
    return watchdog.verdict(watchdog.parse(output), pid, THIRTY, BACKLOG)


class Parse(unittest.TestCase):
    def test_a_connection_with_its_process_and_timers(self):
        (c,) = watchdog.parse(HUNG)
        self.assertEqual((c.recv_q, c.pid, c.last_sent_ms, c.last_received_ms), (691967, 3068, 682649172, 682635040))

    def test_two_connections_keep_their_own_details(self):
        a, b = watchdog.parse(HUNG + HEALTHY)
        self.assertEqual((a.pid, a.last_received_ms), (3068, 682635040))
        self.assertEqual((b.pid, b.last_received_ms), (389574, 20))

    def test_no_process_column_and_no_details(self):
        (c,) = watchdog.parse("ESTAB 0 0 192.0.2.11:1 192.0.2.10:1704\n")
        self.assertEqual((c.pid, c.last_sent_ms, c.last_received_ms), (None, None, None))

    def test_nothing(self):
        self.assertEqual(watchdog.parse(""), [])
        self.assertEqual(watchdog.parse("\n\n"), [])


class Verdict(unittest.TestCase):
    def test_a_working_client_is_fine(self):
        self.assertIsNone(verdict(HEALTHY, 389574))

    def test_the_hung_client_with_its_socket_still_open_is_caught(self):
        why = verdict(HUNG, 3068)
        self.assertIn("no traffic one way for 682649 s", why)

    def test_a_client_that_stopped_reading_is_caught_though_the_server_still_sends(self):
        backlog = HEALTHY.replace("ESTAB 0      0", "ESTAB 900000 0")
        self.assertIn("not reading its connection (900000 bytes", verdict(backlog, 389574))

    def test_a_quiet_server_is_caught_though_the_client_still_sends(self):
        deaf = HEALTHY.replace("lastrcv:20", "lastrcv:45000")
        self.assertIn("no traffic one way for 45 s", verdict(deaf, 389574))

    def test_a_short_pause_is_not_a_hang(self):
        self.assertIsNone(verdict(HEALTHY.replace("lastrcv:20", "lastrcv:5000"), 389574))

    def test_no_connection_of_its_own(self):
        self.assertEqual(verdict("", 389574), "no connection to snapserver")
        self.assertEqual(verdict(HUNG, 389574), "no connection to snapserver")  # another process's

    def test_one_live_connection_is_enough(self):
        self.assertIsNone(verdict(HUNG.replace("pid=3068", "pid=389574") + HEALTHY, 389574))

    def test_no_timers_is_not_a_hang(self):
        # ss without details (-i): only the receive queue can speak.
        self.assertIsNone(verdict("ESTAB 0 0 a:1 b:1704 users:((\"snapclient\",pid=5,fd=8))\n", 5))


class Run(unittest.TestCase):
    """main() restarts only on the second bad check in a row."""

    def run_main(self, state, ss_output, pid="3068"):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            out = pid if cmd[1:3] == ["show", "-p"] else ss_output if cmd[0] == "ss" else ""
            return mock.Mock(stdout=out)

        env = {"STATE": state, "SS": "ss", "SYSTEMCTL": "systemctl"}
        with mock.patch.dict(os.environ, env), mock.patch.object(watchdog.subprocess, "run", fake_run):
            watchdog.main()
        return [c for c in calls if c[:2] == ["systemctl", "restart"]]

    def test_restarts_on_the_second_bad_check_only(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            self.assertEqual(self.run_main(state, HUNG), [])
            self.assertTrue(os.path.exists(state))
            self.assertEqual(self.run_main(state, HUNG), [["systemctl", "restart", "snapclient"]])
            self.assertFalse(os.path.exists(state))

    def test_a_good_check_forgets_the_bad_one(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            self.run_main(state, HUNG)
            self.assertEqual(self.run_main(state, HEALTHY.replace("389574", "3068")), [])
            self.assertFalse(os.path.exists(state))
            self.assertEqual(self.run_main(state, HUNG), [])  # counting from one again

    def test_a_stopped_client_is_systemds_to_restart(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            open(state, "w").close()
            self.assertEqual(self.run_main(state, "", pid="0"), [])
            self.assertFalse(os.path.exists(state))


if __name__ == "__main__":
    unittest.main(verbosity=2)
