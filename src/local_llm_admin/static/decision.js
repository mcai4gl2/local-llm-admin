"use strict";
// Decision test page (M6): build typed questions, read them through the admin, show probabilities.
const $ = (id) => document.getElementById(id);
let running = false;
let busy = false;

const PRESETS = {
  ticket: {
    label: "Support ticket routing",
    state: JSON.stringify({ ticket: "My card was charged twice for the October invoice and the app shows a sync error in billing settings. Please refund the duplicate.", customer_tier: "pro" }, null, 2),
    questions: [
      { id: "is_billing", type: "noul", instructions: "Is this mainly a billing issue?" },
      { id: "department", type: "choice", instructions: "Which team should handle it?", criteria: "billing: Payments, invoices, refunds\ntechnical: Bugs, errors, crashes\naccount: Login, profile, permissions\nother: Anything else" },
      { id: "urgency", type: "score", instructions: "How urgent is it?", criteria: "low\nnormal\nhigh\ncritical" },
    ],
  },
  logline: {
    label: "Is this log line an error?",
    state: "2026-10-01 07:13:33,911 - INFO - Request completed: endpoint=/chat/completions stream=True",
    questions: [
      { id: "is_error", type: "noul", instructions: "Does this log line report an error or failure?" },
      { id: "severity", type: "choice", instructions: "How severe is it?", criteria: "none: Routine information\nwarning: Something to watch\nerror: A failed request or crash" },
    ],
  },
};

const HINT = {
  noul: "Answers P(yes).",
  choice: "One option per line: name, or name: description (2–26 options).",
  score: "One level per line, lowest first (2–10 levels).",
};

function syncType(fs) {
  const type = fs.querySelector(".q-type").value;
  fs.querySelector(".q-criteria-label").hidden = type === "noul";
  fs.querySelector(".q-criteria-label").firstChild.textContent = type === "score" ? "Levels " : "Options ";
  fs.querySelector(".q-hint").textContent = HINT[type];
}

function addQuestion(q = {}) {
  const fs = $("question-tpl").content.firstElementChild.cloneNode(true);
  const n = $("questions").children.length + 1;
  fs.querySelector(".q-id").value = q.id || `q${n}`;
  fs.querySelector(".q-type").value = q.type || "noul";
  fs.querySelector(".q-instructions").value = q.instructions || "";
  fs.querySelector(".q-criteria").value = q.criteria || "";
  fs.querySelector(".q-type").addEventListener("change", () => syncType(fs));
  fs.querySelector(".q-remove").addEventListener("click", () => fs.remove());
  syncType(fs);
  $("questions").append(fs);
}

function parseState(text) {
  const t = text.trim();
  if (t.startsWith("{") || t.startsWith("[")) {
    try { return JSON.parse(t); } catch (e) { /* plain text that starts with a bracket */ }
  }
  return t;
}

function buildBody() {
  const questions = {};
  for (const fs of $("questions").children) {
    const id = fs.querySelector(".q-id").value.trim();
    const type = fs.querySelector(".q-type").value;
    const q = { type };
    const ins = fs.querySelector(".q-instructions").value.trim();
    if (ins) q.instructions = ins;
    const lines = fs.querySelector(".q-criteria").value.split("\n").map((l) => l.trim()).filter(Boolean);
    if (type === "choice") {
      q.criteria = {};
      for (const l of lines) {
        const i = l.indexOf(":");
        if (i > 0) q.criteria[l.slice(0, i).trim()] = l.slice(i + 1).trim();
        else q.criteria[l] = "";
      }
    } else if (type === "score") {
      q.criteria = lines;
    }
    if (id in questions) throw new Error(`Question id "${id}" is used twice.`);
    questions[id] = q;
  }
  if (!Object.keys(questions).length) throw new Error("Add at least one question.");
  const body = { state: parseState($("state").value), model: "openjev-latest", questions };
  if ($("steps").value) body.steps = Number($("steps").value);
  if ($("samples").value) body.samples = Number($("samples").value);
  return body;
}

function probRows(a) {
  let entries = Object.entries(a.probs);
  if (a.type === "score") entries.sort((x, y) => Number(x[0]) - Number(y[0]));
  else entries.sort((x, y) => y[1] - x[1]);
  return entries.map(([k, p]) => {
    const label = a.type === "score" && a.legend ? `${k} · ${a.legend[k] ?? ""}` : k;
    return el("div", { class: `prob${k === a.top ? " best" : ""}` },
      el("span", { class: "prob-label", text: label }),
      el("div", { class: "meter" }, el("span", { style: `width:${(p * 100).toFixed(1)}%` })),
      el("span", { class: "num", text: `${(p * 100).toFixed(1)}%` }));
  });
}

