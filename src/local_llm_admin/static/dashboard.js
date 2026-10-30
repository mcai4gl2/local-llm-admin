"use strict";
let windowSel = "15m";

onStatus(st => {
  const backend = EngineUI.active(st), mlx = backend?.operations.includes("multi-model");
  const active = st.engine?.[backend?.id] || {}, metrics = active.metrics || {};
  const h = mlx ? st.health || {} : {}, inf = mlx ? st.inference || {} : {};
  document.getElementById("st-selected").textContent = EngineUI.label(st);
  document.getElementById("st-state").textContent = EngineUI.badge(st);
  const decode = mlx ? inf.last_decode_rate : metrics.tok_s;
  const prefill = mlx ? inf.last_prefill_rate : metrics.prompt_tok_s;
  document.getElementById("st-decode").textContent = fmt.tok(decode);
  document.getElementById("st-prefill").textContent = fmt.tok(prefill);
  document.getElementById("decode-label").textContent = mlx ? "Last request decode" : "Live decode";
  document.getElementById("prefill-label").textContent = mlx ? "Last request prefill" : "Mean prompt rate";
  document.getElementById("st-decode").parentElement.hidden = !mlx && decode == null;
  document.getElementById("st-prefill").parentElement.hidden = !mlx && prefill == null;
  document.getElementById("status-checked").textContent = (EngineUI.fresh(st) ? "checked " : "stale · last check ") + fmt.time(st.checked_at);
  const context = EngineUI.contexts(st);
  const loaded = context.loaded;
  const facts = [["Active engine", EngineUI.label(st)],
    ["Loaded model", mlx ? h.loaded_model || "Unverified" : active.model || "Unverified"],
    ["Configured context", EngineUI.context(context.configured) || "Not reported"],
    ["Loaded context", (EngineUI.context(loaded) || "Unknown") + " · " + context.state],
    ["Process", backend?.running ? `running (pid ${mlx ? st.process?.pid ?? "?" : active.pid ?? "?"})` : "stopped"],
    ...(mlx ? [["Requests in flight", inf.in_flight_count ?? (inf.in_flight ? "yes" : "0")],
      ["Last time to first token", inf.last_ttft_s == null ? "—" : `${inf.last_ttft_s.toFixed(2)} s`]] : []),
    ...(metrics.queued != null ? [["Queued", fmt.num(metrics.queued)]] : []),
    ["Admin job", st.job ? `${st.job.action} ${st.job.name || ""} (running)` : "none"]];
  const mdl = st.model || {};
  const details = mlx ? [["Tool parser", h.loaded_tool_parser || "—"], ["Last request", inf.last_request_at || "—"],
    ["Last generated tokens", fmt.num(inf.last_generated_tokens)], ["Architecture", mdl.architecture || "—"],
    ["Quantization", mdl.quant_bits ? `${mdl.quant_bits}-bit` : "—"], ["Model position embeddings", fmt.num(mdl.max_position_embeddings)],
    ["Weights on disk", fmt.bytes(mdl.size_bytes)], ["Drive", st.drive_mounted ? "mounted" : "not mounted"],
    ["mlx-model lock", st.lock ? `held for ${Math.round((st.lock_age_s || 0) / 60)} min` : "free"]] : [];
  document.getElementById("st-details").hidden = !details.length;
  for (const [id, rows] of [["st-facts", facts], ["st-detail-facts", details]]) document.getElementById(id).replaceChildren(...rows.map(([k, v]) => el("div", {}, el("dt", {text: k}), el("dd", {text: v}))));
  const access = document.getElementById("access-error");
  access.hidden = !st.access_error; access.textContent = st.access_error || "";
});

function tile(label, value, sub, pct) {
  return el("div", { class: "tile" }, el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
    sub ? el("div", { class: "sub", text: sub }) : null,
    pct !== undefined && pct !== null ? el("div", { class: "meter", role: "meter", "aria-valuenow": Math.round(pct), "aria-valuemin": 0, "aria-valuemax": 100 },
      el("span", { style: `width:${Math.min(100, Math.max(0, pct))}%` })) : null);
}

