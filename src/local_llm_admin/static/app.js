// Shared helpers: API calls with CSRF, formatting, status badge, small SVG charts.
"use strict";
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", ...opts, headers: { ...(opts.headers || {}) } };
  if (init.method && init.method !== "GET") init.headers["X-CSRF-Token"] = CSRF;
  if (init.json !== undefined) {
    init.body = JSON.stringify(init.json);
    init.headers["Content-Type"] = "application/json";
    delete init.json;
  }
  const resp = await fetch(path, init);
  if (resp.status === 401) { location.href = "/login"; throw new Error("login required"); }
  const body = resp.headers.get("content-type")?.includes("json") ? await resp.json() : await resp.text();
  if (!resp.ok) {
    const msg = (body && (body.message || body.detail)) || `HTTP ${resp.status}`;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = resp.status; err.body = body; throw err;
  }
  return body;
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) n.append(c.nodeType ? c : String(c));
  return n;
}

const fmt = {
  num: (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString()),
  pct: (n) => (n === null || n === undefined ? "—" : `${Number(n).toFixed(0)}%`),
  bytes(n) {
    if (n === null || n === undefined) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; n = Number(n);
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  },
  rate: (n) => (n === null || n === undefined ? "—" : `${fmt.bytes(n)}/s`),
  tok: (n) => (n === null || n === undefined ? "—" : `${Number(n).toFixed(1)} tok/s`),
  time: (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "—"),
  clock: (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  compact(n) {
    n = Number(n);
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
    return String(n);
  },
};

const STATE_LABEL = { serving: "Serving", loading: "Loading", switching: "Switching", stopped: "Stopped",
  "chat-ui": "Chat UI mode (API down)", "drive-missing": "Drive missing", error: "Error", unknown: "…",
  comfyui: "ComfyUI", "comfyui-starting": "ComfyUI starting", strata: "Strata", "strata-starting": "Strata starting",
  conflict: "Conflict: two engines running" };

const statusListeners = [];
async function refreshStatus() {
  const badge = document.getElementById("state-badge");
  try {
    const st = await api("/api/status");
    badge.dataset.state = st.state;
    badge.textContent = st.engine?.active && st.engine.active !== "mlx"
      ? EngineUI.badge(st) : STATE_LABEL[st.state] || st.state;
    const nav = document.getElementById("nav-alerts");
    if (nav && st.alerts) {
      nav.hidden = !st.alerts.unacked;
      nav.textContent = String(st.alerts.unacked);
      nav.dataset.severity = st.alerts.worst_severity || "";
      nav.title = `${st.alerts.unacked} unacknowledged, ${st.alerts.open} open`;
    }
    statusListeners.forEach((fn) => fn(st));
  } catch (e) {
    badge.dataset.state = "error"; badge.textContent = "Admin unreachable";
  }
}
function onStatus(fn) { statusListeners.push(fn); }

document.getElementById("logout-form")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  await fetch("/logout", { method: "POST", headers: { "X-CSRF-Token": CSRF } });
  location.href = "/login";
});

// ---- charts (inline SVG, one y-axis, recessive grid, hover tooltip) ---------
const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}
function tooltip(container) {
  let tip = container.querySelector(".tip");
  if (!tip) { tip = el("div", { class: "tip", hidden: true }); container.append(tip); }
  return {
    show(x, y, lines) {
      tip.replaceChildren(...lines.map((l) => el("div", { text: l })));
      tip.hidden = false;
      const w = container.clientWidth, tw = tip.offsetWidth;
      tip.style.left = `${Math.min(Math.max(0, x + 12), w - tw)}px`;
      tip.style.top = `${Math.max(0, y - 36)}px`;
    },
    hide() { tip.hidden = true; },
  };
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)); const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

