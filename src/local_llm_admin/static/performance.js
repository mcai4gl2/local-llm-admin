"use strict";
const $ = (id) => document.getElementById(id);
let days = 7;
let nextBefore = null;
let lastSummary = null;
let lastTokens = null;

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
  const splash = s.coverage?.splash;
  $("splash-coverage").hidden = !splash?.enabled;
  $("splash-coverage").textContent = splash?.reason || "";
  $("perf-source").textContent = `MLX log source: ${s.source.parser} · last read ${s.source.last_ingest ? fmt.time(s.source.last_ingest) : "never"}`;
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

const share = (part, whole) => (whole ? `${((part / whole) * 100).toFixed(1)}%` : "—");

async function loadTokens() {
  const origin = $("origin-filter").value;
  const t = await api(`/api/performance/tokens?${params()}${origin ? `&origin=${origin}` : ""}`);
  lastTokens = t;
  const s = t.totals;
  $("token-totals").replaceChildren(
    tile("Input tokens", fmt.num(s.input_tokens), `${fmt.num(s.input_tokens - s.cached_tokens)} uncached`),
    tile("Cached", fmt.num(s.cached_tokens), `${share(s.cached_tokens, s.input_tokens)} of input${s.cached_unknown ? ` · unknown for ${fmt.num(s.cached_unknown)} requests` : ""}`),
    tile("Output tokens", fmt.num(s.output_tokens)),
    tile("Requests", fmt.num(s.requests), `${fmt.num(s.unmeasured)} unmeasured`));
  drawTokens();
  $("token-table").querySelector("tbody").replaceChildren(...(t.models.length ? t.models.map((m) => el("tr", {},
    el("td", { text: m.model || "unknown" }), el("td", { class: "num", text: fmt.num(m.requests) }),
    el("td", { class: "num", text: fmt.num(m.input_tokens) }), el("td", { class: "num", text: fmt.num(m.cached_tokens) }),
    el("td", { class: "num", text: share(m.cached_tokens, m.input_tokens) }), el("td", { class: "num", text: fmt.num(m.output_tokens) }),
    el("td", { text: m.first }), el("td", { text: m.last })))
    : [el("tr", {}, el("td", { colspan: 8, class: "muted", text: "No token usage recorded in this range." }))]));
}

function drawTokens() {
  if (!lastTokens) return;
  stackedBars($("token-chart"), lastTokens.days.map((d) => ({
    label: d.date, a: d.input_tokens - d.cached_tokens, b: d.cached_tokens, c: d.output_tokens,
    lines: [d.date, `input ${fmt.num(d.input_tokens)} (${fmt.num(d.cached_tokens)} cached)`, `output ${fmt.num(d.output_tokens)}`,
      `${fmt.num(d.requests)} requests${d.unmeasured ? `, ${fmt.num(d.unmeasured)} unmeasured` : ""}`],
  })), { classes: ["bar-p", "bar-q", "bar-c"] });
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

async function loadNative() {
  const profile = $("native-profile").value;
  const query = params().replace(/&model=[^&]*/, "") + (profile ? `&profile=${encodeURIComponent(profile)}` : "");
  const [data, availability] = await Promise.all([api(`/api/performance/engines?${query}`), api(`/api/availability?source=splash&${query}`)]);
  const c = data.coverage;
  $("native-telemetry").hidden = !c.enabled && !data.readings.length;
  $("native-coverage").textContent = `${c.coverage} · last successful poll ${c.last_poll ? fmt.time(c.last_poll) : "never"} · last new data ${c.last_new_data ? fmt.time(c.last_new_data) : "never"}${c.stale ? " · stale" : ""}${c.error ? " · " + c.error : ""} · reset/population qualification: ${c.counter_qualification || "unverified"}`;
  const options = [...new Set(data.readings.map(r => r.profile))];
  if (!profile) $("native-profile").replaceChildren(el("option", {value: "", text: "All profiles"}), ...options.map(p => el("option", {value: p, text: p})));
  $("native-readings").querySelector("tbody").replaceChildren(...(data.readings.length ? data.readings.map(r => el("tr", {},
    el("td", {text: fmt.time(r.observed)}), el("td", {text: `${r.profile} · ${r.fingerprint.slice(0, 12)}`}),
    el("td", {text: r.epoch.slice(0, 12), title: JSON.stringify(r.identity)}),
    el("td", {text: fmt.num(r.counters.completed)}), el("td", {text: `${fmt.num(r.counters.cancelled)} / ${fmt.num(r.counters.failed)}`}),
    el("td", {text: fmt.num(r.counters.prefill_input_tokens)}), el("td", {text: fmt.num(r.counters.decode_output_tokens)})))
    : [el("tr", {}, el("td", {colspan: 7, text: "No attributed native snapshots in this range."}))]));
  const a = availability.totals;
  $("native-availability").textContent = `Splash loaded availability: ${pctOrDash(a.availability)} · observed ${pctOrDash(a.coverage)}. Released/recovering are not serving; maintenance excluded.${availability.truncated ? " Sample history truncated." : ""} `
    + (c.gaps?.length ? `Known gaps: ${c.gaps.map(g => g.reason).join(", ")}. Shutdown has no qualified final capture.` : "No historical observations are backfilled.");
}

async function reload() {
  try { showError(null); $("req-context").hidden = true; await Promise.all([loadSummary(), loadTokens(), loadRequests(), loadNative()]); }
  catch (e) { showError(e); }
}

document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
  days = Number(b.dataset.days);
  document.querySelectorAll("[data-days]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  reload();
}));
$("model").addEventListener("change", reload);
$("origin-filter").addEventListener("change", () => loadTokens().catch(showError));
$("state-filter").addEventListener("change", () => loadRequests().catch(showError));
$("slow-filter").addEventListener("change", () => loadRequests().catch(showError));
$("native-profile").addEventListener("change", () => loadNative().catch(showError));
$("more").addEventListener("click", () => loadRequests(true).catch(showError));
window.addEventListener("resize", () => { if (lastSummary) loadSummary().catch(showError); drawTokens(); });
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
