"""The results page the Echo Show streams (services/dashcast.nix).

The mock, made live: the page polls the router's own loopback API for the page
a room has open and draws five rows, then posts taps back. It is served by the
router itself so the page and the API share an origin, and dashcast's browser
(host networking) reaches both on the loopback.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LANBAT results</title>
<style>
  :root {
    --bg: #0f0d24; --panel: #1c1a3a; --panel-2: #23204a;
    --ink: #f6f4ff; --muted: #a9a4cc; --amber: #f2a516; --line: rgba(242,165,22,.18);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden; }
  body {
    width: 1280px; height: 800px;
    background:
      radial-gradient(900px 500px at 78% -10%, #2b2857 0%, transparent 60%),
      radial-gradient(700px 500px at 10% 110%, #1c1a3a 0%, transparent 55%),
      var(--bg);
    color: var(--ink);
    font-family: "Inter", "Noto Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    display: flex; flex-direction: column; padding: 30px 34px 24px;
  }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 18px; }
  h1 { font-size: 30px; font-weight: 650; letter-spacing: -.02em; margin: 0; white-space: nowrap;
       overflow: hidden; text-overflow: ellipsis; max-width: 900px; }
  h1 .q { color: var(--amber); }
  .page { margin-left: auto; font-size: 17px; color: var(--muted); white-space: nowrap; }
  .page b { color: var(--ink); font-weight: 600; }

  .list { display: flex; flex-direction: column; gap: 10px; flex: 1; min-height: 0; }
  .empty { margin: auto; text-align: center; color: var(--muted); font-size: 20px; line-height: 1.6; }
  .empty .bat { width: 190px; height: 107px; margin: 0 auto 18px; display: block; }
  .row {
    display: flex; align-items: stretch; gap: 18px; text-align: left; width: 100%;
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid rgba(255,255,255,.045); border-radius: 16px;
    padding: 10px 16px 10px 10px; height: 118px; color: inherit; font: inherit;
    cursor: pointer; appearance: none;
  }
  .row.first { border-color: var(--line); box-shadow: 0 0 0 1px rgba(242,165,22,.10), 0 12px 30px rgba(0,0,0,.28); }
  .thumb { position: relative; width: 174px; height: 98px; border-radius: 11px; overflow: hidden;
           flex: none; background: #14122e; }
  .thumb .pic { position: absolute; inset: 0; }
  .thumb .pic img, .thumb .pic svg { width: 100%; height: 100%; display: block; object-fit: cover; }
  .n { position: absolute; left: 8px; top: 8px; width: 28px; height: 28px; border-radius: 50%;
       background: var(--amber); color: #201a05; font-size: 15px; font-weight: 750;
       line-height: 28px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,.35); }
  .body { min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 7px; flex: 1; }
  .title { font-size: 21px; font-weight: 600; letter-spacing: -.01em; line-height: 1.18;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .meta { display: flex; align-items: center; gap: 10px; font-size: 14px; color: var(--muted);
          overflow: hidden; white-space: nowrap; }
  .chip { font-size: 12px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase;
          padding: 3px 8px; border-radius: 999px; color: #cfe4ff; background: #23345c; }
  .chip.movie { color: #ffd9a6; background: #4a3415; }
  .chip.music { color: #d8c8ff; background: #35295c; }
  .chip.playlist { color: #bff0da; background: #1d3f35; }
  .desc { font-size: 14.5px; color: #b9b3d9; line-height: 1.32;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

  footer { display: flex; align-items: center; gap: 18px; padding-top: 16px; }
  .hint { color: var(--muted); font-size: 15.5px; }
  .hint b { color: var(--ink); font-weight: 600; }
  .dots { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .dot { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center;
         font-size: 14.5px; color: var(--muted); background: rgba(255,255,255,.05);
         border: 0; cursor: pointer; }
  .dot.on { background: var(--amber); color: #201a05; font-weight: 700; }
  .dot.arrow { width: 42px; }
  .more { font-size: 13px; color: #8f89b5; margin-left: 6px; }
</style>
</head>
<body>
<header>
  <h1 id="title">Results</h1>
  <div class="page" id="page"></div>
</header>
<main class="list" id="list">
  <div class="empty" id="empty">
    <svg class="bat" viewBox="0 0 640 360" xmlns="http://www.w3.org/2000/svg">
      <defs><radialGradient id="g0" cx="50%" cy="40%" r="70%">
        <stop offset="0" stop-color="#2b2857"/><stop offset=".65" stop-color="#1c1a3a"/><stop offset="1" stop-color="#141230"/>
      </radialGradient></defs>
      <rect width="640" height="360" rx="18" fill="url(#g0)"/>
      <g transform="translate(320 186) scale(9.4) translate(-16 -16)">
        <path fill="#f2a516" d="M16 10 17.6 5.8 19.2 10.2C22.5 8.6 26.5 9.4 30 13Q27.6 13.2 26.4 16.6Q24.2 15 22.4 17.4Q21 16.6 19.6 21.4L16 26.5 12.4 21.4Q11 16.6 9.6 17.4Q7.8 15 5.6 16.6Q4.4 13.2 2 13C5.5 9.4 9.5 8.6 12.8 10.2L14.4 5.8Z"/>
      </g>
    </svg>
    Say “find the matrix” or “look for …”
  </div>
</main>
<footer>
  <div class="hint" id="hint">Say a number to play &nbsp;·&nbsp; <b>“go to page 3”</b> &nbsp;·&nbsp; <b>“next page”</b> to browse</div>
  <div class="dots" id="dots"></div>
</footer>
<script>
const BAT = `<svg class="bat" viewBox="0 0 640 360" xmlns="http://www.w3.org/2000/svg">
  <defs><radialGradient id="g${Math.random().toString(36).slice(2)}" cx="50%" cy="40%" r="70%">
    <stop offset="0" stop-color="#2b2857"/><stop offset=".65" stop-color="#1c1a3a"/><stop offset="1" stop-color="#141230"/>
  </radialGradient></defs>
  <rect width="640" height="360" fill="#1c1a3a"/>
  <g transform="translate(320 186) scale(9.4) translate(-16 -16)">
    <path fill="#f2a516" d="M16 10 17.6 5.8 19.2 10.2C22.5 8.6 26.5 9.4 30 13Q27.6 13.2 26.4 16.6Q24.2 15 22.4 17.4Q21 16.6 19.6 21.4L16 26.5 12.4 21.4Q11 16.6 9.6 17.4Q7.8 15 5.6 16.6Q4.4 13.2 2 13C5.5 9.4 9.5 8.6 12.8 10.2L14.4 5.8Z"/>
  </g>
</svg>`;

const CHIP = { movie: "movie", series: "series", episode: "episode",
               radio: "music", track: "music", album: "music", artist: "music",
               podcast: "music", playlist: "playlist" };

function label(item) {
  if (item.source === "jellyfin") return item.kind === "movie" ? "Jellyfin · Film" : `Jellyfin · ${item.kind}`;
  if (item.source === "music-assistant") return item.kind;
  return item.kind === "playlist" ? "Playlist" : "YouTube";
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

async function act(body) {
  await fetch("/media/action", {method: "POST", headers: {"Content-Type": "application/json"},
                                body: JSON.stringify(body)});
  refresh();
}

function draw(data) {
  const list = document.getElementById("list");
  const title = document.getElementById("title");
  const page = document.getElementById("page");
  document.getElementById("dots").innerHTML = "";
  if (!data.open || !data.items || !data.items.length) {
    title.innerHTML = "Results";
    page.textContent = "";
    list.innerHTML = `<div class="empty" id="empty">${BAT}Say “find the matrix” or “look for …”</div>`;
    return;
  }
  title.innerHTML = `Results for <span class="q">“${escapeHtml(data.query || "")}”</span>`;
  page.innerHTML = `Page <b>${data.page}</b>`;
  list.innerHTML = "";
  data.items.forEach((item, i) => {
    const row = document.createElement("button");
    row.className = "row" + (i === 0 ? " first" : "");
    row.onclick = () => act({action: "play", n: item.n});
    const thumb = el("div", "thumb");
    // The picture is its own layer: a failed one becomes the bat without
    // taking the number with it.
    const pic = el("div", "pic");
    const img = document.createElement("img");
    img.src = "/media/thumb?u=" + encodeURIComponent(item.thumb || "");
    img.onerror = () => { pic.innerHTML = BAT; };
    pic.appendChild(img);
    thumb.appendChild(pic);
    thumb.appendChild(el("span", "n", item.n));
    const body = el("div", "body");
    body.appendChild(el("div", "title", item.title || ""));
    const meta = el("div", "meta");
    const chip = el("span", "chip " + (CHIP[item.kind] || ""), label(item));
    meta.appendChild(chip);
    if (item.channel) meta.appendChild(el("span", "", item.channel));
    if (item.duration) meta.appendChild(el("span", "", "· " + item.duration));
    if (item.facts && item.facts.length) meta.appendChild(el("span", "", "· " + item.facts.join(" · ")));
    body.appendChild(meta);
    if (item.description) body.appendChild(el("div", "desc", item.description));
    row.appendChild(thumb); row.appendChild(body);
    list.appendChild(row);
  });
  const dots = document.getElementById("dots");
  const prev = el("button", "dot arrow", "‹");
  prev.onclick = () => act({action: "prev"});
  dots.appendChild(prev);
  for (let p = 1; p <= 6; p++) {
    const d = el("button", "dot" + (p === data.page ? " on" : ""), String(p));
    d.onclick = () => act({action: "page", n: p});
    dots.appendChild(d);
  }
  const next = el("button", "dot arrow", "›");
  next.onclick = () => act({action: "next"});
  dots.appendChild(next);
  if (data.more && !data.has_next) dots.appendChild(el("span", "more", "more results: ask for something narrower"));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

let lastKey = null;

function fingerprint(data) {
  return JSON.stringify([data.open, data.query, data.page, data.has_next, data.more,
                         (data.items || []).map(i => [i.id, i.title])]);
}

async function refresh() {
  try {
    const r = await fetch("/media/results", {cache: "no-store"});
    const data = await r.json();
    // The poll runs every second; redraw only when the page really changed,
    // or the numbers and pictures flash on every tick.
    const key = fingerprint(data);
    if (key === lastKey) return;
    lastKey = key;
    draw(data);
  } catch (e) { /* the router restarted; the next poll will do */ }
}
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""
