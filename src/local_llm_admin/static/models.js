"use strict";
let pollTimer = null;

const DEFAULT_CONFIRM = document.getElementById("confirm-body").textContent;

function confirmAction(title, body = DEFAULT_CONFIRM) {
  const dlg = document.getElementById("confirm");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").textContent = body;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.showModal();
  });
}

async function runAction(path, body, title) {
  if (!(await confirmAction(title))) return;
  try {
    const job = await api(path, { method: "POST", json: body });
    showJob(job);
    pollJob(job.id);
  } catch (e) {
    alert(e.message);
  }
}

function showJob(job) {
  document.getElementById("job-card").hidden = false;
  document.getElementById("job-title").textContent = `${job.action} ${job.name || ""}`;
  const s = document.getElementById("job-status");
  s.dataset.state = job.status; s.textContent = job.status;
  const out = document.getElementById("job-output");
  out.textContent = job.output || "";
  out.scrollTop = out.scrollHeight;
}

function pollJob(id) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    showJob(job);
    if (job.status !== "running") {
      clearInterval(pollTimer); pollTimer = null; userPicked = false;
      loadModels(); loadHistory(); loadInventory(true).catch(() => {}); loadComfy().catch(() => {}); refreshStatus();
      loadSidecars().catch(() => {});
      loadServices().catch(() => {});
    }
  }, 1000);
}

const STATE_TEXT = { complete: "complete", downloading: "downloading", incomplete: "incomplete",
  "no-weights": "no weights", unknown: "unreadable index" };

function weightsCell(w) {
  if (!w) return el("span", { class: "bad", text: "dir missing" });
  const cls = w.state === "complete" ? "ok" : w.state === "downloading" ? "" : "bad";
  const pct = w.progress === null ? "" : ` ${Math.floor(w.progress * 100)}%`;
  return el("div", {},
    el("span", { class: cls, text: `${STATE_TEXT[w.state] || w.state}${w.state === "complete" ? "" : pct}` }),
    w.state === "downloading" && w.progress !== null
      ? el("div", { class: "meter" }, el("span", { style: `width:${w.progress * 100}%` })) : null,
    w.missing_shards ? el("div", { class: "muted", text: `${w.missing_shards} shard(s) not started` }) : null);
}

async function registerDir(d) {
  const dlg = document.getElementById("register-dialog");
  document.getElementById("reg-dir").textContent = d.dir_name;
  document.getElementById("reg-name").value = d.suggested_name;
  dlg.showModal();
  await new Promise((resolve) => dlg.addEventListener("close", resolve, { once: true }));
  if (dlg.returnValue !== "ok") return;
  try {
    const job = await api("/api/models/register", { method: "POST",
      json: { dir_name: d.dir_name, name: document.getElementById("reg-name").value.trim() } });
    showJob(job); pollJob(job.id);
  } catch (e) { alert(e.message); }
}

function renderDisk(discovered) {
  document.getElementById("disk-checked").textContent = `checked ${new Date().toLocaleTimeString()}`;
  const rows = discovered.map((d) => el("tr", {},
    el("td", {}, el("code", { text: d.dir_name })),
    el("td", {}, weightsCell(d)),
    el("td", { class: "num", text: d.expected_bytes && d.state !== "complete"
      ? `${fmt.bytes(d.present_bytes)} / ${fmt.bytes(d.expected_bytes)}` : fmt.bytes(d.present_bytes) }),
    el("td", { text: [d.architecture, d.quant_bits ? `${d.quant_bits}-bit` : null].filter(Boolean).join(" · ") || (d.has_config ? "—" : "no config.json") }),
    el("td", {}, d.registered_as ? el("strong", { text: d.registered_as }) : el("span", { class: "muted", text: "not registered" })),
    el("td", { class: "actions" }, d.registered_as ? null : el("button", {
      disabled: d.state !== "complete" || !d.has_config,
      title: d.state !== "complete" ? "Register once the download is complete" : null,
      onclick: () => registerDir(d), text: "Register" }))));
  document.querySelector("#disk-table tbody").replaceChildren(...(rows.length ? rows :
    [el("tr", {}, el("td", { colspan: 6, class: "muted", text: "No model directories (is the drive mounted?)" }))]));
}


function checksCell(results, profileFp) {
  if (!results) return el("span", { class: "muted", text: "not checked" });
  const ran = new Set(Object.values(results).map((r) => r.profile_fingerprint).filter(Boolean));
  const stale = ran.size > 0 && !(ran.size === 1 && ran.has(profileFp));
  const fails = Object.entries(results).filter(([, r]) => r.status === "fail");
  const passes = Object.values(results).filter((r) => r.status === "pass").length;
  return el("div", {},
    el("span", { class: "sev", "data-status": fails.length ? "fail" : "ok",
      title: Object.entries(results).map(([k, r]) => `${k}: ${r.status}`).join("\n"),
      text: fails.length ? `${fails.length} failing` : `${passes} passing` }),
    ...fails.map(([k]) => el("span", { class: "pill", text: k.replaceAll("_", " ") })),
    stale ? el("span", { class: "pill", title: "checked with other launch settings; re-run on the Quality page", text: "stale" }) : null);
}