async function refreshMetrics() {
  const m = await api("/api/metrics/current");
  const tiles = [
    tile("CPU", fmt.pct(m.cpu_percent), `load ${m.load.map((l) => l.toFixed(2)).join(" · ")} · ${m.cpu_per_core.length} cores`, m.cpu_percent),
    tile("Memory", fmt.pct(m.mem_percent), `${fmt.bytes(m.mem_used)} of ${fmt.bytes(m.mem_total)}${m.mem_wired ? ` · wired ${fmt.bytes(m.mem_wired)}` : ""}`, m.mem_percent),
    tile("Memory pressure", m.pressure ? m.pressure : "—", `swap ${fmt.bytes(m.swap_used)}`),
  ];
  if (m.gpu_util !== null) tiles.push(tile("GPU", fmt.pct(m.gpu_util), "Device utilisation", m.gpu_util));
  for (const d of m.disks) {
    tiles.push(tile(`Disk ${d.path}`, d.percent === null ? "—" : fmt.pct(d.percent),
      d.total === null ? (d.mounted ? "unreadable" : "not mounted") : `${fmt.bytes(d.used)} of ${fmt.bytes(d.total)}`, d.percent));
  }
  tiles.push(tile("Disk I/O", fmt.rate(m.disk_read_bps), `write ${fmt.rate(m.disk_write_bps)}`));
  tiles.push(tile("Network", fmt.rate(m.net_rx_bps), `send ${fmt.rate(m.net_tx_bps)}`));
  const bundle = m.engine_bundle;
  if (bundle && bundle.source) tiles.push(tile("Engine bundle · " + bundle.source + " · summed RSS",
    bundle.rss === null ? bundle.state : fmt.bytes(bundle.rss),
    `Sum of process RSS · ${bundle.members.length} members · CPU ${fmt.pct(bundle.cpu_percent)}; shared pages may be counted twice`));
  tiles.push(tile("Primary engine process RSS", m.mlx ? fmt.bytes(m.mlx.rss) : bundle && bundle.state !== "absent" ? "unavailable" : "not running", m.mlx ? `CPU ${fmt.pct(m.mlx.cpu_percent)}` : ""));
  document.getElementById("metric-tiles").replaceChildren(...tiles);
}

async function refreshHistory() {
  const selectedWindow = windowSel;
  const { points } = await api(`/api/metrics/history?window=${selectedWindow}`);
  if (selectedWindow !== windowSel) return;
  lineChart(document.getElementById("chart-cpu"), points.map((p) => ({ t: p.ts, v: p.cpu_percent })), { max: 100 });
  lineChart(document.getElementById("chart-mem"), points.map((p) => ({ t: p.ts, v: p.mem_percent })), { max: 100 });
  const gpu = points.some((p) => p.gpu_util !== null);
  document.getElementById("gpu-figure").hidden = !gpu;
  if (gpu) lineChart(document.getElementById("chart-gpu"), points.map((p) => ({ t: p.ts, v: p.gpu_util })), { max: 100 });
}

document.querySelectorAll("[data-window]").forEach((b) => b.addEventListener("click", () => {
  windowSel = b.dataset.window;
  document.querySelectorAll("[data-window]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  trackSection("history-panel", refreshHistory);
}));

async function refreshProblems() {
  const { checks } = await api("/api/diagnostics");
  const bad = checks.filter((c) => c.status !== "ok");
  document.getElementById("problems-card").hidden = bad.length === 0;
  document.getElementById("problems").replaceChildren(...bad.map((c) => checkRow(c, refreshProblems)));
}

function tick() { trackSection("metric-tiles", refreshMetrics); trackSection("history-panel", refreshHistory); }
tick();
setInterval(tick, 5000);
trackSection("problems-card", refreshProblems);
setInterval(() => trackSection("problems-card", refreshProblems), 30000);
