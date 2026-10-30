"use strict";
const $ = (id) => document.getElementById(id);
let source = null, followWanted = false, queryGeneration = 0, lastRead = null;
let explicitSource = new URLSearchParams(location.search).get("source");
let activeSource = null, sourcesReady = false;
function logParams() {
  const p = new URLSearchParams();
  if ($("grep").value) p.set("grep", $("grep").value);
  if ($("level").value) p.set("level", $("level").value);
  return p;
}
function logError(error) { $("log-meta").textContent = `${error.message} · ${lastRead ? "Stale; last success " + fmt.time(lastRead) : "No successful read"}`; }
const MAX_LINES = 5000;

function lineNode(line) {
  const m = line.match(/\b(WARNING|WARN|ERROR|CRITICAL)\b/);
  const lv = m ? (m[1] === "WARN" ? "WARNING" : m[1]) : null;
  return el("span", { class: lv ? `lv-${lv}` : null, text: `${line}\n` });
}

function render(lines) {
  $("log-view").replaceChildren(...lines.map(lineNode));
  $("log-view").scrollTop = $("log-view").scrollHeight;
}

async function load() {
  const gen = ++queryGeneration;
  stopFollow(false);
  const p = logParams(); p.set("lines", $("lines").value);
  const id = $("source").value;
  if (!id) return;
  try {
    const r = await api(`/api/logs/${encodeURIComponent(id)}?${p}`);
    if (gen !== queryGeneration) return;
    lastRead = Date.now() / 1000;
    $("log-meta").textContent = r.exists ? `${fmt.bytes(r.size)} on disk · showing ${r.lines.length} lines · checked ${fmt.time(lastRead)}` : "File does not exist.";
    $("download").href = `/api/logs/${encodeURIComponent(id)}/download`;
    render(r.lines);
    await loadDownloads(id);
    if (followWanted && gen === queryGeneration) startFollow();
  } catch (e) { if (gen === queryGeneration) logError(e); }
}

async function loadDownloads(id) {
  const box = $("dl-summary");
  box.hidden = !id.startsWith("download-");
  if (box.hidden) return;
  const { logs } = await api("/api/downloads");
  const s = logs.find((l) => l.id === id) || { runs: [], last_progress: null };
  const run = s.runs[s.runs.length - 1];
  const p = s.last_progress;
  const facts = run ? [
    ["Last run", `${run.started} → ${run.finished || "running"}`],
    ["Files requested", fmt.num(run.files.length)],
    ["Failures", run.failed === null ? "—" : fmt.num(run.failed)],
    ["Latest transfer", p ? `${p.percent}% of ${p.total} · ${p.speed}/s · ${p.left} left` : "—"],
  ] : [["Downloads", "no runs in the log"]];
  box.replaceChildren(...facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));
}

function stopFollow(pause = true) {
  source?.close(); source = null;
  if (pause) followWanted = false;
  $("btn-follow").setAttribute("aria-pressed", String(followWanted));
  $("btn-follow").textContent = followWanted ? "Pause" : "Follow";
  $("log-connection").textContent = followWanted ? "Connecting…" : "Paused";
}
function startFollow() {
  if (!$("source").value) return;
  source?.close(); followWanted = true;
  const view = $("log-view"), gen = queryGeneration;
  const stream = new EventSource(`/api/logs/${encodeURIComponent($("source").value)}/follow?${logParams()}`);
  source = stream;
  $("log-connection").textContent = "Connecting…";
  stream.onopen = () => { if (source === stream) $("log-connection").textContent = "Following · current text and minimum level filters"; };
  stream.onerror = () => { if (source === stream) $("log-connection").textContent = "Reconnecting · displayed lines may be stale"; };
  stream.onmessage = msg => {
    if (source !== stream || gen !== queryGeneration) return;
    const ev = JSON.parse(msg.data);
    if (ev.type !== "line") return;
    const stick = view.scrollTop + view.clientHeight >= view.scrollHeight - 20;
    view.append(lineNode(ev.line));
    while (view.childNodes.length > MAX_LINES) view.firstChild.remove();
    if (stick) view.scrollTop = view.scrollHeight;
    $("log-connection").textContent = "Following · last line " + fmt.time(Date.now() / 1000);
  };
  $("btn-follow").setAttribute("aria-pressed", "true"); $("btn-follow").textContent = "Pause";
}
function preferActiveSource() {
  if (!sourcesReady || explicitSource || !activeSource) return;
  if ([...$("source").options].some(option => option.value === activeSource) && $("source").value !== activeSource) {
    $("source").value = activeSource; load();
  }
}
onStatus(st => {
  const backend = EngineUI.active(st);
  activeSource = backend?.log_source || backend?.id;
  preferActiveSource();
});
$("btn-follow").addEventListener("click", () => followWanted ? stopFollow() : startFollow());
$("log-form").addEventListener("submit", e => { e.preventDefault(); load(); });
$("source").addEventListener("change", () => { explicitSource = $("source").value; load(); });
$("level").addEventListener("change", load);
let filterTimer = null;
$("grep").addEventListener("input", () => { clearTimeout(filterTimer); filterTimer = setTimeout(load, 300); });
$("wrap").addEventListener("change", () => $("log-view").classList.toggle("wrap", $("wrap").checked));

(async () => {
  const { sources } = await api("/api/logs");
  $("source").replaceChildren(...sources.map((s) => el("option", { value: s.id, text: s.exists ? s.id : `${s.id} (missing)` })));
  const wanted = new URLSearchParams(location.search).get("source");  // a link from a download run
  if (wanted && sources.some(s => s.id === wanted)) $("source").value = wanted;
  else if (activeSource && sources.some(s => s.id === activeSource)) $("source").value = activeSource;
  sourcesReady = true;
  await load();
})().catch(logError);