async function loadModels() {
  const { models, discovered } = await api("/api/models");
  const compat = await api("/api/quality/latest").then((r) => r.compatibility).catch(() => ({}));
  engineModels = models;
  renderEngineOptions();
  renderDisk(discovered);
  const rows = models.map((m) => el("tr", {},
    el("td", {}, el("strong", { text: m.name }), m.selected ? el("span", { class: "pill", text: "selected" }) : null),
    el("td", {}, el("code", { text: m.dir })),
    el("td", { class: "num", text: fmt.bytes(m.size_bytes) }),
    el("td", {}, weightsCell(m.weights),
      m.dir_exists && !m.has_config ? el("div", { class: "bad", text: "no config.json" }) : null),
    el("td", {}, checksCell(compat[m.name], m.profile_fingerprint)),
    el("td", { class: "actions" },
      el("button", { onclick: () => openProfile(m.name), text: "Settings", title: `profile ${m.profile_fingerprint}` }),
      el("button", { onclick: () => removeModel(m.name), text: "Remove", disabled: m.selected }),
      el("button", { disabled: !m.dir_exists || !m.has_config || m.weights?.state !== "complete" || !EngineUI.canUseModelActions(lastStatus), onclick: () => runAction("/api/models/switch", { name: m.name }, `Switch to ${m.name}?`), text: m.selected ? "Restart" : "Switch" }))));
  document.querySelector("#models-table tbody").replaceChildren(...(rows.length ? rows :
    [el("tr", {}, el("td", { colspan: 6, class: "muted", text: "No models (is the drive mounted?)" }))]));
}

