"use strict";
// Task-quality evals on the Quality tab. Loaded after quality.js, which supplies confirmStart,
// watchRun, roster, lastStatus and refreshStatus. Model output and suite text only ever go
// through el({text}) / textContent.
let evalSuites = [];
let evalRunId = null;
const suiteCache = {};

const GRADE_SEV = { graded: "", needs_judge: "warn", judge_error: "warn", ungraded: "warn", failed: "fail" };
const GRADE_TEXT = { graded: "graded", needs_judge: "waiting for judge", judge_error: "judge error",
  ungraded: "ungraded", failed: "failed" };
const pct = (v) => (v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(0)}%`);
const interval = (c) => (c.mean === null || c.mean === undefined ? "—" : `${pct(c.mean)} (${pct(c.lo)}–${pct(c.hi)})`);

function showEvalError(id, msg) {
  const e = document.getElementById(id);
  e.hidden = !msg; e.textContent = msg || "";
}

function evalScopeBody() {
  const scope = document.getElementById("eval-scope").value;
  const body = { scope };
  if (scope === "model") body.model = document.getElementById("eval-model").value;
  return body;
}

function renderEvalForm() {
  const backend = EngineUI.active(lastStatus);
  const currentOnly = backend?.operations.includes("quality") && !backend.operations.includes("multi-model");
  const scope = document.getElementById("eval-scope");
  if (currentOnly && scope.value !== "current") scope.value = "current";
  for (const option of scope.options) option.disabled = currentOnly && option.value !== "current";
  document.getElementById("eval-model-label").hidden = scope.value !== "model";
  const policy = EngineUI.quality(lastStatus, "eval", scope.value);
  document.getElementById("eval-engine-note").textContent = policy.handled ? policy.text : "";
  document.getElementById("eval-start").disabled = (policy.handled && !policy.ok) || Boolean(lastStatus?.job) || !evalSuites.length;
  const thinking = document.getElementById("eval-thinking");
  if (!thinking.dataset.userSet) thinking.checked = EngineUI.reasoningDefault(lastStatus) === true;
  const suite = evalSuites.find((s) => s.id === document.getElementById("eval-suite").value);
  document.getElementById("eval-suite-note").textContent = suite
    ? `${suite.title}: ${suite.cases} cases, version ${suite.version}. Prompts and answers are stored with the run.` : "";
}

function populateModelSelect(sel, models, keep) {
  sel.replaceChildren(...models.map((m) => el("option", { value: m, text: m })));
  if (keep && models.includes(keep)) sel.value = keep;
}

async function loadEvalSuites() {
  const { suites } = await api("/api/evals/suites");
  evalSuites = suites;
  for (const id of ["eval-suite", "cmp-suite"]) {
    const sel = document.getElementById(id);
    const keep = sel.value;
    sel.replaceChildren(...suites.map((s) => el("option", { value: s.id, text: `${s.title} (${s.id})` })));
    if (keep && suites.some((s) => s.id === keep)) sel.value = keep;
  }
  document.querySelector("#eval-suites tbody").replaceChildren(...suites.map((s) => el("tr", {},
    el("td", {}, el("code", { text: s.id })), el("td", { text: s.title }), el("td", { class: "num", text: String(s.cases) }),
    el("td", { text: s.origin }),
    el("td", { class: "actions" },
      el("a", { href: `/api/evals/suites/${encodeURIComponent(s.id)}/export`, text: "Export" }),
      s.origin === "custom" ? el("button", { class: "danger", text: "Delete", onclick: () => deleteSuite(s) }) : null))));
  renderEvalForm();
}

async function deleteSuite(s) {
  if (!(await confirmStart(`Delete suite ${s.id}?`, "Stored results of this suite stay, but the suite can no longer be run.", "Delete"))) return;
  try { await api(`/api/evals/suites/${encodeURIComponent(s.id)}`, { method: "DELETE" }); } catch (e) { showEvalError("eval-error", e.message); }
  loadEvalSuites().catch(() => {});
}

async function importSuite() {
  const file = document.getElementById("eval-import-file").files[0];
  const id = document.getElementById("eval-import-id").value.trim();
  const title = document.getElementById("eval-import-title").value.trim();
  if (!file || !id || !title) { showEvalError("eval-error", "Choose a file and give the suite an id and a title."); return; }
  const fd = new FormData();
  fd.append("file", file); fd.append("id", id); fd.append("title", title);
  try {
    await api("/api/evals/suites", { method: "POST", body: fd });
    showEvalError("eval-error", "");
    document.getElementById("eval-import-file").value = "";
    await loadEvalSuites();
  } catch (e) { showEvalError("eval-error", e.message); }
}

async function startEval() {
  const suite = document.getElementById("eval-suite").value;
  const thinking = document.getElementById("eval-thinking").checked;
  const body = { ...evalScopeBody(), suite, thinking };
  showEvalError("eval-error", "");
  let text;
  try {
    const est = await api("/api/evals/estimate", { method: "POST", json: body });
    const per = est.models.map((m) => `${m.name}: about ${Math.max(1, Math.round(m.seconds / 60))} min${m.basis === "default" ? " (rough, no benchmark yet)" : ""}`);
    const s = evalSuites.find((x) => x.id === suite);
    const judge = est.judge === "none" ? "none (judged cases stay ungraded)" : est.judge;
    text = `${per.join("; ")}. Judge: ${judge}. `
      + `${s ? s.cases : "?"} cases per model. ${effectText("eval", body.scope, body.model).text}`;
  } catch (e) { showEvalError("eval-error", e.message); return; }
  if (!(await confirmStart("Start an eval run?", text))) return;
  const run = { kind: "eval", ...body };
  if (effectText("eval", body.scope, body.model).off) run.confirm_off = true;
  try {
    const started = await api("/api/quality/runs", { method: "POST", json: run });
    watchRun(started.id);
    refreshStatus();
  } catch (e) { showEvalError("eval-error", e.message); }
}

async function loadScorecard() {
  const { models } = await api("/api/evals/scorecard");
  const ids = [...new Set(models.flatMap((m) => Object.keys(m.suites)))].sort();
  document.querySelector("#eval-scorecard-table thead").replaceChildren(el("tr", {}, el("th", { text: "Model" }),
    // cells are keyed "<suite>" (thinking off) and "<suite>+thinking"
    ...ids.map((i) => el("th", { text: i.endsWith("+thinking") ? `${i.slice(0, -9)} (thinking on)` : i })), el("th", { class: "num", text: "Decode" }), el("th", { class: "num", text: "Prefill" }),
    el("th", { class: "num", text: "Peak RSS" })));
  const rows = models.map((m) => el("tr", {}, el("td", {}, el("strong", { text: m.model })),
    ...ids.map((i) => {
      const c = m.suites[i];
      if (!c) return el("td", { class: "muted", text: "—" });
      const note = `${c.n} graded${c.excluded ? `, ${c.excluded} excluded` : ""}, thinking ${c.thinking ? "on" : "off"}`
        + `${c.suite_version ? `, v${c.suite_version}` : `, suite ${c.suite_hash.slice(0, 8)}`}${c.judge ? `, judge ${c.judge}` : ""}${c.self_judged ? " (self-judged)" : ""}`;
      const cats = Object.entries(c.by_category || {}).map(([k, v]) => `${k} ${pct(typeof v === "object" ? v.mean : v)}`).join(", ");
      return el("td", { title: cats }, interval(c), el("div", { class: "muted", text: note }));
    }),
    el("td", { class: "num", text: m.speed ? fmt.tok(m.speed.decode_tok_s) : "—" }),
    el("td", { class: "num", text: m.speed ? fmt.tok(m.speed.prefill_tok_s) : "—" }),
    el("td", { class: "num", text: m.speed && m.speed.peak_rss_gb !== null ? `${m.speed.peak_rss_gb} GB` : "—" })));
  document.querySelector("#eval-scorecard-table tbody").replaceChildren(...(rows.length ? rows
    : [el("tr", {}, el("td", { class: "muted", colspan: 4, text: "No eval scored yet." }))]));
}

let evalRuns = [];
async function loadEvalRuns() {
  const { runs } = await api("/api/quality/runs?limit=200");
  evalRuns = runs.filter((r) => r.kind === "eval");
  document.querySelector("#eval-runs tbody").replaceChildren(...(evalRuns.length ? evalRuns.map((r) => el("tr", {},
    el("td", { text: fmt.time(r.started) }), el("td", { text: runModels(r) }),
    el("td", { class: r.status === "succeeded" ? "ok" : r.status === "running" || r.status === "cancelled" ? "" : "bad", text: r.status.replace("_", " ") }),
    el("td", {}, el("button", { text: "Results", onclick: () => showEvalResults(r.id) }))))
    : [el("tr", {}, el("td", { colspan: 4, class: "muted", text: "No eval runs yet." }))]));
  for (const id of ["cmp-a-run", "cmp-b-run"]) {
    const sel = document.getElementById(id);
    const keep = sel.value;
    sel.replaceChildren(...evalRuns.map((r) => el("option", { value: r.id, text: `${fmt.time(r.started)} · ${runModels(r)}` })));
    if (keep && evalRuns.some((r) => r.id === keep)) sel.value = keep;
  }
  await Promise.all([fillCompareModels("a"), fillCompareModels("b")]);
}

async function fillCompareModels(side) {
  const run = document.getElementById(`cmp-${side}-run`).value;
  const sel = document.getElementById(`cmp-${side}-model`);
  if (!run) { sel.replaceChildren(); return; }
  try {
    const { results } = await api(`/api/evals/runs/${encodeURIComponent(run)}/results`);
    populateModelSelect(sel, [...new Set(results.map((r) => r.model))], sel.value);
  } catch (e) { sel.replaceChildren(); }
}

async function suiteCases(id) {
  if (!suiteCache[id]) {
    const s = await api(`/api/evals/suites/${encodeURIComponent(id)}`);
    suiteCache[id] = Object.fromEntries(s.cases.map((c) => [c.id, c]));
  }
  return suiteCache[id];
}

function promptText(c) {
  if (!c) return "(case no longer in the current suite version)";
  const parts = [];
  if (c.system) parts.push(`[system] ${c.system}`);
  if (c.prompt !== null && c.prompt !== undefined) parts.push(c.prompt);
  for (const m of c.messages || []) parts.push(`[${m.role}] ${m.content}`);
  return parts.join("\n\n");
}

function detailRow(r, c) {
  const bits = [
    ["Prompt", promptText(c)],
    ["Response", r.response ?? ""],
  ];
  if (r.tool_calls) bits.push(["Tool calls", r.tool_calls]);
  if (r.grade_detail) bits.push(["Grade detail", r.grade_detail]);
  if (r.error) bits.push(["Error", r.error]);
  bits.push(["Run", `finish ${r.finish_reason || "—"}, ${r.prompt_tokens ?? "—"} prompt / ${r.completion_tokens ?? "—"} completion tokens, `
    + `${r.reasoning_chars || 0} reasoning chars, ${r.latency_s === null || r.latency_s === undefined ? "—" : secs(r.latency_s)}`
    + `${r.judge ? `, judge ${r.judge}${r.self_judged ? " (self-judged)" : ""}` : ""}`]);
  return el("tr", { hidden: true }, el("td", { colspan: 6 },
    ...bits.flatMap(([k, v]) => [el("strong", { text: k }), el("pre", { class: "log wrap", text: v })])));
}

async function showEvalResults(runId) {
  const card = document.getElementById("eval-results");
  let results;
  try {
    ({ results } = await api(`/api/evals/runs/${encodeURIComponent(runId)}/results`));
  } catch (e) {
    card.hidden = false;
    document.getElementById("eval-results-note").textContent = `Could not load results for ${runId}: ${e.message}`;
    return;
  }
  evalRunId = runId;
  card.hidden = false;
  document.getElementById("eval-results-id").textContent = runId;
  const body = document.getElementById("eval-results-body");
  document.getElementById("eval-results-note").textContent = results.length ? "" : "No results were recorded for this run.";
  const out = [];
  for (const model of [...new Set(results.map((r) => r.model))]) {
    const rows = results.filter((r) => r.model === model);
    const suiteId = rows[0].suite_id;
    let cases = {};
    try { cases = await suiteCases(suiteId); } catch (e) { /* suite deleted: prompts unavailable */ }
    const counts = {};
    for (const r of rows) counts[r.grade_state] = (counts[r.grade_state] || 0) + 1;
    const trs = rows.flatMap((r) => {
      const d = detailRow(r, cases[r.case_id]);
      const main = el("tr", {},
        el("td", {}, el("code", { text: r.case_id })), el("td", { text: r.category }),
        el("td", {}, el("span", { class: "sev", "data-status": GRADE_SEV[r.grade_state] ?? "", title: r.grade_detail || "", text: GRADE_TEXT[r.grade_state] || r.grade_state })),
        el("td", { class: "num", text: r.score === null || r.score === undefined ? "—" : pct(r.score) }),
        el("td", { text: r.finish_reason || (r.error ? "error" : "—") }),
        el("td", {}, el("button", { text: "Details", onclick: () => { d.hidden = !d.hidden; } })));
      return [main, d];
    });
    out.push(el("h3", { text: `${model} · ${suiteId}` }),
      el("p", { class: "muted", text: Object.entries(counts).map(([k, v]) => `${v} ${GRADE_TEXT[k] || k}`).join(", ") }),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ...["Case", "Category", "Grade", "Score", "Finish", ""].map((h) => el("th", { text: h })))),
        el("tbody", {}, ...trs))));
  }
  body.replaceChildren(...out);
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function regrade() {
  if (!evalRunId) return;
  try {
    const r = await api(`/api/evals/runs/${encodeURIComponent(evalRunId)}/regrade`, { method: "POST" });
    document.getElementById("eval-results-note").textContent = `Regraded ${r.regraded} cases; ${r.changed} changed.`;
    await showEvalResults(evalRunId);
    document.getElementById("eval-results-note").textContent = `Regraded ${r.regraded} cases; ${r.changed} changed.`;
    loadScorecard().catch(() => {});
  } catch (e) { document.getElementById("eval-results-note").textContent = e.message; }
}

async function compare() {
  const q = new URLSearchParams({
    suite: document.getElementById("cmp-suite").value,
    a_run: document.getElementById("cmp-a-run").value, a_model: document.getElementById("cmp-a-model").value,
    b_run: document.getElementById("cmp-b-run").value, b_model: document.getElementById("cmp-b-model").value,
  });
  showEvalError("cmp-error", "");
  const out = document.getElementById("cmp-result");
  try {
    const r = await api(`/api/evals/compare?${q}`);
    out.hidden = false;
    document.getElementById("cmp-verdict").textContent = `A is ${r.verdict === "no clear difference" ? "not clearly different from" : r.verdict + " than"} B`;
    document.getElementById("cmp-detail").textContent = `mean difference ${(r.mean_diff * 100).toFixed(1)} points (95% ${(r.lo * 100).toFixed(1)} to ${(r.hi * 100).toFixed(1)}) over ${r.n} shared cases; `
      + `${r.wins} wins, ${r.losses} losses, ${r.ties} ties.`
      + (r.self_judged ? ` Self-judged: ${["a", "b"].filter((k) => r.self_judged[k]).map((k) => k.toUpperCase()).join(" and ")}.` : "");
    document.querySelector("#cmp-disagree tbody").replaceChildren(...r.disagreements.map((d) => el("tr", {},
      el("td", {}, el("code", { text: d.case_id })), el("td", { class: "num", text: pct(d.a_score) }), el("td", { class: "num", text: pct(d.b_score) }))));
  } catch (e) { out.hidden = true; showEvalError("cmp-error", e.message); }
}

document.getElementById("eval-scope").addEventListener("change", renderEvalForm);
document.getElementById("eval-suite").addEventListener("change", renderEvalForm);
document.getElementById("eval-start").addEventListener("click", startEval);
document.getElementById("eval-import").addEventListener("click", importSuite);
document.getElementById("eval-regrade").addEventListener("click", regrade);
document.getElementById("cmp-go").addEventListener("click", compare);
document.getElementById("cmp-a-run").addEventListener("change", () => fillCompareModels("a"));
document.getElementById("cmp-b-run").addEventListener("change", () => fillCompareModels("b"));
onStatus(() => { renderEvalForm(); });
// the model picker reuses quality.js's roster, which loads a moment after this script
setInterval(() => {
  const sel = document.getElementById("eval-model");
  const names = roster.map((m) => m.name);
  if (sel.options.length !== names.length) populateModelSelect(sel, names, sel.value);
}, 1000);
const refreshEvals = () => { loadEvalRuns().catch(() => {}); loadScorecard().catch(() => {}); };
loadEvalSuites().catch(() => {});
refreshEvals();
setInterval(refreshEvals, 15000);

document.getElementById("eval-thinking").addEventListener("change", (ev) => { ev.target.dataset.userSet = "true"; });
