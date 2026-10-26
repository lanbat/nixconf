"""The screen the Echo Show streams when it is not showing results.

One page for the whole house and for a person: it draws the room's clock, who
is around, and the next things on the calendar, in the same LANBAT look as the
results page (services/dashcast.nix).  The router serves it and the state it
polls, on the loopback, from what the screens' bridges and the router's own
people already know.
"""

import hashlib

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LANBAT</title>
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
    display: flex; flex-direction: column; padding: 30px 38px 24px;
  }
  header { display: flex; align-items: center; gap: 16px; }
  .mark { width: 46px; height: 46px; flex: none; }
  .room { font-size: 21px; font-weight: 650; letter-spacing: .16em; text-transform: uppercase;
          color: var(--amber); white-space: nowrap; }
  .date { margin-left: auto; font-size: 24px; color: var(--muted); white-space: nowrap; }

  main { flex: 1; display: flex; gap: 32px; min-height: 0; margin-top: 20px; }
  .clock { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center;
           padding-bottom: 20px; }
  .time { font-size: 172px; font-weight: 600; letter-spacing: -.045em; line-height: .92;
          font-variant-numeric: tabular-nums; }
  .greet { margin-top: 22px; font-size: 33px; color: var(--muted); white-space: nowrap;
           overflow: hidden; text-overflow: ellipsis; }
  .greet b { color: var(--ink); font-weight: 600; }
  .hint { margin-top: 16px; font-size: 19px; color: #8f89b5; }
  .hint b { color: var(--muted); font-weight: 600; }

  .side { width: 476px; display: flex; flex-direction: column; gap: 14px; min-height: 0; }
  .card { background: linear-gradient(180deg, var(--panel-2), var(--panel));
          border: 1px solid rgba(255,255,255,.045); border-radius: 20px;
          padding: 16px 22px 14px; min-height: 0; overflow: hidden; }
  .card h2 { margin: 0 0 8px; font-size: 14.5px; font-weight: 650; letter-spacing: .14em;
             text-transform: uppercase; color: var(--amber); }
  .person { display: flex; align-items: center; gap: 12px; padding: 6px 0; font-size: 22px;
            min-width: 0; }
  .person .pip { width: 9px; height: 9px; border-radius: 50%; background: var(--amber); flex: none; }
  .person.away .pip { background: #4d4870; }
  .person .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .person .where { margin-left: auto; font-size: 15.5px; color: var(--muted);
                   background: rgba(255,255,255,.06); border-radius: 999px; padding: 3px 11px;
                   white-space: nowrap; flex: none; }
  .event { display: flex; align-items: baseline; gap: 14px; padding: 6px 0; min-width: 0; }
  .event .when { flex: none; min-width: 82px; font-size: 17.5px; font-weight: 600;
                 color: var(--amber); font-variant-numeric: tabular-nums; }
  .event .what { font-size: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .event.allday .when { color: var(--muted); }
  .none { color: #8f89b5; font-size: 18.5px; padding: 4px 0 6px; }
  .more { color: #8f89b5; font-size: 15px; padding-top: 4px; }
  #reminders-card[hidden] { display: none; }
</style>
</head>
<body>
<header>
  <svg class="mark" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path fill="#f2a516" d="M16 10 17.6 5.8 19.2 10.2C22.5 8.6 26.5 9.4 30 13Q27.6 13.2 26.4 16.6Q24.2 15 22.4 17.4Q21 16.6 19.6 21.4L16 26.5 12.4 21.4Q11 16.6 9.6 17.4Q7.8 15 5.6 16.6Q4.4 13.2 2 13C5.5 9.4 9.5 8.6 12.8 10.2L14.4 5.8Z"/>
  </svg>
  <div class="room" id="room"></div>
  <div class="date" id="date"></div>
</header>
<main>
  <section class="clock">
    <div class="time" id="time">--:--</div>
    <div class="greet" id="greet"></div>
    <div class="hint">Say <b>“find the matrix”</b> to look something up &nbsp;·&nbsp; <b>“play …”</b> to play it</div>
  </section>
  <aside class="side">
    <section class="card">
      <h2>Around the house</h2>
      <div id="people"></div>
    </section>
    <section class="card">
      <h2>Next up</h2>
      <div id="events"></div>
    </section>
    <section class="card" id="reminders-card" hidden>
      <h2>Reminders</h2>
      <div id="reminders"></div>
    </section>
  </aside>
</main>
<script>
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];
const PEOPLE_MAX = 6, EVENTS_MAX = 4, REMINDERS_MAX = 2;

function pad(n) { return String(n).padStart(2, "0"); }

function tick() {
  const now = new Date();
  document.getElementById("time").textContent = pad(now.getHours()) + ":" + pad(now.getMinutes());
  document.getElementById("date").textContent =
    DAYS[now.getDay()] + " " + now.getDate() + " " + MONTHS[now.getMonth()];
}

function greeting(name) {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, <b>${escapeHtml(name)}</b>` : `${part}, <b>welcome home</b>`;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function timeOf(iso, allDay) {
  if (allDay) return "All day";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
}

function dayOf(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  const days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate())
                         - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
  if (days === 0) return "";
  if (days === 1) return "Tomorrow";
  return DAYS[d.getDay()].slice(0, 3);
}

function draw(data) {
  document.getElementById("room").textContent = data.room || "Home";
  document.getElementById("greet").innerHTML = greeting(data.name);

  const people = document.getElementById("people");
  people.innerHTML = "";
  const around = (data.glance || []).filter(p => p.where && p.where !== "away");
  (around.length ? around : []).slice(0, PEOPLE_MAX).forEach(p => {
    const row = el("div", "person" + (p.where === "Home" ? " away" : ""));
    row.appendChild(el("span", "pip"));
    row.appendChild(el("span", "name", p.name));
    row.appendChild(el("span", "where", p.where));
    people.appendChild(row);
  });
  if (!around.length) people.appendChild(el("div", "none", "Nobody about just now"));

  const events = document.getElementById("events");
  events.innerHTML = "";
  (data.events || []).slice(0, EVENTS_MAX).forEach(e => {
    const row = el("div", "event" + (e.all_day ? " allday" : ""));
    const day = e.all_day ? "" : dayOf(e.start);
    row.appendChild(el("span", "when", (day ? day + " " : "") + timeOf(e.start, e.all_day)));
    row.appendChild(el("span", "what", e.title));
    events.appendChild(row);
  });
  if (!(data.events || []).length) events.appendChild(el("div", "none", "Nothing on the calendar"));
  else if (data.events.length > EVENTS_MAX)
    events.appendChild(el("div", "more", `and ${data.events.length - EVENTS_MAX} more`));

  const card = document.getElementById("reminders-card");
  const reminders = document.getElementById("reminders");
  const due = data.reminders || [];
  card.hidden = !due.length;
  reminders.innerHTML = "";
  due.slice(0, REMINDERS_MAX).forEach(r => {
    const row = el("div", "event");
    const day = r.due ? dayOf(r.due) : "";
    row.appendChild(el("span", "when", (day ? day + " " : "") + (r.due ? timeOf(r.due, false) : "")));
    row.appendChild(el("span", "what", r.title));
    reminders.appendChild(row);
  });
}

let lastKey = null;
let version = null;

async function checkVersion() {
  try {
    const r = await fetch("/screen/version", {cache: "no-store"});
    const v = (await r.text()).trim();
    if (!version) version = v;
    else if (v && v !== version) location.reload();
  } catch (e) { /* the router is restarting */ }
}

function fingerprint(data) {
  return JSON.stringify([data.room, data.name, data.present, data.glance,
                         data.events, data.reminders]);
}

async function refresh() {
  try {
    const r = await fetch("/screen/state", {cache: "no-store"});
    const data = await r.json();
    const key = fingerprint(data);
    if (key === lastKey) return;
    lastKey = key;
    draw(data);
  } catch (e) { /* the router restarted; the next poll will do */ }
}

tick();
draw({room: "", name: null, present: false, glance: [], events: [], reminders: []});
refresh();
checkVersion();
setInterval(tick, 1000);
setInterval(refresh, 2000);
setInterval(checkVersion, 15000);
</script>
</body>
</html>
"""


# A short id for the page as this build has it, so a page already open on a
# screen can notice a deployment and reload itself.
VERSION = hashlib.sha256(PAGE.encode()).hexdigest()[:12]