async function removeModel(name) {
  let p;
  try { p = await api(`/api/models/${encodeURIComponent(name)}/remove-preview`); } catch (e) { alert(e.message); return; }
  const dlg = document.getElementById("remove-dialog");
  const del = document.getElementById("rm-delete");
  const confirmBox = document.getElementById("rm-confirm");
  document.getElementById("rm-name").textContent = name;
  document.getElementById("rm-facts").replaceChildren(...[
    ["Directory", p.canonical || p.dir], ["Size", fmt.bytes(p.size_bytes)],
    ["Also registered as", p.aliases.join(", ") || "—"],
  ].map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  del.checked = false; confirmBox.value = "";
  const sync = () => {
    const refusals = del.checked ? p.delete_refusals : p.unregister_refusals;
    document.getElementById("rm-refusals").replaceChildren(...refusals.map((r) => el("li", { text: r })));
    document.getElementById("rm-confirm-label").hidden = !del.checked;
    document.getElementById("rm-ok").disabled = refusals.length > 0 || (del.checked && confirmBox.value !== name);
  };
  del.onchange = sync; confirmBox.oninput = sync; sync();
  dlg.showModal();
  await new Promise((resolve) => dlg.addEventListener("close", resolve, { once: true }));
  if (dlg.returnValue !== "ok") return;
  try {
    const job = await api("/api/models/remove", { method: "POST",
      json: { name, delete_files: del.checked, confirmation: del.checked ? confirmBox.value : null } });
    showJob(job); pollJob(job.id);
  } catch (e) { alert(e.message); }
}

async function loadInventory(refresh = false) {
  const inv = await api(`/api/models/inventory${refresh ? "?refresh=true" : ""}`);
  document.getElementById("inv-checked").textContent = inv.available ? `checked ${fmt.time(inv.checked_at)}` : inv.reason;
  if (!inv.available) return;
  const d = inv.disk;
  document.getElementById("inv-disk").replaceChildren(...[
    ["Free on the model drive", d ? `${fmt.bytes(d.free_bytes)} of ${fmt.bytes(d.total_bytes)}` : "—"],
    ["Request log covers", inv.requests_since ? `since ${fmt.time(inv.requests_since)}` : "no logged requests"],
  ].map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  document.getElementById("inv-note").textContent = `${inv.token_attribution} "First observed" is when the admin first saw the directory, not its download date.`;
  document.querySelector("#inv-table tbody").replaceChildren(...inv.models.map((m) => el("tr", {},
    el("td", {}, el("strong", { text: m.name }), m.loaded ? el("span", { class: "pill", text: "loaded" }) : m.selected ? el("span", { class: "pill", text: "selected" }) : null,
      m.aliases.length ? el("div", { class: "muted", text: `same weights as ${m.aliases.join(", ")}` }) : null),
    el("td", { class: "num", text: fmt.bytes(m.size_bytes) }),
    el("td", {}, m.fingerprint ? el("code", { title: "file names and sizes plus config/index content", text: m.fingerprint.slice(5, 13) }) : el("span", { class: "muted", text: m.exists ? "—" : "dir missing" })),
    el("td", { text: evalCell(m.eval) }),
    el("td", { text: m.downloaded_at ? `downloaded ${fmt.time(m.downloaded_at)}` : fmt.time(m.first_observed) }),
    el("td", { text: fmt.time(m.last_activated) }),
    el("td", { class: "num", text: m.requests ? fmt.num(m.requests.count) : "—" }),
    el("td", { class: "num", text: m.requests ? fmt.num((m.requests.prompt_tokens || 0) + (m.requests.generated_tokens || 0)) : "—",
      title: m.requests ? `input ${fmt.num(m.requests.prompt_tokens)} (${fmt.num(m.requests.cached_tokens)} cached) · output ${fmt.num(m.requests.generated_tokens)}` : "" },
      m.requests?.cached_tokens ? el("div", { class: "muted", text: `${fmt.num(m.requests.cached_tokens)} cached` }) : null))));
}

function evalCell(ev) {
  // thinking-off scores only: the API leaves out "<suite>+thinking" cells
  const parts = ["core-v1", "writing-v1"].filter((id) => ev && typeof ev[id] === "number").map((id) => `${id} ${ev[id].toFixed(2)}`);
  return parts.length ? parts.join(" · ") : "—";
}

async function loadHistory() {
  const { history } = await api("/api/history?limit=100");
  document.querySelector("#history-table tbody").replaceChildren(...history.map((h) => el("tr", {},
    el("td", { text: fmt.time(h.ts) }), el("td", { text: h.action }), el("td", { text: h.from_name || "—" }),
    el("td", { text: h.to_name || "—" }), el("td", { class: ["succeeded", "observed"].includes(h.result) ? "ok" : "bad", text: h.result }),
    el("td", { class: "num", text: h.duration_s === null ? "—" : `${h.duration_s.toFixed(0)} s` }), el("td", { text: h.source }))));
}

document.getElementById("btn-start").addEventListener("click", () => runAction("/api/models/start", {}, "Start the selected model?"));
document.getElementById("btn-stop").addEventListener("click", () => runAction("/api/models/stop", undefined, "Stop the model server?"));
let lastStatus = null;
let engineModels = [];
let userPicked = false;  // keep the dropdown choice only if the user made one

function engineLabel(st) { return EngineUI.label(st); }

// 131072 → 128K; effective contexts are presented only when reported.
const contextK = (n) => EngineUI.context(n);

function renderEngineOptions() {
  const sel = document.getElementById("engine-target");
  const current = userPicked ? sel.value : null;
  const options = EngineUI.options(lastStatus, engineModels);
  sel.replaceChildren(...options.map(t => el("option", {value: t.value, text: t.label})));
  sel.value = current && options.some(t => t.value === current)
    ? current : EngineUI.defaultTarget(lastStatus, engineModels);
}

async function applyEngine() {
  const target = document.getElementById("engine-target").value;
  const prompt = EngineUI.prompt(lastStatus, target);
  if (!(await confirmAction(prompt.title, prompt.body))) return;
  try {
    const job = await setEngineWithForce(target, confirmAction);
    if (job) { showJob(job); pollJob(job.id); }
  } catch (e) { alert(e.message); }
}

function renderBackendCards(st) {
  const container = document.getElementById("backend-cards");
  const cards = Object.values(st.engine?.backends || {}).filter(b => b.enabled
    && b.operations.includes("profiles") && !document.getElementById(b.id + "-card"));
  container.replaceChildren(...cards.map(b => {
    const status = st.engine?.[b.id] || {};
    const target = b.targets.find(t => t.value === b.id + ":" + b.reference) || {};
    const facts = [["Profile", b.reference || "—"], ["Model", status.model || "—"],
      ["Checkpoint", target.cohort || "—"],
      ["Context", EngineUI.context(status.max_context || target.max_context) || "—"],
      ["Reasoning", target.reasoning ? "On by default" : "—"], ["Usage", "unavailable"]];
    return el("section", {class: "card"},
      el("div", {class: "card-head"}, el("h2", {text: b.label}),
        el("span", {class: "badge", "data-state": b.id + "-" + b.state, text: EngineUI.badge(st.engine.active === b.id ? st : {engine: {active: b.id, backends: st.engine.backends}})})),
      el("p", {class: "muted", text: b.note || ""}),
      el("dl", {class: "facts"}, ...facts.map(([key, value]) => el("div", {}, el("dt", {text: key}), el("dd", {text: value})))));
  }));
}

// Strata card: from /api/status; a field Strata does not report shows "—"
function renderStrata(st) {
  const card = document.getElementById("strata-card");
  card.hidden = !st.engine?.strata_enabled;
  const s = st.engine?.strata;
  if (card.hidden || !s) return;
  const m = s.metrics || {};
  const badge = document.getElementById("strata-state");
  badge.dataset.state = !s.running ? "stopped" : s.ready ? "strata" : "strata-starting";
  badge.textContent = !s.running ? "Stopped" : s.ready ? (m.state || "Loaded") : "Loading";
  const dash = (v, f = String) => (v === null || v === undefined ? "—" : f(v));
  const facts = [
    ["Model", dash(s.model)], ["Pack", dash(s.pack)], ["Context", dash(s.max_context, fmt.compact)],
    ["Queued", dash(m.queued)], ["Live decode", fmt.tok(m.tok_s)],
    ["Requests", dash(m.requests)], ["Mean prompt", fmt.tok(m.prompt_tok_s)],
    ["Mean decode", fmt.tok(m.decode_tok_s)], ["PID", dash(s.pid)],
  ];
  document.getElementById("strata-facts").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
}

async function loadComfy() {
  const c = await api("/api/comfyui");
  document.getElementById("comfy-link").href = c.public_url;
  const facts = [
    ["State", c.running ? (c.ready ? `running (pid ${c.pid})` : "starting") : "stopped"],
    ["Version", c.version || "—"], ["Device", c.device || "—"],
    ["Memory free", c.vram_total ? `${fmt.bytes(c.vram_free)} of ${fmt.bytes(c.vram_total)}` : "—"],
    ["Queue", c.queue_running === null ? "—" : `${c.queue_running} running · ${c.queue_pending} pending`],
    ["Output", `${fmt.num(c.output.files)} files · ${fmt.bytes(c.output.bytes)}`],
  ];
  document.getElementById("comfy-facts").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  const rows = c.weights.map((w) => el("tr", {}, el("td", { text: w.category }), el("td", {}, el("code", { text: w.file })),
    el("td", { class: w.state === "complete" ? "ok" : "", text: w.state }), el("td", { class: "num", text: fmt.bytes(w.size_bytes) })));
  document.querySelector("#comfy-weights tbody").replaceChildren(...(rows.length ? rows
    : [el("tr", {}, el("td", { colspan: 4, class: "muted", text: "No weights yet under comfyui/models/" }))]));
}

// --- ComfyUI queue (M5) -------------------------------------------------------------
const Q_POLL_MS = 3000;
const Q_STALE_S = 15;
const Q_RESULT = { removed: "removed", not_pending: "no longer pending", running: "running (use Cancel)",
  started: "started before it could be removed", still_pending: "still pending", unconfirmed: "sent, not confirmed" };
let queueSnap = null;
let queueTimer = null;
let queueBusy = false;  // a mutation from this page is in flight

function comfyUp(st) { return st?.engine?.active === "comfyui" && st.engine.comfyui?.ready; }

function queueTime(ts) { return ts ? fmt.time(ts) : "—"; }

function renderQueue(snap, error) {
  const panel = document.getElementById("queue-panel");
  const stateEl = document.getElementById("q-state");
  const stale = !!error || !snap || snap.state !== "ok" || (Date.now() / 1000 - snap.checked_at) > Q_STALE_S;
  panel.dataset.stale = String(stale);
  if (error) stateEl.textContent = `unreachable: ${error}${snap ? ` (last read ${fmt.clock(snap.checked_at)})` : ""}`;
  else if (snap.state === "stopped") stateEl.textContent = "ComfyUI is not running";
  else if (snap.state === "unknown") stateEl.textContent = `unknown: ${snap.reason}`;
  else stateEl.textContent = `${snap.running_count} running · ${snap.pending_count} pending${snap.unparsed ? ` · ${snap.unparsed} unreadable` : ""}${snap.truncated ? " · list truncated" : ""} · ${fmt.clock(snap.checked_at)}`;
  const ok = !error && snap?.state === "ok";
  const locked = !ok || queueBusy || snap.job_running;
  const atomic = snap?.capabilities?.per_job_cancel === true;
  const running = ok ? snap.running : [];
  const pending = ok ? snap.pending : [];
  document.querySelector("#q-running tbody").replaceChildren(...(running.length ? running.map((r) => el("tr", {},
    el("td", {}, el("code", { text: r.id.slice(0, 8), title: r.id })), el("td", { text: queueTime(r.created) }),
    el("td", { class: "num", text: fmt.num(r.nodes) }),
    el("td", {}, el("button", { class: "danger", disabled: locked,
      title: atomic ? "Stops this generation only if it is still the one running" : "This ComfyUI has no per-job cancel: checks, then interrupts",
      onclick: () => cancelRunning(r.id), text: atomic ? "Cancel" : "Interrupt" }))))
    : [el("tr", {}, el("td", { colspan: 4, class: "muted", text: ok ? "Nothing running" : "—" }))]));
  const checked = new Set([...document.querySelectorAll("#q-pending tbody input:checked")].map((i) => i.value));
  document.querySelector("#q-pending tbody").replaceChildren(...(pending.length ? pending.map((r) => el("tr", {},
    el("td", {}, el("input", { type: "checkbox", value: r.id, checked: checked.has(r.id), disabled: locked, "aria-label": `Select ${r.id}` })),
    el("td", {}, el("code", { text: r.id.slice(0, 8), title: r.id })), el("td", { class: "num", text: r.number ?? "—" }),
    el("td", { text: queueTime(r.created) }), el("td", { class: "num", text: fmt.num(r.nodes) })))
    : [el("tr", {}, el("td", { colspan: 5, class: "muted", text: ok ? "Nothing pending" : "—" }))]));
  document.getElementById("q-all").disabled = locked || !pending.length;
  document.getElementById("q-delete").disabled = locked || !pending.length;
  document.getElementById("q-clear").disabled = locked || !pending.length;
  document.getElementById("q-interrupt").disabled = locked || !running.length;
  if (ok && snap.job_running) stateEl.textContent += " · an engine job is running";
}

async function loadQueue() {
  try {
    queueSnap = await api("/api/comfyui/queue");
    renderQueue(queueSnap, null);
  } catch (e) {
    renderQueue(queueSnap, e.message);
  }
}

function syncQueuePolling(st) {
  const up = comfyUp(st);
  document.getElementById("queue-panel").hidden = !st?.engine?.comfyui?.running;
  if (up && !queueTimer) { loadQueue(); queueTimer = setInterval(loadQueue, Q_POLL_MS); }
  if (!up && queueTimer) { clearInterval(queueTimer); queueTimer = null; }
  if (!up && st?.engine?.comfyui?.running) loadQueue();  // show "unknown" once, no polling
}

function summarize(results) {
  const counts = {};
  for (const v of Object.values(results || {})) counts[v] = (counts[v] || 0) + 1;
  return Object.entries(counts).map(([k, n]) => `${n} ${Q_RESULT[k] || k}`).join(", ") || "nothing to do";
}

async function queueMutation(path, body, describe) {
  const result = document.getElementById("q-result");
  queueBusy = true; renderQueue(queueSnap, null);
  try {
    const out = await api(path, { method: "POST", json: { ...body, session: queueSnap?.session } });
    result.textContent = describe(out);
  } catch (e) {
    result.textContent = e.message;
  } finally {
    queueBusy = false;
    await loadQueue();
  }
}

async function cancelRunning(id) {
  const atomic = queueSnap?.capabilities?.per_job_cancel === true;
  const body = atomic ? "Only this generation stops; pending work continues. ComfyUI stops it at its next checkpoint (between nodes, or between steps in samplers), so a long step can finish first."
    : "This ComfyUI cannot cancel by id atomically: if the generation finishes just now, the next one could be interrupted instead.";
  if (!(await confirmAction(`Stop generation ${id.slice(0, 8)}?`, body))) return;
  queueMutation("/api/comfyui/queue/cancel", { id }, (o) => ({ cancelled: "Cancel sent. ComfyUI stops the generation at its next checkpoint.", not_running: "It was no longer running.", interrupt_requested: "Interrupt sent. ComfyUI stops at its next checkpoint." }[o.result] || o.result));
}

document.getElementById("q-all").addEventListener("change", (ev) => {
  for (const box of document.querySelectorAll("#q-pending tbody input")) box.checked = ev.target.checked;
});
document.getElementById("q-delete").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#q-pending tbody input:checked")].map((i) => i.value);
  if (!ids.length) { document.getElementById("q-result").textContent = "Select pending items first."; return; }
  if (!(await confirmAction(`Remove ${ids.length} pending item(s)?`, "Items that already started are left running."))) return;
  document.getElementById("q-all").checked = false;
  queueMutation("/api/comfyui/queue/delete", { ids }, (o) => `Remove: ${summarize(o.results)}.`);
});
document.getElementById("q-clear").addEventListener("click", async () => {
  if (!(await confirmAction("Clear pending work?", "Removes every item pending right now. The running generation continues."))) return;
  queueMutation("/api/comfyui/queue/clear", {}, (o) => `Clear: ${summarize(o.results)}.`);
});
document.getElementById("q-interrupt").addEventListener("click", async () => {
  const expected = (queueSnap?.running || []).map((r) => r.id);
  if (!(await confirmAction("Interrupt the running generation?",
    "This is ComfyUI's global interrupt: it stops whatever is running when it arrives. The admin checks the running item first, but another ComfyUI client can still start work in between. Pending work continues."))) return;
  queueMutation("/api/comfyui/interrupt", { expected_running: expected }, (o) => (o.result === "interrupted" ? "Interrupt sent. ComfyUI stops at its next checkpoint." : "Nothing was running."));
});