// points: [{t: epoch s, v: number|null}]
function lineChart(container, points, { max = null, unit = "%" } = {}) {
  const W = container.clientWidth || 300, H = container.clientHeight || 160;
  const m = { l: 36, r: 8, t: 8, b: 20 };
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  const pts = points.filter((p) => p.v !== null && p.v !== undefined);
  const top = max ?? niceMax(Math.max(1, ...pts.map((p) => p.v)));
  const t0 = points.length ? points[0].t : 0, t1 = points.length ? points[points.length - 1].t : 1;
  const x = (t) => m.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - m.l - m.r);
  const y = (v) => H - m.b - (v / top) * (H - m.t - m.b);
  for (const f of [0, 0.5, 1]) {
    const yy = y(top * f);
    svg.append(svgEl("line", { class: f === 0 ? "base" : "grid", x1: m.l, x2: W - m.r, y1: yy, y2: yy }));
    const t = svgEl("text", { x: m.l - 6, y: yy + 4, "text-anchor": "end" }); t.textContent = fmt.compact(top * f); svg.append(t);
  }
  if (points.length) {
    for (const [tt, anchor] of [[t0, "start"], [t1, "end"]]) {
      const t = svgEl("text", { x: x(tt), y: H - 4, "text-anchor": anchor }); t.textContent = fmt.clock(tt); svg.append(t);
    }
  }
  if (pts.length > 1) {
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    svg.append(svgEl("path", { class: "area", d: `${d}L${x(pts[pts.length - 1].t)},${y(0)}L${x(pts[0].t)},${y(0)}Z` }));
    svg.append(svgEl("path", { class: "line", d }));
  }
  const cross = svgEl("line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const dot = svgEl("circle", { class: "dot", r: 4, visibility: "hidden" });
  svg.append(cross, dot);
  const hit = svgEl("rect", { class: "hit", x: m.l, y: 0, width: Math.max(0, W - m.l - m.r), height: H });
  svg.append(hit);
  container.replaceChildren(svg);
  const tip = tooltip(container);
  hit.addEventListener("pointermove", (ev) => {
    if (!pts.length) return;
    const r = svg.getBoundingClientRect(); const px = ((ev.clientX - r.left) / r.width) * W;
    let best = pts[0];
    for (const p of pts) if (Math.abs(x(p.t) - px) < Math.abs(x(best.t) - px)) best = p;
    cross.setAttribute("x1", x(best.t)); cross.setAttribute("x2", x(best.t)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(best.t)); dot.setAttribute("cy", y(best.v)); dot.setAttribute("visibility", "visible");
    tip.show((x(best.t) / W) * r.width, (y(best.v) / H) * r.height, [fmt.clock(best.t), `${best.v.toFixed(1)}${unit}`]);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hide(); });
}

