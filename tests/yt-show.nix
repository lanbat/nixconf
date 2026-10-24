# tests/yt-show.nix
#
# yt-show (pkgs/yt-show) against a stand-in Invidious and a stand-in yt-dlp, so
# it runs offline: a search comes back five to a page, numbered from one, with
# a later page sliced out of Invidious' twenty; a video resolves to the 720p
# MP4, not the 1080p one the screen cannot pair; Invidious being down falls
# back to yt-dlp for both; and bad input is refused.
#
# Run with: nix build .#checks.x86_64-linux.yt-show
{ pkgs }:

let
  ytShow = pkgs.callPackage ../pkgs/yt-show { };

  # A stand-in Invidious: twenty videos a page, one muxed 720p stream and one
  # 1080p stream the Show must not be given, and a second page that is empty.
  invidious = pkgs.writeText "fake-invidious.py" ''
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    VIDEOS = [
        {"type": "video", "videoId": "vid%08d" % i, "title": "Title %d" % i,
         "author": "A channel", "lengthSeconds": 60 + i}
        for i in range(20)
    ]

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
                return self.json(VIDEOS if page == 1 else [])
            if url.path.startswith("/api/v1/videos/"):
                vid = url.path.rsplit("/", 1)[-1]
                return self.json({"title": "A title " + vid, "formatStreams": [
                    {"itag": "137", "url": "https://cdn.example/1080"},
                    {"itag": "22", "url": "https://cdn.example/720"},
                ]})
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

  # JSON helpers for the curl calls.
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
    trap 'kill $show_pid $invidious_pid $fallback_pid' EXIT

    for _ in $(seq 50); do
      curl -fs -o /dev/null http://127.0.0.1:18202/healthz && break
      sleep 0.2
    done
    for _ in $(seq 50); do
      curl -fs -o /dev/null http://127.0.0.1:18203/healthz && break
      sleep 0.2
    done

    ${post "p1.json" 18202 "/search" ''{"query":"kittens"}''}
    ${post "p4.json" 18202 "/search" ''{"query":"kittens","page":4}''}
    ${post "p5.json" 18202 "/search" ''{"query":"kittens","page":5}''}
    ${post "play.json" 18202 "/resolve" ''{"video_id":"vid00000003"}''}
    ${post "down.json" 18203 "/search" ''{"query":"kittens"}''}
    ${post "downplay.json" 18203 "/resolve" ''{"video_id":"vid00000003"}''}

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
    PY

    # Bad input is refused, not guessed.
    status() { curl -s -o /dev/null -w '%{http_code}' -X POST \
      -H 'Content-Type: application/json' -d "$2" "http://127.0.0.1:18202$1"; }
    test "$(status /search '{}')" = 400
    test "$(status /search '{"query":""}')" = 400
    test "$(status /search '{"query":"x","page":0}')" = 400
    test "$(status /resolve '{"video_id":"not a video"}')" = 400
    test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18202/healthz)" = 200

    touch $out
  ''