onStatus((st) => {
  const engineChanged = lastStatus?.engine?.active !== st.engine?.active;
  lastStatus = st;
  renderEngineOptions();
  renderBackendCards(st);
  if (engineChanged) {  // redraw what depends on the engine: buttons, default target, card
    renderEngineOptions();
    loadModels().catch(() => {});
    loadComfy().catch(() => {});
  }
  const cur = document.getElementById("engine-current");
  cur.dataset.state = st.state; cur.textContent = engineLabel(st);
  renderStrata(st);
  syncQueuePolling(st);
});
document.getElementById("engine-apply").addEventListener("click", applyEngine);
document.getElementById("engine-target").addEventListener("change", () => { userPicked = true; });

onStatus((st) => { if (st.job && !pollTimer) { showJob(st.job); pollJob(st.job.id); } });
loadModels();
loadHistory();
loadInventory().catch(() => {});
setInterval(() => loadInventory().catch(() => {}), 60000);
setInterval(() => loadModels().catch(() => {}), 10000);
loadComfy().catch(() => {});
setInterval(() => loadComfy().catch(() => {}), 10000);

// --- sidecar slot (M7) --------------------------------------------------------------
const SIDECAR_PAGE = { openjev: "/decision", "qwen-tts": "/tts" };
const SIDECAR_LOAD = { openjev: "It loads DiffusionGemma (~20 GB) next to the running model.", "qwen-tts": "It loads Qwen3-TTS (~3 GB, up to ~7 GB while speaking) next to the running model." };
// today's traffic, from each sidecar's own history route
const SIDECAR_TODAY = { openjev: ["Reads today", "/api/decision/history?days=1"], "qwen-tts": ["Speech today", "/api/tts/history?days=1"] };
let sidecars = null;

