"use strict";
// Quality runs: start/cancel, progress, latest compatibility, run details.
let lastStatus = null;
let roster = [];
let meta = null;
let activeTimer = null;

const OUTCOME_SEV = { pass: "ok", fail: "fail", unknown: "warn", unsupported: "" };
const RESTORE_TEXT = { restored: "restored", not_needed: "nothing to restore", failed: "FAILED",
  not_attempted: "not attempted" };

function unavailable() { return el("span", { class: "muted", title: "not reported", text: "unavailable" }); }
function metric(v, f) { return v === null || v === undefined ? unavailable() : f(v); }
const secs = (v) => `${Number(v).toFixed(v < 10 ? 2 : 1)} s`;

function confirmStart(title, body) {
  const dlg = document.getElementById("confirm");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").textContent = body;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.showModal();
  });
}

function currentName(st) { return st?.engine?.active === "mlx" ? st.selected : (roster.find((m) => m.selected) || {}).name; }

function effectText() {
  const st = lastStatus || {};
  const engine = st.engine?.active;
  const scope = document.getElementById("q-scope").value;
  const chosen = document.getElementById("q-model").value;
  const cur = currentName(st) || "the selected model";
  const parts = [];
  if (engine === "comfyui" || engine === "conflict") {
    return { text: "ComfyUI owns the GPU. Switch the engine to MLX or off first.", ok: false };
  }
  const n = scope === "all" ? roster.length : 1;
  const target = scope === "model" ? chosen : scope === "all" ? null : cur;
  if (engine === "mlx") {
    if (scope === "all") parts.push(`Switches through ${n} models one after another, then back to ${cur}. The API is down during each switch (about 30–90 s each).`);
    else if (target === cur) parts.push(`Runs on the loaded model (${cur}); nothing is switched.`);
    else parts.push(`Switches to ${target}, then back to ${cur}. The API is down during each switch.`);
  } else {
    parts.push(`MLX is off: the run starts it, then returns it to off with ${cur} selected.`);
  }
  parts.push("LiteLLM clients can still use the model during the run; pause them for clean numbers.");
  parts.push("Test chat and manual model actions are unavailable until the run ends or is cancelled.");
  return { text: parts.join(" "), ok: true, off: engine !== "mlx" };
}

function renderEffect() {
  const e = effectText();
  document.getElementById("q-effect").textContent = e.text;
  document.getElementById("quality-start").disabled = !e.ok || Boolean(lastStatus?.job);
  const st = lastStatus || {};
  document.getElementById("engine-now").textContent = st.engine?.active === "mlx" ? `MLX · ${st.selected}`
    : st.engine?.active === "none" ? "Engine off" : st.engine?.active || "";
}

async function start() {
  const kind = document.getElementById("q-kind").value;
  const scope = document.getElementById("q-scope").value;
  const body = { kind, scope };
  if (scope === "model") body.model = document.getElementById("q-model").value;
  const e = effectText();
  if (!(await confirmStart(`Start a ${kind} run?`, e.text))) return;
  if (e.off) body.confirm_off = true;
  try {
    const run = await api("/api/quality/runs", { method: "POST", json: body });
    watchRun(run.id);
    refreshStatus();
  } catch (err) { alert(err.message); }
}

function progressText(run) {
  const p = run.progress;
  if (!p) return "Starting…";
  const reps = meta ? meta.warmup + meta.repeats : null;
  const bits = [`Model ${p.index + 1} of ${p.total}: ${p.model}`];
  if (p.step === "load") bits.push("loading");
  else if (p.step) bits.push(`case ${p.step}`);
  if (p.rep !== undefined && reps) bits.push(p.rep < meta.warmup ? "warm-up" : `repeat ${p.rep - meta.warmup + 1} of ${meta.repeats}`);
  return bits.join(" · ");
}

