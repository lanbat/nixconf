# tests/yt-show.nix
#
# yt-show (pkgs/yt-show) against a stand-in Invidious and a stand-in yt-dlp, so
# it runs offline: a search comes back five to a page, numbered from one, with
# a later page sliced out of Invidious' twenty; a video with the single-file
# 720p stream resolves to it; one with only 360p is remuxed from a stand-in DASH
# pair and served with ranges, while a failed remux and a video past the length
# limit fall back to 360p; Invidious being down falls back to yt-dlp for both;
# and bad input is refused.
#
# Run with: nix build .#checks.x86_64-linux.yt-show
{ pkgs }:

let
  ytShow = pkgs.callPackage ../pkgs/yt-show { };

  # A stand-in Invidious: twenty videos a page, a second page that is empty, and
  # per-video formats: 22 for most, 18 for ids ending 8, 9 or 6 (the remux and
  # its fallbacks), and a long one ending 6.
  invidious = pkgs.writeText "fake-invidious.py" ''
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    VIDEOS = [
        {"type": "video", "videoId": "vid%08d" % i, "title": "Title %d" % i,
         "author": "A channel", "lengthSeconds": 60 + i}
        for i in range(20)
    ]
    PLID = "PLabcdefghijklmnopqrstuvwx"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def json(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
            if url.path == "/api/v1/search":
                page = int(query.get("page", ["1"])[0])
                if page != 1:
                    return self.json([])
                if query.get("q", [""])[0] == "playlist":
                    return self.json([
                        {"type": "playlist", "playlistId": PLID,
                         "title": "A playlist", "author": "C", "videoCount": 120},
                        {"type": "video", "videoId": "vid00000000", "title": "Title 0",
                         "author": "A channel", "lengthSeconds": 60},
                    ])
                return self.json(VIDEOS)
            if url.path.startswith("/api/v1/playlists/"):
                if url.path.rsplit("/", 1)[-1] != PLID:
                    self.send_response(404)
                    self.end_headers()
                    return
                return self.json({"title": "A playlist", "videos": [
                    {"videoId": "vid%08d" % i, "title": "Video %d" % i,
                     "author": "C", "lengthSeconds": 60 + i}
                    for i in range(120)
                ]})
            if url.path.startswith("/api/v1/videos/"):
                vid = url.path.rsplit("/", 1)[-1]
                fields = query.get("fields", [""])[0]
                if "adaptiveFormats" in fields:
                    formats = []
                    if vid.endswith(("8", "9")):
                        formats = [
                            {"itag": "136", "url": "https://cdn.example/720-video"},
                            {"itag": "140", "url": "https://cdn.example/audio"},
                        ]
                    return self.json({"adaptiveFormats": formats})
                streams = [
                    {"itag": "137", "url": "https://cdn.example/1080"},
                    {"itag": "22", "url": "https://cdn.example/720"},
                ]
                if vid.endswith(("6", "8", "9")):
                    streams = [{"itag": "18", "url": "https://cdn.example/360"}]
                seconds = 4000 if vid.endswith("6") else 120
                return self.json({"title": "A title " + vid, "lengthSeconds": seconds,
                                  "formatStreams": streams})
            if url.path == "/latest_version":
                self.send_response(302)
                self.send_header("Location", "/stream")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    HTTPServer(("127.0.0.1", 18201), Handler).serve_forever()
  '';

  # A stand-in yt-dlp: two search entries, a title and one address; a URL with
  # "broken" in it fails, as YouTube does when it wants a sign-in.
  fakeYtDlp = pkgs.writeShellScript "yt-dlp" ''
    mode=""
    for arg in "$@"; do
      case "$arg" in
        --dump-single-json) mode=list ;;
        --print) mode=title ;;
        -g) mode=url ;;
      esac
    done
    url="''${!#}"
    case "$url" in
      *broken*) echo "ERROR: Sign in to confirm you are not a bot" >&2; exit 1 ;;
    esac
    if [ "$mode" = list ]; then
      cat <<'JSON'
    {"entries": [
      {"id": "ytdlp000001", "title": "From yt-dlp one", "channel": "C", "duration": 61},
      {"id": "ytdlp000002", "title": "From yt-dlp two", "channel": "C", "duration": 62}
    ]}
    JSON
    elif [ "$mode" = title ]; then
      echo "A yt-dlp title"
    else
      echo "https://googlevideo.example/videoplayback"
    fi
  '';

  # A stand-in ffmpeg: joins the two streams by writing the file, and fails for
  # vid00000008, the fallback case.
  fakeFfmpeg = pkgs.writeShellScript "ffmpeg" ''
    for arg in "$@"; do
      case "$arg" in
        *vid00000008*) echo "ERROR: stream gone" >&2; exit 1 ;;
      esac
    done
    for last; do :; done
    printf '0123456789' > "$last"
  '';

  # JSON helper for the curl calls.
  post = file: port: path: body: ''
    curl -fs -X POST -H 'Content-Type: application/json' -d '${body}' \
      http://127.0.0.1:${toString port}${path} -o ${file}
  '';