async function loadSidecars() {
  if (!document.getElementById("sidecar-card")) return;
  sidecars = await api("/api/sidecars");
  const sel = document.getElementById("sidecar-select");
  if (!sel.options.length) {
    const running = sidecars.sidecars.find((s) => s.running);
    for (const s of sidecars.sidecars) sel.append(el("option", { value: s.name, text: s.title }));
    if (running) sel.value = running.name;
  }
  const s = sidecars.sidecars.find((x) => x.name === sel.value) || sidecars.sidecars[0];
  document.getElementById("sidecar-page").href = SIDECAR_PAGE[s.name] || "#";
  document.getElementById("sidecar-conflict").hidden = !sidecars.conflict;
  const state = s.running ? (s.ready ? `running (pid ${s.pid})` : "starting") : "stopped";
  const facts = [
    ["State", state], ["Memory (RSS)", fmt.bytes(s.rss_bytes)], ["Weights", s.weights || "—"],
    ["Listens on", s.listen], ["Allowed MLX models", s.allowed_mlx.join(", ") || "none"],
  ];
  const today = SIDECAR_TODAY[s.name];
  if (today) {
    const day = (await api(today[1]).catch(() => null))?.daily?.at(-1);
    facts.push([today[0], day ? `${fmt.num(day.requests)} · p50 ${day.p50_ms === null ? "—" : `${Math.round(day.p50_ms)} ms`}` : "none"]);
  }
  document.getElementById("sidecar-facts").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  const b = s.budget;
  const lines = s.running ? [] : [...b.lines, b.ok ? "A start passes the memory check now." : "A start would be refused:"];
  document.getElementById("sidecar-budget").replaceChildren(...lines.map((t) => el("li", { text: t })));
  const refusal = document.getElementById("sidecar-refusal");
  refusal.hidden = s.running || b.ok;
  refusal.textContent = b.reasons.join("; ");
  const jobBusy = Boolean(lastStatus?.job);
  document.getElementById("sidecar-start").disabled = s.running || !b.ok || jobBusy || sidecars.conflict;
  document.getElementById("sidecar-stop").disabled = !s.running || jobBusy;
}