function watchRun(id) {
  clearInterval(activeTimer);
  const card = document.getElementById("active-card");
  const tick = async () => {
    const run = await api(`/api/quality/runs/${encodeURIComponent(id)}`);
    card.hidden = false;
    document.getElementById("active-id").textContent = run.id;
    const badge = document.getElementById("active-status");
    badge.dataset.state = run.status; badge.textContent = run.status.replace("_", " ");
    document.getElementById("active-progress").textContent = run.status === "running"
      ? progressText(run) : `Finished: ${run.status.replace("_", " ")}; restoration ${RESTORE_TEXT[run.restoration?.outcome] || "—"}.`;
    document.getElementById("active-warning").textContent = run.warning || "";
    document.getElementById("active-cancel").hidden = run.status !== "running";
    document.getElementById("active-cancel").onclick = async () => {
      document.getElementById("active-cancel").disabled = true;
      try { await api(`/api/quality/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); } catch (e) { alert(e.message); }
    };
    if (run.status !== "running") {
      clearInterval(activeTimer); activeTimer = null;
      document.getElementById("active-cancel").disabled = false;
      loadRuns(); loadLatest(); showRun(run); refreshStatus();
    }
  };
  tick().catch(() => {});
  activeTimer = setInterval(() => tick().catch(() => {}), 2000);
}

async function loadLatest() {
  meta = await api("/api/quality/latest");
  const s = meta.settings;
  document.getElementById("q-params").textContent =
    `Streaming with usage, temperature ${s.temperature}, seed ${s.seed}, max ${s.max_tokens} output tokens, thinking ${s.enable_thinking ? "on" : "off"}. `
    + `${meta.warmup} warm-up (excluded) and ${meta.repeats} measured repeats per case; medians need at least 2 valid repeats. `
    + "Token counts and rates come from the server's usage report only; a run whose server counters cannot be read is marked uncontrolled and not compared.";
  document.querySelector("#q-cases tbody").replaceChildren(...meta.benchmark_cases.map((c) => el("tr", {},
    el("td", {}, el("code", { text: c.key })), el("td", { class: "num", text: `~${fmt.num(c.target_prompt_tokens)} tokens` }),
    el("td", { text: c.description }))));
  const cases = meta.compatibility_cases;
  const models = Object.keys(meta.compatibility);
  document.querySelector("#compat-table thead").replaceChildren(el("tr", {}, el("th", { text: "Model" }),
    ...cases.map((c) => el("th", { text: c.replaceAll("_", " ") }))));
  const rows = models.map((m) => el("tr", {}, el("td", {}, el("strong", { text: m })),
    ...cases.map((c) => {
      const r = meta.compatibility[m][c];
      return el("td", {}, r ? el("span", { class: "sev", "data-status": OUTCOME_SEV[r.status], title: r.detail || "", text: r.status })
        : el("span", { class: "muted", text: "—" }));
    })));
  document.querySelector("#compat-table tbody").replaceChildren(...(rows.length ? rows
    : [el("tr", {}, el("td", { class: "muted", colspan: cases.length + 1, text: "No compatibility run yet." }))]));
}

function runModels(r) { return r.scope === "model" ? r.model : r.scope === "all" ? "all" : "current"; }

async function loadRuns() {
  const { runs, active } = await api("/api/quality/runs?limit=30");
  document.querySelector("#runs-table tbody").replaceChildren(...(runs.length ? runs.map((r) => el("tr", {},
    el("td", { text: fmt.time(r.started) }), el("td", { text: r.kind }), el("td", { text: runModels(r) }),
    el("td", { class: r.status === "succeeded" ? "ok" : r.status === "running" || r.status === "cancelled" ? "" : "bad", text: r.status.replace("_", " ") }),
    el("td", { class: r.restoration?.outcome === "failed" || r.restoration?.outcome === "not_attempted" ? "bad" : "", text: RESTORE_TEXT[r.restoration?.outcome] || "—" }),
    el("td", { text: r.kind !== "benchmark" ? "—" : r.controlled === false ? "uncontrolled" : r.controlled ? "controlled" : "—" }),
    el("td", {}, el("button", { text: "View", onclick: async () => showRun(await api(`/api/quality/runs/${encodeURIComponent(r.id)}`)) }))))
    : [el("tr", {}, el("td", { colspan: 7, class: "muted", text: "No runs yet." }))]));
  if (active && !activeTimer) watchRun(active.run_id);
}

function baselineCell(summary) {
  if (!summary) return unavailable();
  if (!summary.controlled) return el("span", { class: "muted", text: "not compared (uncontrolled)" });
  if (!summary.complete) return el("span", { class: "muted", text: "not compared (incomplete)" });
  if (summary.cache_state === "unknown") return el("span", { class: "muted", text: "not compared (cache state unknown)" });
  if (!summary.baseline) return el("span", { class: "muted", text: "first comparable result" });
  if (!summary.regressions.length) return el("span", { class: "ok", text: `within 15% of ${fmt.time(summary.baseline.ts)}` });
  return el("div", {}, ...summary.regressions.map((g) => el("div", { class: "bad",
    text: `${g.metric.replaceAll("_", " ")} ${(g.change * 100).toFixed(0)}%` })));
}

function showRun(run) {
  document.getElementById("detail-card").hidden = false;
  document.getElementById("detail-id").textContent = run.id;
  const b = document.getElementById("detail-status");
  b.dataset.state = run.status; b.textContent = run.status.replace("_", " ");
  const rest = run.restoration || {};
  const facts = [
    ["Kind", run.kind], ["Models", runModels(run)], ["Started", fmt.time(run.started)], ["Finished", fmt.time(run.finished)],
    ["Started from", run.original ? (run.original.engine === "mlx" ? `MLX · ${run.original.name}` : `off (${run.original.name} selected)`) : "—"],
    ["Restoration", `${RESTORE_TEXT[rest.outcome] || "—"}${rest.detail ? ` — ${rest.detail}` : ""}`],
    ["Runtime", run.runtime_version ? `mlx-vlm ${run.runtime_version}` : "unavailable"],
    ["Settings fingerprint", run.runtime_fingerprint ? run.runtime_fingerprint.slice(0, 12) : "unavailable"],
    ["Case version", run.case_version || "—"],
  ];
  if (run.kind === "benchmark") facts.push(["Measurements", run.controlled === false ? "uncontrolled: server counters unreadable, regression checks off" : "controlled"]);
  document.getElementById("detail-facts").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  const err = document.getElementById("detail-error");
  err.hidden = !run.error; err.textContent = run.error || "";
  const bench = run.cases.filter((c) => c.kind === "benchmark");
  const compat = run.cases.filter((c) => c.kind === "compatibility");
  document.getElementById("detail-bench").hidden = !bench.length;
  document.getElementById("detail-compat").hidden = !compat.length;
  document.querySelector("#detail-bench tbody").replaceChildren(...bench.map((c) => {
    const med = c.summary?.medians || {};
    return el("tr", {},
      el("td", { text: c.model }), el("td", {}, el("code", { text: c.case_key })),
      el("td", { class: c.status === "complete" ? "ok" : "bad", title: c.detail || "", text: c.status }),
      el("td", { text: (c.cache_state || "—").replace("_", " ") }),
      el("td", { class: "num", text: c.summary ? `${c.summary.valid}` : "—" }),
      el("td", { class: "num" }, metric(med.ttft_s, secs)),
      el("td", { class: "num" }, metric(med.backend_prefill_tok_s, fmt.tok)),
      el("td", { class: "num" }, metric(med.backend_decode_tok_s, fmt.tok)),
      el("td", { class: "num", title: "client estimate from backend token counts" }, metric(med.client_decode_tok_s, fmt.tok)),
      el("td", { class: "num", title: "server process RSS (not total unified memory)" }, metric(med.peak_rss_bytes, fmt.bytes)),
      el("td", { class: "num", title: "MLX allocator peak reported by the server" }, metric(med.backend_peak_memory_gb, (v) => `${Number(v).toFixed(1)} GB`)),
      el("td", { class: "num" }, metric(c.load_s, secs)),
      el("td", {}, c.status === "complete" || c.status === "incomplete" ? baselineCell(c.summary) : el("span", { class: "muted", text: c.detail || "—" })));
  }));
  document.querySelector("#detail-compat tbody").replaceChildren(...compat.map((c) => el("tr", {},
    el("td", { text: c.model }), el("td", { text: c.case_key.replaceAll("_", " ") }),
    el("td", {}, el("span", { class: "sev", "data-status": OUTCOME_SEV[c.status] ?? "", text: c.status })),
    el("td", { class: "muted", text: c.detail || "" }))));
}

async function loadRoster() {
  const { models } = await api("/api/models");
  roster = models;
  const sel = document.getElementById("q-model");
  const keep = sel.value;
  sel.replaceChildren(...models.map((m) => el("option", { value: m.name, text: m.name })));
  if (keep && models.some((m) => m.name === keep)) sel.value = keep;
  renderEffect();
}

document.getElementById("q-scope").addEventListener("change", () => {
  document.getElementById("q-model-label").hidden = document.getElementById("q-scope").value !== "model";
  renderEffect();
});
document.getElementById("q-model").addEventListener("change", renderEffect);
document.getElementById("quality-start").addEventListener("click", start);
onStatus((st) => { lastStatus = st; renderEffect(); });
loadRoster().catch(() => {});
loadLatest().catch(() => {});
loadRuns().catch(() => {});
setInterval(() => loadRuns().catch(() => {}), 15000);