// days: [{label, a, b, c?, lines: [..tooltip]}] stacked a (bottom) + b + c (top); classes
// name each segment's fill (default a = s1, b = s2)
function stackedBars(container, days, { classes = ["bar-p", "bar-c", "bar-x"] } = {}) {
  const W = container.clientWidth || 600, H = container.clientHeight || 240;
  const m = { l: 44, r: 8, t: 8, b: 22 };
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  const top = niceMax(Math.max(1, ...days.map((d) => d.a + d.b + (d.c || 0))));
  const y = (v) => H - m.b - (v / top) * (H - m.t - m.b);
  for (const f of [0, 0.25, 0.5, 0.75, 1]) {
    const yy = y(top * f);
    svg.append(svgEl("line", { class: f === 0 ? "base" : "grid", x1: m.l, x2: W - m.r, y1: yy, y2: yy }));
    const t = svgEl("text", { x: m.l - 6, y: yy + 4, "text-anchor": "end" }); t.textContent = fmt.compact(top * f); svg.append(t);
  }
  const slot = (W - m.l - m.r) / Math.max(1, days.length);
  const bw = Math.max(2, Math.min(28, slot - 2));  // >= 2px gap between adjacent bars
  container.replaceChildren();
  const tip = tooltip(container);
  const every = Math.ceil(days.length / Math.max(1, Math.floor((W - m.l) / 56)));
  days.forEach((d, i) => {
    const x0 = m.l + i * slot + (slot - bw) / 2;
    const r = Math.min(4, bw / 2);
    const vals = [d.a, d.b, d.c || 0];
    const last = vals.reduce((k, v, j) => (v > 0 ? j : k), -1);
    let base = 0;
    vals.forEach((v, j) => {
      if (v <= 0) return;
      // square at the baseline and between segments, rounded only on the top segment;
      // 2px surface gap between stacked segments
      const yt = y(base + v), h = Math.max(0, y(base) - yt - (base > 0 ? 2 : 0));
      svg.append(j === last ? svgEl("path", { class: classes[j], d: roundTop(x0, yt, bw, h, r) })
        : svgEl("rect", { class: classes[j], x: x0, y: yt, width: bw, height: h }));
      base += v;
    });
    if (i % every === 0) {
      const t = svgEl("text", { x: x0 + bw / 2, y: H - 6, "text-anchor": "middle" }); t.textContent = d.label.slice(5); svg.append(t);
    }
    const hit = svgEl("rect", { class: "hit bar-hit", x: m.l + i * slot, y: m.t, width: slot, height: H - m.t - m.b });
    hit.addEventListener("pointermove", (ev) => {
      const b = svg.getBoundingClientRect();
      tip.show(ev.clientX - b.left, ev.clientY - b.top, d.lines);
    });
    hit.addEventListener("pointerleave", () => tip.hide());
    svg.append(hit);
  });
  container.prepend(svg);
}
function roundTop(x, y, w, h, r) {
  if (h <= 0) return "";
  r = Math.min(r, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

async function setEngine(target, force = false) {
  return api("/api/engine", { method: "POST", json: { target, force } });
}

// Submit an engine switch; if ComfyUI has queued work, ask before cancelling it (D-C4).
// `ask(title, body)` resolves to true/false; returns the job, or null if declined.
async function setEngineWithForce(target, ask) {
  try {
    return await setEngine(target);
  } catch (e) {
    if (e.status !== 409 || !/queued or running|queue state is unknown/.test(e.message)) throw e;
    if (!(await ask("Cancel ComfyUI work?", `${e.message}. Continue and switch?`))) return null;
    return setEngine(target, true);
  }
}
const nativeAsk = async (title, body) => window.confirm(`${title}\n\n${body}`);
let conflictStatus = null;
onStatus((st) => {
  const banner = document.getElementById("conflict-banner");
  if (!banner) return;
  conflictStatus = st;
  banner.hidden = st.engine?.active !== "conflict";
  const container = document.getElementById("conflict-engine-controls");
  const running = Object.values(st.engine?.backends || {}).filter(b => b.enabled && b.running);
  container?.replaceChildren(...running.map(b => {
    const ref = b.operations.includes("multi-model") ? st.selected : b.reference;
    const target = b.id + (ref ? ":" + ref : "");
    return el("button", {"data-keep": target, text: "Keep " + b.label});
  }));
});
document.getElementById("conflict-banner")?.addEventListener("click", async (ev) => {
  const target = ev.target?.dataset?.keep;
  if (!target) return;
  const prompt = target === "off" ? "Stop every engine?"
    : "Keep " + (conflictStatus?.engine?.backends?.[target.split(":")[0]]?.label || target) + " and stop the other engines?";
  if (!window.confirm(prompt)) return;
  try { await setEngineWithForce(target, nativeAsk); refreshStatus(); } catch (e) { alert(e.message); }
});

// ---- diagnostics (shared by the dashboard Problems card and the Alerts page) ----
const CHECK_LABEL = { ok: "OK", warn: "Warning", fail: "Problem", unknown: "Unknown" };
const ACTIONS = {
  unlock: { label: "Unlock", path: "/api/diagnostics/unlock", confirm: "Remove the stale run/mlx.lock with mlx-model unlock?" },
  resume: { label: "Resume watchdog", path: "/api/watchdog/resume", confirm: "Resume automatic MLX recovery?" },
};

async function waitJob(id, onUpdate) {
  for (;;) {
    const job = await api(`/api/jobs/${id}`);
    onUpdate(job);
    if (job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function runAction(name, statusEl, done) {
  const a = ACTIONS[name];
  if (!a || !window.confirm(a.confirm)) return;
  statusEl.textContent = "Submitting…";
  try {
    const res = await api(a.path, { method: "POST" });
    if (res.id) {
      await waitJob(res.id, (job) => { statusEl.textContent = `Job ${job.id}: ${job.status}`; });
    } else {
      statusEl.textContent = "Done";
    }
  } catch (e) {
    statusEl.textContent = e.message;
  }
  done?.();
  refreshStatus();
}

// all untrusted text goes through textContent (el's "text")
function checkRow(c, done) {
  const status = el("span", { class: "muted action-status", "aria-live": "polite" });
  const act = c.action && ACTIONS[c.action]
    ? el("button", { onclick: () => runAction(c.action, status, done), text: ACTIONS[c.action].label }) : null;
  return el("li", { class: "check", "data-status": c.status },
    el("div", { class: "check-head" },
      el("span", { class: "sev", "data-status": c.status, text: CHECK_LABEL[c.status] || c.status }),
      el("strong", { text: c.title }),
      c.stale ? el("span", { class: "pill", text: "stale" }) : null,
      el("span", { class: "muted", text: `checked ${new Date(c.observed_at * 1000).toLocaleTimeString()}` })),
    el("div", { text: c.evidence }),
    c.fix ? el("div", { class: "muted", text: c.fix }) : null,
    act ? el("div", { class: "actions" }, status, act) : null);
}

refreshStatus();
setInterval(refreshStatus, 5000);