function sidecarRefused(e) {
  const refusal = document.getElementById("sidecar-refusal");
  refusal.textContent = e.message; refusal.hidden = false;
}

async function sidecarStart() {
  const name = document.getElementById("sidecar-select").value;
  const s = sidecars.sidecars.find((x) => x.name === name);
  const other = sidecars.sidecars.find((x) => x.running && x.name !== name);
  const title = other ? `Stop ${other.title} and start ${s.title}?` : `Start ${s.title}?`;
  const body = (other ? `${other.title} stops first; its LAN clients get 503 until it is started again. ` : "")
    + `${SIDECAR_LOAD[name] || ""} While it works, the chat model generates more slowly.`;
  if (!(await confirmAction(title, body))) return;
  try {
    const job = await api(`/api/sidecars/${encodeURIComponent(name)}/start`, { method: "POST", json: { confirm: Boolean(other) } });
    showJob(job); pollJob(job.id);
  } catch (e) { sidecarRefused(e); }
}

async function sidecarStop() {
  const name = document.getElementById("sidecar-select").value;
  const s = sidecars.sidecars.find((x) => x.name === name);
  if (!(await confirmAction(`Stop ${s.title}?`, "The test page and LAN clients fail until it is started again."))) return;
  try {
    const job = await api(`/api/sidecars/${encodeURIComponent(name)}/stop`, { method: "POST" });
    showJob(job); pollJob(job.id);
  } catch (e) { sidecarRefused(e); }
}

if (document.getElementById("sidecar-card")) {
  document.getElementById("sidecar-select").addEventListener("change", () => loadSidecars().catch(() => {}));
  document.getElementById("sidecar-start").addEventListener("click", sidecarStart);
  document.getElementById("sidecar-stop").addEventListener("click", sidecarStop);
  loadSidecars().catch(() => {});
  setInterval(() => loadSidecars().catch(() => {}), 10000);
}

// --- embedding service (M8) ---------------------------------------------------------
let services = null;

async function loadServices() {
  if (!document.getElementById("embed-card")) return;
  services = await api("/api/services");
  const s = services.services.find((x) => x.name === "qwen-embed");
  if (!s) return;
  const state = s.running ? (s.ready ? `running (pid ${s.pid})` : "starting")
    : s.paused ? "paused while ComfyUI runs" : s.intent === "running" ? "down" : "stopped";
  const facts = [
    ["State", state], ["Intent", s.intent], ["Models", s.models.join(" · ")],
    ["Memory (RSS)", fmt.bytes(s.rss_bytes)], ["Weights", s.weights || "—"], ["Listens on", s.listen],
    ["Allowed MLX models", s.allowed_mlx.join(", ") || "none"],
  ];
  const day = (await api("/api/embed/history?days=1").catch(() => null))?.daily?.at(-1);
  facts.push(["Requests today", day ? `${fmt.num(day.requests)} · p50 ${day.p50_ms === null ? "—" : `${Math.round(day.p50_ms)} ms`}` : "none"]);
  document.getElementById("embed-facts").replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  const b = s.budget;
  const lines = s.running ? [] : [...b.lines, b.ok ? "A start passes the memory check now." : "A start would be refused:"];
  document.getElementById("embed-budget").replaceChildren(...lines.map((t) => el("li", { text: t })));
  const refusal = document.getElementById("embed-refusal");
  refusal.hidden = s.running || b.ok;
  refusal.textContent = b.reasons.join("; ");
  const jobBusy = Boolean(lastStatus?.job);
  document.getElementById("embed-start").disabled = s.running || !b.ok || jobBusy;
  document.getElementById("embed-stop").disabled = !s.running || jobBusy;
}

