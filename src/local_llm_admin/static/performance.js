"use strict";
const $ = (id) => document.getElementById(id);
let days = 7;
let nextBefore = null;
let lastSummary = null;

function isoLocal(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function params() {
  const to = new Date(); const from = new Date(to.getTime() - (days - 1) * 86400000);
  const model = $("model").value;
  return `from=${isoLocal(from)}&to=${isoLocal(to)}${model ? `&model=${encodeURIComponent(model)}` : ""}`;
}
const secs = (v) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(v < 10 ? 2 : 1)} s`);
const hours = (s) => `${(s / 3600).toFixed(1)} h`;
const pctOrDash = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function showError(e) {
  $("perf-error").hidden = !e;
  if (e) $("perf-error").textContent = e.message;
}

function tile(label, value, sub) {
  return el("div", {}, el("div", { class: "label", text: label }), el("div", { class: "hero-value", text: value }),
    sub ? el("div", { class: "label", text: sub }) : null);
}

async function loadSummary() {
  const [s, a] = await Promise.all([api(`/api/performance/summary?${params()}`), api(`/api/availability?${params().replace(/&model=[^&]*/, "")}`)]);
  lastSummary = { s, a };
  const sel = $("model"); const cur = sel.value;
  sel.replaceChildren(el("option", { value: "", text: "All models" }), ...s.models.map((m) => el("option", { value: m, text: m })));
  sel.value = s.models.includes(cur) ? cur : "";
  const t = s.totals;
  $("perf-source").textContent = `Source: ${s.source.parser} · last read ${s.source.last_ingest ? fmt.time(s.source.last_ingest) : "never"}`;
  $("perf-totals").replaceChildren(
    tile("Requests", fmt.num(t.requests), `${fmt.num(t.errors)} errors · ${fmt.num(t.cancelled)} cancelled · ${fmt.num(t.incomplete)} incomplete`),
    tile("TTFT median / p90", `${secs(t.ttft_p50)} / ${secs(t.ttft_p90)}`, `${fmt.num(t.slow)} slow (≥ ${s.slow_ttft_s} s)`),
    tile("Decode median / p90", `${fmt.tok(t.decode_p50)} / ${fmt.tok(t.decode_p90)}`),
    tile("Availability", pctOrDash(a.totals.availability), `observed ${pctOrDash(a.totals.coverage)} of the range`),
    tile("Partial rows", fmt.num(t.partial), "missing some measurements"));
  renderAvailability(a);
  const byDay = s.days;
  const bars = (key50, key90, fmtv) => byDay.map((d) => ({
    label: d.date, a: d[key50] ?? 0, b: Math.max(0, (d[key90] ?? 0) - (d[key50] ?? 0)),
    lines: [d.date, `median ${fmtv(d[key50])}`, `p90 ${fmtv(d[key90])}`, `${fmt.num(d.requests)} requests`],
  }));
  stackedBars($("ttft-chart"), bars("ttft_p50", "ttft_p90", secs));
  stackedBars($("decode-chart"), bars("decode_p50", "decode_p90", fmt.tok));
  stackedBars($("count-chart"), byDay.map((d) => {
    const bad = d.errors + d.cancelled + d.incomplete;
    return { label: d.date, a: d.requests - bad, b: bad,
      lines: [d.date, `${fmt.num(d.requests - bad)} completed`, `${fmt.num(d.errors)} errors`, `${fmt.num(d.cancelled)} cancelled`, `${fmt.num(d.incomplete)} incomplete`] };
  }));
}

function renderAvailability(a) {
  const strip = $("avail-strip");
  const lo = new Date(`${a.from}T00:00:00`).getTime() / 1000;
  const segs = a.segments;
  const t0 = a.days.length ? Math.min(lo, ...segs.map((x) => x.start)) : lo;
  const t1 = Math.max(Date.now() / 1000, ...segs.map((x) => x.end));
  const span = Math.max(1, t1 - t0);
  strip.replaceChildren(...segs.map((x) => el("span", {
    class: `st-${x.state}`, title: `${x.state}: ${fmt.time(x.start)} – ${fmt.time(x.end)}`,
    style: `left:${((x.start - t0) / span) * 100}%;width:${Math.max(0.1, ((x.end - x.start) / span) * 100)}%`,
  })));
  $("avail-summary").textContent = a.totals.intended_s ? `${pctOrDash(a.totals.availability)} over ${hours(a.totals.intended_s)} with MLX intended`
    : "MLX was not the intended engine in this range";
  const seen = [...a.days].reverse().filter((d) => d.observed_s > 0);
  const unseen = a.days.length - seen.length;
  $("avail-table").querySelector("tbody").replaceChildren(...seen.map((d) => el("tr", {},
    el("td", { text: d.date }), el("td", { class: "num", text: pctOrDash(d.availability) }),
    el("td", { class: "num", text: pctOrDash(d.coverage) }), el("td", { class: "num", text: hours(d.seconds.serving) }),
    el("td", { class: `num ${d.seconds.down ? "bad" : ""}`, text: hours(d.seconds.down + d.seconds.conflict) }),
    el("td", { class: "num", text: hours(d.seconds.off + d.seconds.comfyui + (d.seconds.strata || 0)) }),
    el("td", { class: "num muted", text: hours(d.unknown_s) }))),
    ...(unseen ? [el("tr", {}, el("td", { colspan: 7, class: "muted",
      text: `${unseen} day${unseen === 1 ? "" : "s"} in this range with no observations (not counted as up or down).` }))] : []));
}

function reqRow(r) {
  const partial = r.complete ? "" : " (partial)";
  return el("tr", {},
    el("td", { text: fmt.time(r.started) }), el("td", { text: r.model || "unknown" }),
    el("td", { class: r.state === "completed" ? "" : "bad", text: `${r.state}${r.error ? `: ${r.error}` : ""}${partial}` }),
    el("td", { class: "num", text: fmt.num(r.prompt_tokens) }), el("td", { class: "num", text: fmt.num(r.cached_tokens) }),
    el("td", { class: "num", text: fmt.num(r.generated_tokens) }), el("td", { class: "num", text: secs(r.ttft_s) }),
    el("td", { class: "num", text: fmt.tok(r.decode_tok_s) }), el("td", { class: "num", text: secs(r.elapsed_s) }),
    el("td", {}, el("button", { class: "link", text: "Log", onclick: () => showContext(r.id) })));
}

async function loadRequests(append = false) {
  const state = $("state-filter").value;
  const q = `${params()}&limit=50${state ? `&state=${state}` : ""}${$("slow-filter").checked ? "&slow=true" : ""}${append && nextBefore ? `&before=${nextBefore}` : ""}`;
  const r = await api(`/api/performance/requests?${q}`);
  nextBefore = r.next;
  const body = $("req-table").querySelector("tbody");
  const rows = r.requests.map(reqRow);
  if (append) body.append(...rows);
  else body.replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: 10, class: "muted", text: "No requests recorded in this range." }))]));
  $("more").hidden = !r.next;
}

async function showContext(id) {
  const box = $("req-context");
  const c = await api(`/api/performance/requests/${id}/context`);
  box.hidden = false;
  box.textContent = c.available ? c.lines.join("\n") : c.reason;
}

async function reload() {
  try { showError(null); $("req-context").hidden = true; await Promise.all([loadSummary(), loadRequests()]); }
  catch (e) { showError(e); }
}

document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
  days = Number(b.dataset.days);
  document.querySelectorAll("[data-days]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  reload();
}));
$("model").addEventListener("change", reload);
$("state-filter").addEventListener("change", () => loadRequests().catch(showError));
$("slow-filter").addEventListener("change", () => loadRequests().catch(showError));
$("more").addEventListener("click", () => loadRequests(true).catch(showError));
window.addEventListener("resize", () => { if (lastSummary) loadSummary().catch(showError); });
api("/api/maintenance/log-rotation").then((r) => {
  const last = r.last;
  const parts = [`Log rotation: daily at ${String(r.hour).padStart(2, "0")}:00, logs over ${r.max_mib} MiB, ${r.keep} archives kept.`];
  if (last?.outcome) parts.push(`Last run ${last.day || ""}: ${last.outcome}.`);
  const deferred = last?.result?.deferred || [];
  if (deferred.length) parts.push(`Deferred (writer running): ${deferred.map((d) => d.name).join(", ")}.`);
  if (last?.skipped) parts.push(`Waiting: ${last.skipped}`);
  $("rotation-state").textContent = parts.join(" ");
}).catch(() => {});
reload();