function renderAnswers(out) {
  const blocks = out.answers.map((a) => {
    if (a.invalid) {
      return el("div", { class: "answer" }, el("h3", { text: a.id }),
        el("p", { class: "error", text: "The sidecar returned an invalid distribution; no numbers are shown." }));
    }
    const summary = a.type === "noul" ? `P(yes) ${(a.probs.yes * 100).toFixed(1)}%`
      : a.type === "score" ? `score ${Number(a.expected).toFixed(2)} · top ${a.top}`
        : `top ${a.top}`;
    const conf = a.confidence === null || a.confidence === undefined ? "" : ` · model confidence ${a.confidence.toFixed(2)}`;
    return el("div", { class: "answer" },
      el("div", { class: "card-head" }, el("h3", { text: a.id }), el("span", { class: "muted", text: `${a.type} · ${summary}${conf}` })),
      ...probRows(a));
  });
  $("answers").replaceChildren(...(blocks.length ? blocks : [el("p", { class: "muted", text: "No answers." })]));
  const st = out.server_timing || {};
  const parts = [`${fmt.num(Math.round(out.latency_ms))} ms`];
  if (st.total !== undefined) parts.push(`sidecar ${fmt.num(Math.round(st.total))} ms`);
  parts.push(`${fmt.num(out.usage?.input_tokens)} input tokens`);
  $("read-meta").textContent = parts.join(" · ");
}

const ms = (v) => (v === null || v === undefined ? "—" : `${fmt.num(Math.round(v))} ms`);

async function loadHistory() {
  const h = await api("/api/decision/history?days=14");
  const a = h.availability;
  $("hist-availability").textContent = a.ratio === null ? "no availability samples yet"
    : `available ${(a.ratio * 100).toFixed(1)}% of the time it was meant to run (${h.days} days)`;
  const days = h.daily.slice().reverse().map((d) => el("tr", {}, el("td", { text: d.day }),
    el("td", { class: "num", text: fmt.num(d.requests) }), el("td", { class: "num", text: fmt.num(d.errors) }),
    el("td", { class: "num", text: ms(d.p50_ms) }), el("td", { class: "num", text: ms(d.p95_ms) }),
    el("td", { class: "num", text: fmt.num(d.input_tokens) })));
  $("hist-daily").querySelector("tbody").replaceChildren(...(days.length ? days
    : [el("tr", {}, el("td", { colspan: 6, class: "muted", text: "No reads yet." }))]));
  const recent = h.recent.map((r) => el("tr", {}, el("td", { text: fmt.time(r.ts) }), el("td", { text: r.source }),
    el("td", { class: "num", text: fmt.num(r.n_questions) }), el("td", { text: r.types || "—" }),
    el("td", { class: "num", text: ms(r.latency_ms) }),
    el("td", { class: r.status === "ok" ? "ok" : "", text: r.status === "ok" ? "ok" : `error ${r.error_code ?? ""}` })));
  $("hist-recent").querySelector("tbody").replaceChildren(...recent);
}

function syncRun() { $("run").disabled = !running || busy; }

async function run(ev) {
  ev.preventDefault();
  if (!running || busy) return;
  $("read-error").hidden = true;
  let body;
  try { body = buildBody(); } catch (e) { $("read-error").textContent = e.message; $("read-error").hidden = false; return; }
  busy = true; syncRun(); $("read-meta").textContent = "Reading…";
  try {
    renderAnswers(await api("/api/decision/read", { method: "POST", json: body }));
  } catch (e) {
    $("read-error").textContent = e.message; $("read-error").hidden = false; $("read-meta").textContent = "";
  } finally {
    busy = false; syncRun();
    trackSection("hist-daily", loadHistory);
  }
}

async function copyCurl() {
  let body;
  try { body = buildBody(); } catch (e) { alert(e.message); return; }
  const json = JSON.stringify(body).replace(/'/g, "'\\''");
  const cmd = "curl -s http://127.0.0.1:8081/v1/systemone \\\n"
    + "  -H \"Authorization: Bearer $OPENJEV_API_KEY\" -H 'Content-Type: application/json' \\\n"
    + `  -d '${json}'`;
  try { await navigator.clipboard.writeText(cmd); $("read-meta").textContent = "curl command copied (run it on the Mac; the key stays in openjev.env)."; }
  catch (e) { prompt("Copy the command:", cmd); }
}

function loadPreset(name) {
  const p = PRESETS[name];
  if (!p) return;
  $("state").value = p.state;
  $("questions").replaceChildren();
  p.questions.forEach(addQuestion);
}

for (const [k, p] of Object.entries(PRESETS)) $("preset").append(el("option", { value: k, text: p.label }));
$("preset").addEventListener("change", (e) => loadPreset(e.target.value));
$("add-question").addEventListener("click", () => addQuestion());
$("read-form").addEventListener("submit", run);
$("copy-curl").addEventListener("click", copyCurl);
watchService("/api/decision", cfg => {
  running = serviceNotice($("decision-disabled"), cfg).ok;
  syncRun();
});
$("preset").value = "ticket";
loadPreset("ticket");
syncRun();
trackSection("hist-daily", loadHistory);
