/* lanbat/nixconf site: the service playground, the tier simulator, small helpers. */
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  /* ───────────────────────── Playground ─────────────────────────
     Ports and subdomains below are the real claims made by the
     services in this repo, so clashes are reported as the flake would. */
  const CLAIMS = [
    { name: "audiobookshelf", port: 13378, sub: "audiobooks" },
    { name: "authentik", port: 9000, sub: "auth" },
    { name: "bitmagnet", port: 3333, sub: "bitmagnet" },
    { name: "caddy", port: null, sub: "ca" },
    { name: "frigate", port: 5000, sub: "nvr" },
    { name: "grafana", port: 3030, sub: "grafana" },
    { name: "home-assistant", port: 8123, sub: "ha" },
    { name: "homepage", port: 3000, sub: "home" },
    { name: "immich", port: 2283, sub: "photos" },
    { name: "influxdb", port: 8086, sub: null },
    { name: "invidious", port: 8083, sub: "youtube" },
    { name: "jackett", port: 9117, sub: "jackett" },
    { name: "jellyfin", port: 8096, sub: "media" },
    { name: "mosquitto", port: 1883, sub: null },
    { name: "music-assistant", port: 8095, sub: "music" },
    { name: "nextcloud", port: 8080, sub: "cloud" },
    { name: "postgresql", port: 5432, sub: null },
    { name: "qbittorrent", port: 8090, sub: "torrent" },
    { name: "redis", port: 6379, sub: null },
    { name: "redlib", port: 8081, sub: "reddit" },
    { name: "romm", port: 8098, sub: "romm" },
    { name: "searxng", port: 8888, sub: "search" },
    { name: "snapcast", port: 1780, sub: "audio" },
    { name: "syncthing", port: 8384, sub: "sync" },
    { name: "vaultwarden", port: 8222, sub: "vault" },
    { name: "zigbee2mqtt", port: 8099, sub: "zigbee" },
  ];

  const DEFAULTS = { name: "paperless", sub: "docs", port: "8000", auth: "forward-auth", tier: "workload", nfs: "" };

  const rack = $("#rack");
  if (rack) {
    const f = {
      name: $("#f-name"), sub: $("#f-sub"), port: $("#f-port"),
      auth: $("#f-auth"), tier: $("#f-tier"), nfs: $("#f-nfs"),
    };
    const out = { caddy: $("#o-caddy"), systemd: $("#o-systemd"), sso: $("#o-sso"), dash: $("#o-dash") };
    const status = $("#status");
    const statusText = $("#status-text");
    const panels = $(".panels", rack);

    const cleanName = (v) => v.toLowerCase().replace(/[^a-z0-9-]/g, "").replace(/^-+/, "");
    const cleanSub = (v) => v.toLowerCase().replace(/[^a-z0-9-]/g, "");
    const title = (n) => n.split("-").filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

    const com = (s) => `<span class="com">${esc(s)}</span>`;
    const hl = (s) => `<span class="hl">${esc(s)}</span>`;
    const kw = (s) => `<span class="kw">${esc(s)}</span>`;
    const st = (s) => `<span class="st">${esc(s)}</span>`;

    function fit(el) {
      let text;
      if (el.tagName === "SELECT") text = el.options[el.selectedIndex].text;
      else text = el.value;
      el.style.width = Math.max(text.length, 2) + "ch";
    }

    function read() {
      const name = cleanName(f.name.value) || "service";
      const portRaw = f.port.value.trim();
      const port = /^\d+$/.test(portRaw) ? parseInt(portRaw, 10) : NaN;
      return {
        name,
        sub: cleanSub(f.sub.value),
        portRaw,
        port,
        portOk: Number.isInteger(port) && port >= 1 && port <= 65535,
        auth: f.auth.value,
        tier: f.tier.value,
        nfs: f.nfs.value,
      };
    }

    function check(s) {
      const errors = [];
      const others = CLAIMS.filter((c) => c.name !== s.name);
      if (!s.portOk) {
        errors.push(`lanbat.services.${s.name}.port must be a port number between 1 and 65535, not "${s.portRaw}"`);
      } else {
        const owners = others.filter((c) => c.port === s.port).map((c) => c.name);
        if (owners.length) errors.push(`lanbat: port ${s.port} is used by ${owners.concat(s.name).sort().join(", ")}`);
      }
      if (!s.sub) {
        errors.push(`lanbat: ${s.name} is on the dashboard but has no subdomain to link to`);
      } else {
        const owners = others.filter((c) => c.sub === s.sub).map((c) => c.name);
        if (owners.length) errors.push(`lanbat: subdomain ${s.sub} is used by ${owners.concat(s.name).sort().join(", ")}`);
      }
      return errors;
    }

    function caddy(s) {
      const host = `${s.sub || "?"}.<domain>`;
      const upstream = `localhost:${s.portOk ? s.port : "?"}`;
      const page = s.nfs ? "storage.html" : "offline.html";
      const L = [];
      L.push(`${hl(host)} {`);
      L.push(`  ${kw("tls")} internal { on_demand }`);
      L.push("");
      L.push(`  ${kw("handle_errors")} 502 503 504 {`);
      L.push(`    rewrite * /${st(page)}`);
      L.push(`    file_server { root /var/lib/caddy-error-pages }`);
      L.push(`  }`);
      L.push("");
      if (s.auth === "forward-auth") {
        L.push(`  ${kw("route")} {`);
        L.push(`    request_header -X-Authentik-Username  ${com("# a client can't pick its own name")}`);
        L.push(`    ${com("# ...Authentik session check, generated...")}`);
        L.push(`    ${kw("reverse_proxy")} ${hl(upstream)}`);
        L.push(`  }`);
      } else if (s.auth === "none") {
        L.push(`  ${com("# auth = \"none\": open to the LAN on purpose")}`);
        L.push(`  ${kw("reverse_proxy")} ${hl(upstream)}`);
      } else {
        L.push(`  ${com("# auth = \"app\": the service handles its own login")}`);
        L.push(`  ${kw("reverse_proxy")} ${hl(upstream)}`);
      }
      L.push(`}`);
      return L.join("\n");
    }

    function systemd(s) {
      const L = [];
      L.push(com(`# ${s.name}.service`));
      if (s.tier === "workload") {
        L.push(`[Unit]`);
        L.push(`After=workload-online.target workload-init.service`);
        L.push(`BindsTo=workload-online.target     ${com("# stops when the layer locks")}`);
        L.push("");
        L.push(`[Install]`);
        L.push(`WantedBy=${st("workload-online.target")}  ${com("# not multi-user.target")}`);
        L.push("");
        L.push(com(`# /var/lib/${s.name} is bind-mounted from /mnt/workload (LUKS).`));
        L.push(com(`# Nothing starts until you run unlock-workload.`));
      } else {
        L.push(`[Install]`);
        L.push(`WantedBy=${st("multi-user.target")}  ${com("# starts at boot, no unlock needed")}`);
      }
      if (s.nfs) {
        L.push("");
        L.push(com(`# nfs.drives = [ "${s.nfs}" ]: the Pi's drive ${s.nfs} is mounted over NFS.`));
        L.push(com(`# If the Pi stops answering, ${s.name} stops cleanly and`));
        L.push(com(`# comes back when the mount returns. Meanwhile Caddy serves storage.html.`));
      }
      return L.join("\n");
    }

    function sso(s) {
      const host = `${s.sub || "?"}.<domain>`;
      if (s.auth === "forward-auth") {
        return [
          com(`# auth = "forward-auth"`),
          `Authentik guards ${hl(host)}.`,
          `Caddy asks Authentik about the session before it proxies`,
          `to ${hl("localhost:" + (s.portOk ? s.port : "?"))}, and passes the user's name`,
          `along in X-Authentik-Username.`,
          "",
          com(`# Only some people? Add access.groups to limit it to`),
          com(`# Authentik groups.`),
        ].join("\n");
      }
      if (s.auth === "none") {
        return [
          com(`# auth = "none"`),
          `No authentication. ${hl(host)} is open to everyone on the LAN,`,
          `like Homepage and SearXNG.`,
          "",
          com(`# Intentional, and visible in the file instead of an accident.`),
        ].join("\n");
      }
      return [
        com(`# auth = "app"`),
        `Nothing is generated here. ${esc(title(s.name))} handles its own login,`,
        `through native OIDC or its own accounts.`,
        "",
        com(`# Register it with Authentik by adding`),
        com(`# oidc.redirectPaths = [ "/..." ];`),
      ].join("\n");
    }

    function dash(s) {
      return [
        com("# Homepage tile"),
        `${kw("Documents")}:`,
        `  - ${st(title(s.name))}:`,
        `      href: https://${hl((s.sub || "?") + ".<domain>")}`,
        `      description: Document archive`,
      ].join("\n");
    }

    function render() {
      const s = read();
      // inline name echoes
      $$("[data-name]", rack).forEach((el) => (el.textContent = s.name));
      $$("[data-title]", rack).forEach((el) => (el.textContent = title(s.name)));

      // conditional lines (what checks.nix requires for each combination)
      const needState = s.tier === "workload";
      const needUnits = s.tier === "workload" || !!s.nfs;
      $$('[data-show="state"]', rack).forEach((el) => (el.hidden = !needState));
      $$('[data-show="units"]', rack).forEach((el) => (el.hidden = !needUnits));
      $$('[data-show="nfsunits"]', rack).forEach((el) => (el.hidden = !s.nfs));

      // fit inputs
      Object.values(f).forEach(fit);
      f.port.setAttribute("aria-invalid", String(!s.portOk));
      f.sub.setAttribute("aria-invalid", String(!s.sub));

      out.caddy.innerHTML = caddy(s);
      out.systemd.innerHTML = systemd(s);
      out.sso.innerHTML = sso(s);
      out.dash.innerHTML = dash(s);

      const errors = check(s);
      status.dataset.ok = String(errors.length === 0);
      panels.style.opacity = errors.length ? ".4" : "1";
      if (errors.length === 0) {
        statusText.innerHTML = `<div><strong>eval ok</strong></div><div>No port, subdomain or secret clashes. 0 failed assertions.</div>`;
      } else {
        statusText.innerHTML =
          `<div><strong>error: Failed assertions:</strong></div>` +
          errors.map((e) => `<div>- ${esc(e)}</div>`).join("") +
          `<div>Nothing is generated or deployed.</div>`;
      }
    }

    f.name.addEventListener("input", () => { const c = cleanName(f.name.value); if (c !== f.name.value) f.name.value = c; render(); });
    f.sub.addEventListener("input", () => { const c = cleanSub(f.sub.value); if (c !== f.sub.value) f.sub.value = c; render(); });
    f.port.addEventListener("input", () => { const c = f.port.value.replace(/\D/g, ""); if (c !== f.port.value) f.port.value = c; render(); });
    [f.auth, f.tier, f.nfs].forEach((el) => el.addEventListener("change", render));

    $$("[data-set]", rack).forEach((btn) =>
      btn.addEventListener("click", () => {
        const v = btn.dataset.set;
        if (v === "reset") {
          f.name.value = DEFAULTS.name; f.sub.value = DEFAULTS.sub; f.port.value = DEFAULTS.port;
          f.auth.value = DEFAULTS.auth; f.tier.value = DEFAULTS.tier; f.nfs.value = DEFAULTS.nfs;
        } else {
          const [k, val] = v.split("=");
          f[k].value = val;
        }
        render();
      })
    );

    // Tabs
    const tabs = $$('[role="tab"]', rack);
    function selectTab(tab, focus) {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        $("#" + t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
    }
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => selectTab(t, false));
      t.addEventListener("keydown", (e) => {
        let n = null;
        if (e.key === "ArrowRight") n = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") n = tabs[0];
        else if (e.key === "End") n = tabs[tabs.length - 1];
        if (n) { e.preventDefault(); selectTab(n, true); }
      });
    });

    // Webfonts change the width of "ch"; refit once they load.
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
    render();
  }

  /* ───────────────────────── Tier simulator ───────────────────────── */
  const sim = $("#sim");
  if (sim) {
    // Tiers and drive use are taken from the service files in services/.
    const SERVICES = [
      // starts at boot
      { id: "homepage", n: "Homepage", s: "home.<domain>", lane: "boot" },
      { id: "authentik", n: "Authentik", s: "auth.<domain>", lane: "boot" },
      { id: "ha", n: "Home Assistant", s: "ha.<domain>", lane: "boot" },
      { id: "frigate", n: "Frigate", s: "nvr.<domain>", lane: "boot" },
      { id: "grafana", n: "Grafana", s: "grafana.<domain>", lane: "boot" },
      { id: "searxng", n: "SearXNG", s: "search.<domain>", lane: "boot" },
      { id: "redlib", n: "Redlib", s: "reddit.<domain>", lane: "boot" },
      { id: "invidious", n: "Invidious", s: "youtube.<domain>", lane: "boot" },
      { id: "z2m", n: "Zigbee2MQTT", s: "zigbee.<domain>", lane: "boot" },
      { id: "music", n: "Music Assistant", s: "music.<domain>", lane: "boot", nfs: true },
      { id: "snap", n: "Snapcast", s: "audio.<domain>", lane: "boot" },
      { id: "ca", n: "CA page", s: "ca.<domain>", lane: "boot" },
      { id: "mqtt", n: "Mosquitto", s: "MQTT :1883", lane: "boot" },
      { id: "influx", n: "InfluxDB", s: "internal only", lane: "boot" },
      { id: "wyoming", n: "Wyoming voice", s: "LAN-internal", lane: "boot" },
      { id: "telegraf", n: "Telegraf", s: "internal only", lane: "boot" },
      // waits for unlock-workload
      { id: "nextcloud", n: "Nextcloud", s: "cloud.<domain>", lane: "workload" },
      { id: "immich", n: "Immich", s: "photos.<domain>", lane: "workload", nfs: true },
      { id: "jellyfin", n: "Jellyfin", s: "media.<domain>", lane: "workload", nfs: true },
      { id: "qbt", n: "qBittorrent", s: "torrent.<domain>", lane: "workload", nfs: true },
      { id: "vault", n: "Vaultwarden", s: "vault.<domain>", lane: "workload" },
      { id: "sync", n: "Syncthing", s: "sync.<domain>", lane: "workload", nfs: true },
      { id: "samba", n: "Samba", s: "SMB :445", lane: "workload", nfs: true },
      { id: "abs", n: "Audiobookshelf", s: "audiobooks.<domain>", lane: "workload", nfs: true },
      { id: "jackett", n: "Jackett", s: "jackett.<domain>", lane: "workload" },
      // on demand
      { id: "bitmagnet", n: "Bitmagnet", s: "bitmagnet.<domain>", lane: "demand" },
      { id: "romm", n: "RomM", s: "romm.<domain>", lane: "demand", nfs: true },
    ];

    const LANES = [
      { id: "boot", t: "Starts at boot", d: "No key needed. SSH, networking, SSO and home automation are up before you touch anything." },
      { id: "workload", t: "Waits for unlock", d: "Personal data on the encrypted layer. Starts when you run unlock-workload." },
      { id: "demand", t: "Starts on request", d: "Woken by its first visit, stopped after sitting idle." },
    ];

    const lanes = $("#lanes");
    const term = $("#sim-term");
    const awake = new Set();
    const nodes = new Map();

    LANES.forEach((l) => {
      const el = document.createElement("div");
      el.className = "lane lane--" + l.id;
      el.innerHTML = `<h3>${l.t}</h3><p>${l.d}</p><ul class="lane__list"></ul>`;
      const ul = $("ul", el);
      SERVICES.filter((s) => s.lane === l.id).forEach((s) => {
        const li = document.createElement("li");
        // Only on-demand services react to a click (the "first request").
        const b = document.createElement(l.id === "demand" ? "button" : "div");
        b.className = "svc";
        b.innerHTML = `<span class="svc__name">${esc(s.n)}</span><span class="svc__sub">${esc(s.s)}</span><span class="svc__state"></span>`;
        if (l.id === "demand") {
          b.type = "button";
          b.addEventListener("click", () => {
            if (stateOf(s) === "asleep") { awake.add(s.id); update(); }
          });
        }
        li.appendChild(b);
        ul.appendChild(li);
        nodes.set(s.id, b);
      });
      lanes.appendChild(el);
    });

    const val = (name) => $(`input[name="${name}"]:checked`, sim).value;

    function stateOf(s) {
      const unlocked = val("sim-lock") === "unlocked";
      const piDown = val("sim-pi") === "down";
      const gone = s.nfs && piDown;
      if (s.lane === "boot") return gone ? "gone" : "running";
      if (!unlocked) return "locked";
      if (s.lane === "workload") return gone ? "gone" : "running";
      if (!awake.has(s.id)) return "asleep";
      return gone ? "gone" : "running";
    }

    const LABEL = {
      running: (s) => (s.lane === "demand" ? "awake, idle soon" : "running"),
      locked: () => "locked",
      gone: () => "waiting for Pi",
      asleep: () => "click to wake",
    };

    function update() {
      if (val("sim-lock") === "locked") awake.clear();
      let gone = 0, running = 0;
      SERVICES.forEach((s) => {
        const st = stateOf(s);
        const b = nodes.get(s.id);
        b.dataset.state = st;
        $(".svc__state", b).textContent = LABEL[st](s);
        if (st === "gone") gone++;
        if (st === "running") running++;
        if (s.lane === "demand") {
          b.tabIndex = st === "asleep" ? 0 : -1;
          b.setAttribute("aria-disabled", String(st !== "asleep"));
        }
      });
      const unlocked = val("sim-lock") === "unlocked";
      const piDown = val("sim-pi") === "down";
      term.innerHTML =
        `<span class="dim">$ systemctl is-active workload-online.target</span>\n` +
        (unlocked ? `<span class="ok">active</span>` : `<span class="no">inactive</span> <span class="dim">(run unlock-workload)</span>`) +
        `\n<span class="dim"># Pi storage over NFS</span>\n` +
        (piDown
          ? `<span class="no">unreachable</span> <span class="dim">(${gone} service${gone === 1 ? "" : "s"} stopped, restart when it returns)</span>`
          : `<span class="ok">reachable</span>`) +
        `\n<span class="dim">${running} running</span>`;
    }

    $$("input", sim).forEach((i) => i.addEventListener("change", update));
    update();
  }

  /* ───────────────────────── Copy buttons ───────────────────────── */
  $$("[data-copy]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const code = btn.parentElement.querySelector("code").innerText;
      try {
        await navigator.clipboard.writeText(code);
        btn.textContent = "Copied";
      } catch (_) {
        btn.textContent = "Press Ctrl+C";
        const r = document.createRange();
        r.selectNodeContents(btn.parentElement.querySelector("code"));
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
      }
      setTimeout(() => (btn.textContent = "Copy"), 1600);
    })
  );
})();
