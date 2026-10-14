"use strict";
const $ = (id) => document.getElementById(id);
let source = null;
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
  stopFollow();
  const p = new URLSearchParams({ lines: $("lines").value });
  if ($("grep").value) p.set("grep", $("grep").value);
  if ($("level").value) p.set("level", $("level").value);
  const id = $("source").value;
  const r = await api(`/api/logs/${encodeURIComponent(id)}?${p}`);
  $("log-meta").textContent = r.exists ? `${fmt.bytes(r.size)} on disk · showing ${r.lines.length} lines` : "File does not exist.";
  $("download").href = `/api/logs/${encodeURIComponent(id)}/download`;
  render(r.lines);
  await loadDownloads(id);
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

function stopFollow() {
  source?.close(); source = null;
  $("btn-follow").setAttribute("aria-pressed", "false"); $("btn-follow").textContent = "Follow";
}

function startFollow() {
  const view = $("log-view");
  const grep = $("grep").value.toLowerCase();
  source = new EventSource(`/api/logs/${encodeURIComponent($("source").value)}/follow`);
  source.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type !== "line" || (grep && !ev.line.toLowerCase().includes(grep))) return;
    const stick = view.scrollTop + view.clientHeight >= view.scrollHeight - 20;
    view.append(lineNode(ev.line));
    while (view.childNodes.length > MAX_LINES) view.firstChild.remove();
    if (stick) view.scrollTop = view.scrollHeight;
  };
  $("btn-follow").setAttribute("aria-pressed", "true"); $("btn-follow").textContent = "Pause";
}

$("btn-follow").addEventListener("click", () => (source ? stopFollow() : startFollow()));
$("log-form").addEventListener("submit", (e) => { e.preventDefault(); load().catch((err) => ($("log-meta").textContent = err.message)); });
$("source").addEventListener("change", () => load());
$("level").addEventListener("change", () => load());
$("wrap").addEventListener("change", () => $("log-view").classList.toggle("wrap", $("wrap").checked));

(async () => {
  const { sources } = await api("/api/logs");
  $("source").replaceChildren(...sources.map((s) => el("option", { value: s.id, text: s.exists ? s.id : `${s.id} (missing)` })));
  const wanted = new URLSearchParams(location.search).get("source");  // a link from a download run
  if (wanted && sources.some((s) => s.id === wanted)) $("source").value = wanted;
  await load();
})();
