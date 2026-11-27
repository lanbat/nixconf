"""The Radarr/Sonarr bootstrap (pkgs/servarr-bootstrap) against a mock that behaves
like the real apps: remote path mappings are stored with a trailing slash, and a
second mapping for the same remote path is a 500 ("RemotePath already configured").

Run with: nix build .#checks.x86_64-linux.servarr-bootstrap
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

BOOTSTRAP = sys.argv.pop(1)


def slash(path):
    return path if path.endswith("/") else path + "/"


class Servarr(BaseHTTPRequestHandler):
    mappings = []
    posts = []

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/v3/system/status":
            self.reply(200, {"version": "mock"})
        elif path == "/api/v3/remotepathmapping":
            self.reply(200, self.mappings)
        elif path == "/api/v3/downloadclient":
            self.reply(200, [{"implementation": "QBittorrent"}])
        elif path == "/api/v3/rootfolder":
            self.reply(200, [])
        elif path == "/api/v3/qualityprofile":
            self.reply(200, [{"id": 1}])
        else:
            self.reply(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        path = self.path.split("?")[0]
        self.posts.append((path, body))
        if path != "/api/v3/remotepathmapping":
            self.reply(200, {})
            return
        if any(m["host"] == body["host"] and m["remotePath"] == slash(body["remotePath"]) for m in self.mappings):
            self.reply(500, {"message": "RemotePath already configured."})
            return
        self.mappings.append({"id": len(self.mappings) + 1, "host": body["host"],
                              "remotePath": slash(body["remotePath"]), "localPath": slash(body["localPath"])})
        self.reply(201, {})


class Bootstrap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Servarr)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = os.path.join(cls.tmp.name, "config.xml")
        with open(cls.config, "w") as f:
            f.write("<Config><ApiKey>test-key</ApiKey></Config>")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        Servarr.mappings = []
        Servarr.posts = []

    def run_bootstrap(self):
        env = dict(os.environ, APP="radarr", CONFIG_XML=self.config,
                   SERVARR_URL=f"http://127.0.0.1:{self.server.server_port}")
        return subprocess.run([BOOTSTRAP], env=env, capture_output=True, text=True, timeout=60)

    def mapping_posts(self):
        return [p for p in Servarr.posts if p[0] == "/api/v3/remotepathmapping"]

    def test_a_fresh_app_gets_both_mappings(self):
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.mapping_posts()), 2)

    def test_a_second_run_adds_nothing_and_succeeds(self):
        self.assertEqual(self.run_bootstrap().returncode, 0)
        Servarr.posts = []
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.mapping_posts(), [])

    def test_mappings_the_app_stored_with_a_trailing_slash_are_recognised(self):
        # What a real Radarr 6 returned on 2026-11-27: the paths end in "/".
        Servarr.mappings = [
            {"id": 1, "host": "127.0.0.1", "remotePath": "/media/a/", "localPath": "/srv/storage/a/media/"},
            {"id": 2, "host": "127.0.0.1", "remotePath": "/media/b/", "localPath": "/srv/storage/b/media/"},
        ]
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.mapping_posts(), [])


if __name__ == "__main__":
    unittest.main()