in
pkgs.runCommand "yt-show-check"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.curl
    ];
  }
  ''
    python3 ${invidious} &
    invidious_pid=$!
    ${ytShow}/bin/yt-show serve --port 18202 \
      --invidious http://127.0.0.1:18201 --invidious-public https://youtube.example \
      --ytdlp ${fakeYtDlp} &
    show_pid=$!
    # A second helper whose Invidious is not there: yt-dlp has to answer.
    ${ytShow}/bin/yt-show serve --port 18203 \
      --invidious http://127.0.0.1:19876 --ytdlp ${fakeYtDlp} &
    fallback_pid=$!
    # A third with a remux cache and the media app the screen would fetch from.
    mkdir -p cache
    ${ytShow}/bin/yt-show serve --port 18204 --media-port 18205 \
      --invidious http://127.0.0.1:18201 --invidious-public https://youtube.example \
      --ytdlp ${fakeYtDlp} --ffmpeg ${fakeFfmpeg} --cache-dir "$PWD/cache" \
      --media-url-base http://127.0.0.1:18205 --remux-seconds 20 &
    remux_pid=$!
    trap 'kill $show_pid $invidious_pid $fallback_pid $remux_pid' EXIT

    for port in 18202 18203 18204; do
      for _ in $(seq 50); do
        curl -fs -o /dev/null http://127.0.0.1:$port/healthz && break
        sleep 0.2
      done
    done

    ${post "p1.json" 18202 "/search" ''{"query":"kittens"}''}
    ${post "p4.json" 18202 "/search" ''{"query":"kittens","page":4}''}
    ${post "p5.json" 18202 "/search" ''{"query":"kittens","page":5}''}
    ${post "play.json" 18202 "/resolve" ''{"video_id":"vid00000003"}''}
    ${post "down.json" 18203 "/search" ''{"query":"kittens"}''}
    ${post "downplay.json" 18203 "/resolve" ''{"video_id":"vid00000003"}''}
    ${post "remux.json" 18204 "/resolve" ''{"video_id":"vid00000009"}''}
    ${post "failed.json" 18204 "/resolve" ''{"video_id":"vid00000008"}''}
    ${post "long.json" 18204 "/resolve" ''{"video_id":"vid00000006"}''}
    ${post "mixed.json" 18202 "/search" ''{"query":"playlist"}''}
    ${post "contents.json" 18202 "/contents" ''{"playlist_id":"PLabcdefghijklmnopqrstuvwx"}''}
    ${post "contents2.json" 18202 "/contents" ''{"playlist_id":"PLabcdefghijklmnopqrstuvwx","page":2}''}

    python3 - <<'PY'
    import json

    def load(name):
        with open(name) as f:
            return json.load(f)

    first = load("p1.json")
    assert first["source"] == "invidious", first
    assert first["has_next"] is True, first
    assert [i["n"] for i in first["items"]] == [1, 2, 3, 4, 5], first["items"]
    assert first["items"][0]["title"] == "Title 0", first["items"][0]
    assert first["items"][0]["duration"] == "1:00", first["items"][0]

    # Page 4 is items 16-20 of Invidious' first page of 20.
    fourth = load("p4.json")
    assert [i["title"] for i in fourth["items"]] == ["Title %d" % i for i in range(15, 20)], fourth
    assert [i["n"] for i in fourth["items"]] == [1, 2, 3, 4, 5], fourth

    fifth = load("p5.json")
    assert fifth["items"] == [] and fifth["has_next"] is False, fifth

    played = load("play.json")
    assert played["source"] == "invidious", played
    assert played["url"] == (
        "https://youtube.example/latest_version?id=vid00000003&itag=22&local=true"
    ), played
    assert played["title"] == "A title vid00000003", played

    # Invidious unreachable: the search and the address come from yt-dlp.
    down = load("down.json")
    assert down["source"] == "yt-dlp", down
    assert [i["title"] for i in down["items"]] == ["From yt-dlp one", "From yt-dlp two"], down
    downplay = load("downplay.json")
    assert downplay == {"url": "https://googlevideo.example/videoplayback",
                        "title": "A yt-dlp title", "source": "yt-dlp"}, downplay

    # 360p only, short: a remuxed 720p file, served with ranges.
    remux = load("remux.json")
    assert remux["source"] == "remux", remux
    assert remux["url"] == "http://127.0.0.1:18205/media/vid00000009.mp4", remux
    # 360p only, but the remux fails: the 360p address.
    failed = load("failed.json")
    assert failed["source"] == "invidious" and "itag=18" in failed["url"], failed
    # 360p only, but too long to make the screen wait.
    long = load("long.json")
    assert long["source"] == "invidious" and "itag=18" in long["url"], long

    # Playlists come back alongside videos, and their videos come back a page at a time.
    mixed = load("mixed.json")
    assert [i["kind"] for i in mixed["items"]] == ["playlist", "video"], mixed
    assert mixed["items"][0]["count"] == 120, mixed
    c1 = load("contents.json")
    assert c1["title"] == "A playlist" and c1["has_next"] is True, c1
    assert [i["title"] for i in c1["items"]] == ["Video %d" % i for i in range(5)], c1
    c2 = load("contents2.json")
    assert [i["title"] for i in c2["items"]] == ["Video %d" % i for i in range(5, 10)], c2
    PY

    # The media app serves the remuxed file, and nothing else.
    test "$(curl -fs -r 0-3 http://127.0.0.1:18205/media/vid00000009.mp4)" = "0123"
    test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18205/media/vid00000008.mp4)" = 404
    test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18205/media/../../etc/passwd)" = 404

    # Bad input is refused, not guessed.
    status() { curl -s -o /dev/null -w '%{http_code}' -X POST \
      -H 'Content-Type: application/json' -d "$2" "http://127.0.0.1:18202$1"; }
    test "$(status /search '{}')" = 400
    test "$(status /search '{"query":""}')" = 400
    test "$(status /search '{"query":"x","page":0}')" = 400
    test "$(status /resolve '{"video_id":"not a video"}')" = 400
    test "$(status /contents '{"playlist_id":"not a playlist"}')" = 400
    test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18202/healthz)" = 200

    touch $out
  ''
