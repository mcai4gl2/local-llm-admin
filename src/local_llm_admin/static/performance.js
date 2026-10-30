"use strict";
const $ = id => document.getElementById(id);
let days = 7, generation = 0, nextBefore = null, nativeNext = null;
let nativeCursors = [null], nativeIndex = 0;
let lastSummary = null, lastTokens = null;
let engineChosen = false, backendStatus = null;
const sectionSuccess = {};
const offset = OpsTime.offset;
const opsDay = date => new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10);
const timezoneLabel = "UTC" + (offset <= 0 ? "+" : "−") + String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0") + ":" + String(Math.abs(offset) % 60).padStart(2, "0");
const opsTime = ts => ts ? new Date(ts * 1000 - offset * 60000).toISOString().slice(0, 19).replace("T", " ") + " " + timezoneLabel : "—";
const secs = v => v == null ? "—" : `${Number(v).toFixed(v < 10 ? 2 : 1)} s`;
const hours = s => `${(s / 3600).toFixed(1)} h`;
const pctOrDash = v => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const share = (part, whole) => whole ? `${((part / whole) * 100).toFixed(1)}%` : "—";
function querySnapshot() {
  const to = new Date(), from = new Date(to.getTime() - (days - 1) * 86400000);
  return Object.freeze({engine: $("perf-engine").value, model: $("model").value,
    from: opsDay(from), to: opsDay(to), origin: $("origin-filter").value,
    state: $("state-filter").value, slow: $("slow-filter").checked,
    epoch: $("native-epoch").value, cursor: nativeCursors[nativeIndex]});
}
function params(q, extras = {}) {
  return new URLSearchParams({from: q.from, to: q.to, ...extras}).toString();
}
function requestParams(q, extras = {}) {
  return params(q, {source: q.engine === "mlx" ? "mlx-perf" : "strata", ...(q.model ? {model: q.model} : {}), ...extras});
}
function tile(label, value, sub) {
  return el("div", {}, el("div", {class: "label", text: label}), el("div", {class: "hero-value", text: value}),
    sub ? el("div", {class: "label", text: sub}) : null);
}
function choices(selector, rows, current, empty) {
  const options = [{value: "", label: empty}, ...rows];
  if (current && !options.some(row => row.value === current)) options.push({value: current, label: current + " · no data in this range"});
  selector.replaceChildren(...options.map(row => el("option", {value: row.value, text: row.label})));
  selector.value = current;
}
async function section(id, gen, load, render) {
  const state = $(id);
  state.textContent = sectionSuccess[id] ? "Refreshing · previous values may be stale" : "Loading…";
  state.dataset.stale = "true";
  try {
    const data = await load();
    if (gen !== generation) return;
    render(data);
    sectionSuccess[id] = Date.now();
    state.textContent = "Updated " + fmt.time(sectionSuccess[id] / 1000);
    state.dataset.stale = "false";
  } catch (e) {
    if (gen !== generation) return;
    state.dataset.stale = "true";
    state.textContent = `${sectionSuccess[id] ? "Stale · last success " + fmt.time(sectionSuccess[id] / 1000) : "Unavailable"}: ${e.message}`;
  }
}
function drawSummary() {
  if (!lastSummary) return;
  const s = lastSummary;
  const bars = (p50, p90, format) => s.days.map(d => ({label: d.date,
    a: d[p50] ?? null, b: d[p50] == null || d[p90] == null ? null : Math.max(0, d[p90] - d[p50]),
    lines: [d.date, `median ${format(d[p50])}`, `p90 ${format(d[p90])}`, `${fmt.num(d.requests)} requests`]}));
  stackedBars($("ttft-chart"), bars("ttft_p50", "ttft_p90", secs));
  stackedBars($("decode-chart"), bars("decode_p50", "decode_p90", fmt.tok));
  stackedBars($("count-chart"), s.days.map(d => ({label: d.date,
    a: d.requests - d.errors - d.cancelled - d.incomplete, b: d.errors + d.cancelled + d.incomplete,
    lines: [d.date, `${fmt.num(d.requests)} requests`, `${fmt.num(d.errors)} errors`, `${fmt.num(d.cancelled)} cancelled`, `${fmt.num(d.incomplete)} incomplete`]})));
}
function renderSummary(s, q) {
  lastSummary = s;
  const t = s.totals;
  const label = q.engine === "strata" ? "Strata request metrics · prompt duration used as TTFT proxy" : "MLX server log · measured time to first token";
  $("perf-source").textContent = label;
  choices($("model"), s.models.map(m => ({value: m, label: m})), q.model, "All models (availability remains engine-wide)");
  $("perf-totals").replaceChildren(tile("Recorded requests", fmt.num(t.requests), `${fmt.num(t.errors)} errors · ${fmt.num(t.cancelled)} cancelled · ${fmt.num(t.incomplete)} incomplete`),
    tile(q.engine === "strata" ? "Prompt duration median / p90" : "TTFT median / p90", `${secs(t.ttft_p50)} / ${secs(t.ttft_p90)}`, `${fmt.num(t.slow)} slow (≥ ${s.slow_ttft_s} s)`),
    tile("Decode median / p90", `${fmt.tok(t.decode_p50)} / ${fmt.tok(t.decode_p90)}`, label),
    tile("Partial rows", fmt.num(t.partial), "Missing measurements stay unknown"));
  drawSummary();
}
function renderAvailability(a, q) {
  const label = q.engine === "splash" ? "Splash" : q.engine === "strata" ? "Strata" : "MLX";
  const segs = a?.segments || [], totals = a?.totals || {};
  const strip = $("avail-strip");
  const t0 = a?.range_start ?? Date.parse(q.from + "T00:00:00Z") / 1000 + offset * 60;
  const t1 = a?.range_end ?? Date.parse(q.to + "T00:00:00Z") / 1000 + offset * 60 + 86400;
  const span = Math.max(1, t1 - t0);
  strip.replaceChildren(...segs.map(x => el("span", {class: `st-${x.state}`, title: `${x.state}: ${opsTime(x.start)} – ${opsTime(x.end)}`,
    style: `left:${((x.start - t0) / span) * 100}%;width:${Math.max(0.1, ((x.end - x.start) / span) * 100)}%`})));
  $("avail-intervals").replaceChildren(...segs.map(x => el("li", {text: `${x.state}: ${opsTime(x.start)} – ${opsTime(x.end)}`})));
  $("avail-summary").textContent = a?.unsupported ? a.reason : `${label}: ${pctOrDash(totals.availability)} · observed ${pctOrDash(totals.coverage)} of the elapsed range (future time excluded)${a?.truncated ? " · sample history truncated" : ""}`;
  $("avail-definition").textContent = `${label} loaded serving time / observed time while intended. Off, other engines and maintenance are excluded; unobserved time never counts as uptime. ` +
    (q.engine === "splash" ? "Released and recovering are not serving. Target scopes use recorded profiles." : "MLX/Strata model selection affects requests and tokens; availability is engine-wide.");
  const observed = (a?.days || []).filter(d => d.observed_s > 0).reverse();
  $("avail-table").querySelector("tbody").replaceChildren(...(observed.length ? observed.map(d => el("tr", {},
    el("td", {text: d.date}), el("td", {class: "num", text: pctOrDash(d.availability)}), el("td", {class: "num", text: pctOrDash(d.coverage)}),
    el("td", {class: "num", text: hours(d.seconds.serving || 0)}), el("td", {class: "num", text: hours((d.seconds.down || 0) + (d.seconds.conflict || 0))}),
    el("td", {class: "num", text: hours((d.seconds.off || 0) + (d.seconds.comfyui || 0) + (d.seconds.strata || 0) + (d.seconds.maintenance || 0))}), el("td", {class: "num", text: hours(d.unknown_s)})))
    : [el("tr", {}, el("td", {colspan: 7, text: a?.reason || "No observed availability in this range. Historical gaps remain unknown."}))]));
}
function renderTokens(t, q) {
  $("token-source").textContent = q.engine === "strata" ? "Strata metrics · live + retained totals" : "MLX server logs · live + retained totals";
  lastTokens = t;
  const s = t.totals;
  $("token-totals").replaceChildren(tile("Input tokens", fmt.num(s.input_tokens), `${fmt.num(s.input_tokens - s.cached_tokens)} uncached`),
    tile("Cached", fmt.num(s.cached_tokens), `${share(s.cached_tokens, s.input_tokens)} of input${s.cached_unknown ? ` · unknown for ${fmt.num(s.cached_unknown)} requests` : ""}`),
    tile("Output tokens", fmt.num(s.output_tokens)), tile("Requests", fmt.num(s.requests), `${fmt.num(s.unmeasured)} unmeasured`));
  drawTokens();
  $("token-table").querySelector("tbody").replaceChildren(...(t.models.length ? t.models.map(m => el("tr", {},
    el("td", {text: m.model || "unknown"}), el("td", {class: "num", text: fmt.num(m.requests)}), el("td", {class: "num", text: fmt.num(m.input_tokens)}),
    el("td", {class: "num", text: fmt.num(m.cached_tokens)}), el("td", {class: "num", text: share(m.cached_tokens, m.input_tokens)}),
    el("td", {class: "num", text: fmt.num(m.output_tokens)}), el("td", {text: m.first}), el("td", {text: m.last})))
    : [el("tr", {}, el("td", {colspan: 8, text: "No engine token counts recorded in this range."}))]));
}
function drawTokens() {
  if (!lastTokens) return;
  stackedBars($("token-chart"), lastTokens.days.map(d => ({label: d.date,
    a: d.input_tokens - d.cached_tokens, b: d.cached_tokens, c: d.output_tokens,
    lines: [d.date, `input ${fmt.num(d.input_tokens)} (${fmt.num(d.cached_tokens)} cached)`, `output ${fmt.num(d.output_tokens)}`, `${fmt.num(d.requests)} requests`]})),
    {classes: ["bar-p", "bar-q", "bar-c"]});
}
function reqRow(r) {
  return el("tr", {}, el("td", {text: opsTime(r.started)}), el("td", {text: r.source === "strata" ? "Strata · TTFT = prompt duration" : "MLX · server log TTFT"}), el("td", {text: r.model || "unknown"}),
    el("td", {class: r.state === "completed" ? "" : "bad", text: r.state + (r.error ? ": " + r.error : "") + (r.complete ? "" : " (partial)")}),
    el("td", {class: "num", text: fmt.num(r.prompt_tokens)}), el("td", {class: "num", text: fmt.num(r.cached_tokens)}), el("td", {class: "num", text: fmt.num(r.generated_tokens)}),
    el("td", {class: "num", text: secs(r.ttft_s)}), el("td", {class: "num", text: fmt.tok(r.decode_tok_s)}), el("td", {class: "num", text: secs(r.elapsed_s)}),
    el("td", {}, r.source === "strata" ? "Native metrics" : el("button", {class: "link", text: "Log", onclick: () => showContext(r.id)})));
}
async function showContext(id) {
  const gen = generation;
  try {
    const c = await api(`/api/performance/requests/${id}/context`);
    if (gen !== generation) return;
    $("req-context").hidden = false; $("req-context").textContent = c.available ? c.lines.join("\n") : c.reason;
  } catch (e) { if (gen === generation) { $("req-context").hidden = false; $("req-context").textContent = e.message; } }
}
async function loadRequests(q, gen, append = false) {
  $("more").disabled = true;
  await section("requests-state", gen, () => api(`/api/performance/requests?${requestParams(q, {limit: 50,
    ...(q.state ? {state: q.state} : {}), ...(q.slow ? {slow: "true"} : {}), ...(append && nextBefore ? {before: nextBefore} : {})})}`), r => {
    nextBefore = r.next;
    const body = $("req-table").querySelector("tbody"), rows = r.requests.map(reqRow);
    if (append) body.append(...rows);
    else body.replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", {colspan: 11, text: "No requests recorded in this range."}))]));
    $("more").hidden = !r.next;
  });
  if (gen === generation) $("more").disabled = false;
}
function renderNative(data, q) {
  const c = data.coverage;
  const b = backendStatus?.engine?.backends?.splash;
  const label = p => {
    const target = b?.targets?.find(t => t.value === "splash:" + p);
    return target ? EngineUI.targetLabel(b, target) : p;
  };
  choices($("model"), [...new Set([...(data.profiles || []).map(p => p.profile), ...(b?.targets || []).map(t => t.value.slice("splash:".length))])].map(p => ({value: p, label: label(p)})), q.model, "All profiles");
  choices($("native-epoch"), (data.epochs || []).map(e => ({value: e.epoch, label: label(e.profile) + " · " + e.epoch.slice(0, 12)})), q.epoch, "Choose an epoch for latest counters");
  $("native-coverage").textContent = `Current collector health (independent of selected range): ${c.coverage} · last successful poll ${opsTime(c.last_poll)} · last new counters ${opsTime(c.last_new_data)}${c.stale ? " · stale" : ""}${c.error ? " · " + c.error : ""}. Reset/population qualification: ${c.counter_qualification || "unverified"}.`;
  $("native-latest").textContent = q.epoch && !q.cursor && data.readings.length ? `Latest snapshot in selected epoch: ${opsTime(data.readings[0].observed)} · completed ${fmt.num(data.readings[0].counters.completed)} · prefill execution tokens ${fmt.num(data.readings[0].counters.prefill_input_tokens)} · decode execution tokens ${fmt.num(data.readings[0].counters.decode_output_tokens)}. Cumulative, not range totals.` : "Select an epoch and its first page to inspect the latest cumulative counters. No period deltas are qualified.";
  $("native-readings").querySelector("tbody").replaceChildren(...(data.readings.length ? data.readings.map(r => el("tr", {},
    el("td", {text: opsTime(r.observed)}), el("td", {}, el("span", {text: label(r.profile)}), el("details", {}, el("summary", {text: "Identity"}), el("code", {text: r.profile + " · " + r.fingerprint}))),
    el("td", {}, el("details", {}, el("summary", {text: r.epoch.slice(0, 12)}), el("pre", {text: JSON.stringify(r.identity)}))),
    el("td", {text: fmt.num(r.counters.completed)}), el("td", {text: `${fmt.num(r.counters.cancelled)} / ${fmt.num(r.counters.failed)}`}),
    el("td", {text: fmt.num(r.counters.prefill_input_tokens)}), el("td", {text: fmt.num(r.counters.decode_output_tokens)})))
    : [el("tr", {}, el("td", {colspan: 7, text: "No attributed native snapshots in this range."}))]));
  $("native-availability").textContent = (c.gaps?.length ? "Known collector gaps: " + c.gaps.map(g => g.reason).join(", ") + ". " : "") + "Shutdown has no qualified final capture. Availability is shown above; no historical observations are backfilled.";
  nativeNext = data.next;
  $("native-older").disabled = !nativeNext;
  $("native-newer").disabled = nativeIndex === 0;
  $("native-page-info").textContent = `Page ${nativeIndex + 1} · ${data.readings.length} snapshots · limit ${data.limit || 20}${data.next ? " · older data available" : " · end of retained range"}`;
}
function resetNative() { nativeCursors = [null]; nativeIndex = 0; nativeNext = null; }
async function reload() {
  const gen = ++generation, q = querySnapshot();
  $("req-context").hidden = true;
  $("more").hidden = true;
  $("native-older").disabled = $("native-newer").disabled = true;
  $("perf-scope").textContent = `${q.from} through ${q.to} · calendar days in ${timezoneLabel}. Traffic filters apply only to tokens; state/slow filters only to requests. Availability is engine-wide for MLX/Strata and profile-scoped for Splash.`;
  const native = q.engine === "splash";
  for (const id of ["tokens", "requests-card", "request-charts"]) $(id).hidden = native;
  $("engine-diagnostics").hidden = !native;
  $("splash-coverage").hidden = !native;
  $("splash-coverage").textContent = "Complete request history is unsupported for Splash. Native execution counters and response usage when reported do not form a gateway or all-client request ledger.";
  if (native) {
    lastSummary = null; lastTokens = null;
    $("perf-source").textContent = "Splash · cumulative native execution snapshots";
    $("perf-totals").replaceChildren(tile("Request history", "Unsupported", "No fabricated period request, latency or token totals"));
    $("perf-summary-state").textContent = "Native collector health is in Engine diagnostics.";
  }
  const jobs = [];
  if (!native) {
    jobs.push(section("perf-summary-state", gen, () => api(`/api/performance/summary?${requestParams(q)}`), s => renderSummary(s, q)));
    jobs.push(section("tokens-state", gen, () => api(`/api/performance/tokens?${requestParams(q, q.origin ? {origin: q.origin} : {})}`), data => renderTokens(data, q)));
    jobs.push(loadRequests(q, gen));
  } else jobs.push(section("native-state", gen, () => api(`/api/performance/engines?${params(q, {limit: 20, ...(q.model ? {profile: q.model} : {}), ...(q.epoch ? {epoch: q.epoch} : {}), ...(q.cursor ? {before: q.cursor} : {})})}`), data => renderNative(data, q)));
  jobs.push(section("availability-state", gen, () => api(`/api/availability?${params(q, {source: q.engine, ...(native && q.model ? {profile: q.model} : {})})}`), a => renderAvailability(a, q)));
  await Promise.all(jobs);
}
document.querySelectorAll("[data-days]").forEach(b => b.addEventListener("click", () => {
  days = Number(b.dataset.days);
  document.querySelectorAll("[data-days]").forEach(o => o.setAttribute("aria-pressed", String(o === b)));
  resetNative(); reload();
}));
$("perf-engine").addEventListener("change", () => { engineChosen = true; $("model").value = ""; $("native-epoch").value = ""; resetNative(); reload(); });
$("model").addEventListener("change", () => { $("native-epoch").value = ""; resetNative(); reload(); });
for (const id of ["origin-filter", "state-filter", "slow-filter"]) $(id).addEventListener("change", reload);
$("native-epoch").addEventListener("change", () => { resetNative(); reload(); });
$("more").addEventListener("click", () => { if (!$("more").disabled && nextBefore) loadRequests(querySnapshot(), generation, true); });
$("native-older").addEventListener("click", () => { if (!nativeNext) return; nativeCursors[++nativeIndex] = nativeNext; reload(); });
$("native-newer").addEventListener("click", () => { if (!nativeIndex) return; nativeIndex--; reload(); });
window.addEventListener("resize", () => { drawSummary(); drawTokens(); });
onStatus(st => {
  backendStatus = st;
  if (!engineChosen && ["mlx", "strata", "splash"].includes(st.engine?.active)) {
    engineChosen = true; $("perf-engine").value = st.engine.active; $("model").value = ""; resetNative(); reload();
  }
});
api("/api/maintenance/log-rotation").then(r => {
  $("rotation-state").textContent = `Log rotation: daily at ${String(r.hour).padStart(2, "0")}:00 ${timezoneLabel}, logs over ${r.max_mib} MiB, ${r.keep} archives kept.`;
}).catch(e => { $("rotation-state").textContent = "Log rotation status unavailable: " + e.message; });
reload();