async function embedAction(action) {
  const start = action === "start";
  const title = start ? "Start the embeddings service?" : "Stop the embeddings service?";
  const body = start
    ? "It loads Qwen3-Embedding-4B and Qwen3-Reranker-0.6B (~7 GB) next to the running model and stays on until you stop it. The chat model is slower while it embeds."
    : "Cortex and other LAN clients get 503 until it is started again. It is not restarted automatically.";
  if (!(await confirmAction(title, body))) return;
  try {
    const job = await api(`/api/services/qwen-embed/${action}`, { method: "POST" });
    showJob(job); pollJob(job.id);
  } catch (e) {
    const r = document.getElementById("embed-refusal");
    r.textContent = e.message; r.hidden = false;
  }
}

if (document.getElementById("embed-card")) {
  document.getElementById("embed-start").addEventListener("click", () => embedAction("start"));
  document.getElementById("embed-stop").addEventListener("click", () => embedAction("stop"));
  loadServices().catch(() => {});
  setInterval(() => loadServices().catch(() => {}), 10000);
}

// --- downloads (M3b) ---------------------------------------------------------------
let dlPreview = null;
const DL_STATE = { starting: "starting", running: "downloading", publishing: "publishing", published: "published",
  failed: "failed", cancelled: "cancelled", interrupted: "interrupted" };

function dlProgress(r) {
  if (r.state === "published") return el("span", { class: "ok", text: fmt.bytes(r.bytes_total) });
  if (!r.bytes_total) return el("span", { class: "muted", text: r.bytes_done ? fmt.bytes(r.bytes_done) : "—" });
  const frac = Math.min(1, (r.bytes_done || 0) / r.bytes_total);
  return el("div", {}, el("span", { text: `${fmt.bytes(r.bytes_done || 0)} / ${fmt.bytes(r.bytes_total)} · ${r.files_done || 0}/${r.files_total} files` }),
    ["running", "publishing"].includes(r.state) ? el("div", { class: "meter" }, el("span", { style: `width:${frac * 100}%` })) : null);
}

async function dlAction(path, what) {
  try { await api(path, { method: "POST" }); } catch (e) { alert(`${what}: ${e.message}`); }
  loadDownloads().catch(() => {});
}

async function loadDownloads() {
  const { runs, allowed_owners } = await api("/api/downloads/runs");
  document.getElementById("dl-owners").textContent = `allowed owners: ${allowed_owners}`;
  const rows = runs.map((r) => el("tr", {},
    el("td", { text: fmt.time(r.created) }),
    el("td", {}, el("code", { text: r.repo }), el("div", { class: "muted", text: r.revision.slice(0, 12) })),
    el("td", {}, el("code", { text: r.dest })),
    el("td", {}, el("span", { class: r.state === "published" ? "ok" : ["failed", "interrupted"].includes(r.state) ? "bad" : "", text: DL_STATE[r.state] || r.state }),
      r.message && r.state !== "running" ? el("div", { class: "muted", text: r.message }) : null,
      r.current_file && r.state === "running" ? el("div", { class: "muted", text: r.current_file }) : null),
    el("td", {}, dlProgress(r)),
    el("td", { class: "actions" },
      ["running", "publishing"].includes(r.state) ? el("button", { class: "danger", text: "Cancel", onclick: () => dlAction(`/api/downloads/${r.id}/cancel`, "Cancel") }) : null,
      ["failed", "cancelled", "interrupted"].includes(r.state) && r.retryable !== false ? el("button", { text: "Resume", onclick: () => dlAction(`/api/downloads/${r.id}/resume`, "Resume") }) : null,
      el("a", { class: "button", href: `/logs?source=${encodeURIComponent(r.log_source)}`, text: "Log" }))));
  document.querySelector("#dl-runs tbody").replaceChildren(...(rows.length ? rows :
    [el("tr", {}, el("td", { colspan: 6, class: "muted", text: "No downloads yet." }))]));
  if (runs.some((r) => ["starting", "running", "publishing"].includes(r.state))) setTimeout(() => loadDownloads().catch(() => {}), 2000);
}

document.getElementById("dl-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const box = document.getElementById("dl-preview");
  box.hidden = true;
  try {
    dlPreview = await api("/api/downloads/preview", { method: "POST",
      json: { repo: document.getElementById("dl-repo").value.trim(), revision: document.getElementById("dl-rev").value.trim() || "main" } });
  } catch (e) { alert(e.message); return; }
  const p = dlPreview;
  document.getElementById("dl-facts").replaceChildren(...[
    ["Commit", p.revision], ["Total size", p.unknown_size_files.length ? `${fmt.bytes(p.total_bytes)} + ${p.unknown_size_files.length} of unknown size` : fmt.bytes(p.total_bytes)],
    ["Free on the drive", fmt.bytes(p.free_bytes)], ["Needed with headroom", fmt.bytes(p.required_bytes)],
    ["Access", p.gated ? `gated (${p.token ? "token present" : "no token"})` : p.private ? "private" : "public"],
  ].map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
  const warnings = [...p.missing.map((m) => `Not a loadable model: missing ${m}`),
    ...(p.fits ? [] : ["Not enough free space (including headroom)"]),
    ...(p.unchecked_files.length ? [`${p.unchecked_files.length} file(s) have no published checksum; the download will refuse them`] : [])];
  document.getElementById("dl-warnings").replaceChildren(...warnings.map((w) => el("li", { text: w })));
  document.getElementById("dl-nfiles").textContent = p.files.length;
  document.querySelector("#dl-files tbody").replaceChildren(...p.files.map((f) => el("tr", {},
    el("td", {}, el("code", { text: f.path })), el("td", { class: "num", text: f.size === null ? "unknown" : fmt.bytes(f.size) }),
    el("td", { class: "muted", text: f.sha256 ? "sha256" : f.blob ? "git sha1" : "none" }))));
  document.getElementById("dl-card").textContent = p.card || "(no model card)";  // text only, never HTML
  document.getElementById("dl-dest").value = p.suggested_dest;
  document.getElementById("dl-start").disabled = warnings.length > 0;
  box.hidden = false;
});

document.getElementById("dl-start").addEventListener("click", async () => {
  if (!dlPreview) return;
  const dest = document.getElementById("dl-dest").value.trim();
  if (!(await confirmAction(`Download ${dlPreview.repo}?`, `${fmt.bytes(dlPreview.total_bytes)} into models/${dest}. The running model keeps serving.`))) return;
  try {
    await api("/api/downloads", { method: "POST", json: { repo: dlPreview.repo, revision: dlPreview.revision, dest } });
    document.getElementById("dl-preview").hidden = true; dlPreview = null;
  } catch (e) { alert(e.message); }
  loadDownloads().catch(() => {});
});
loadDownloads().catch(() => {});

// --- profiles (M3c) ----------------------------------------------------------------
function fieldInput(section, f, value, verified) {
  const id = `pf-${section}-${f.name}`;
  let input;
  if (f.type === "bool") {
    input = el("select", { id }, el("option", { value: "", text: "default" }), el("option", { value: "true", text: "on" }), el("option", { value: "false", text: "off" }));
    input.value = value === undefined ? "" : String(value);
  } else if (f.enum) {
    const opts = f.name === "reasoning_effort" ? f.enum.filter((v) => verified.includes(v)) : f.enum;
    input = el("select", { id }, el("option", { value: "", text: "default" }), ...opts.map((v) => el("option", { value: String(v), text: String(v) })));
    input.value = value === undefined ? "" : String(value);
  } else {
    input = el("input", { id, type: "number", placeholder: "default", min: f.min ?? null, max: f.max ?? null,
      step: f.type === "int" ? "1" : "any", value: value === undefined ? "" : String(value) });
  }
  return el("label", { title: f.help }, el("span", { text: f.flag || f.name }), input,
    f.name === "reasoning_effort" ? el("small", { class: "muted", text: verified.length ? `verified: ${verified.join(", ")}` : "no value has passed the compatibility check" }) : null);
}

function readFields(section, fields) {
  const out = {};
  for (const f of fields) {
    const v = document.getElementById(`pf-${section}-${f.name}`).value;
    if (v === "") continue;
    if (f.type === "bool") out[f.name] = v === "true";
    else if (f.type === "str") out[f.name] = v;
    else out[f.name] = Number(v);
  }
  return out;
}

async function afterProfileJob(job, name) {
  showJob(job); pollJob(job.id);
  for (let i = 0; i < 60; i++) {
    const j = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
    if (j.status !== "running") {
      if (j.status !== "succeeded") document.getElementById("pf-error").textContent = (j.output || "").split("\n").filter((l) => l.includes("invalid") || l.includes("Unknown")).join(" ") || `profile change ${j.status}`;
      return renderProfile(name);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

async function renderProfile(name) {
  const v = await api(`/api/models/${encodeURIComponent(name)}/profile`);
  const p = v.profile;
  document.getElementById("pf-name").textContent = name;
  document.getElementById("pf-runtime").textContent = v.schema.runtime;
  document.getElementById("pf-state").textContent = `Saved: ${p.saved ? p.fingerprint : "none (built-in flags)"} · running: ${v.running ? (v.running_fingerprint || "unknown (started before profiles)") : "not running"}${p.launch_args.length ? ` · flags: ${p.launch_args.join(" ")}` : ""}`;
  document.getElementById("pf-restart").hidden = !v.restart_required;
  document.getElementById("pf-stale").hidden = !v.checks_stale;
  document.getElementById("pf-launch").replaceChildren(...v.schema.launch.map((f) => fieldInput("launch", f, p.launch[f.name], [])));
  document.getElementById("pf-chat").replaceChildren(...v.schema.chat.map((f) => fieldInput("chat", f, p.chat[f.name], v.verified_reasoning_effort)));
  document.getElementById("pf-revert").disabled = !p.has_previous;
  document.getElementById("pf-reset").disabled = !p.saved;
  const change = async (path, body) => {
    document.getElementById("pf-error").textContent = "";
    try { await afterProfileJob(await api(path, { method: "POST", json: body }), name); }
    catch (e) { document.getElementById("pf-error").textContent = e.message; }
  };
  const base = `/api/models/${encodeURIComponent(name)}/profile`;
  document.getElementById("pf-save").onclick = () => change(base, { launch: readFields("launch", v.schema.launch), chat: readFields("chat", v.schema.chat) });
  document.getElementById("pf-reset").onclick = () => change(`${base}/reset`);
  document.getElementById("pf-revert").onclick = () => change(`${base}/revert`);
  document.getElementById("pf-restart-btn").onclick = () => { document.getElementById("profile-dialog").close(); runAction("/api/models/switch", { name }, `Restart ${name} with its saved settings?`); };
}

async function openProfile(name) {
  document.getElementById("pf-error").textContent = "";
  try { await renderProfile(name); } catch (e) { alert(e.message); return; }
  document.getElementById("profile-dialog").showModal();
}
