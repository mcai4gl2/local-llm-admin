"use strict";
const $ = (id) => document.getElementById(id);
let range = { days: 30 };
let sessPage = 1;
let lastDaily = null;
let requestGeneration = 0, dailyChecked = null, sessionsChecked = null;

function isoLocal(d) { return OpsTime.day(d); }
function params() {
  if (range.from) return `from=${range.from}&to=${range.to}`;
  // "today" is computed server side in the ops timezone; send only the span
  const to = new Date(); const from = new Date(to.getTime() - (range.days - 1) * 86400000);
  return `from=${isoLocal(from)}&to=${isoLocal(to)}`;
}

function showError(e) {
  const box = $("usage-error");
  if (!e) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = e.body?.error === "config" ? "LiteLLM key is not configured on the admin (LITELLM_ADMIN_KEY)."
    : e.body?.error === "auth" ? "LiteLLM rejected the admin key. Check LITELLM_ADMIN_KEY."
    : e.body?.error === "unreachable" ? "The LiteLLM gateway is unreachable." : e.message;
}

async function loadDaily() {
  const gen = requestGeneration, query = params();
  $("daily-state").textContent = "Refreshing · previous values may be stale";
  try {
    const r = await api(`/api/usage/daily?${query}`);
    if (gen !== requestGeneration) return;
    showError(null); lastDaily = r;
    dailyChecked = Date.now() / 1000; $("daily-state").textContent = "Updated " + fmt.time(dailyChecked);
    $("from").value = r.from; $("to").value = r.to;
    const t = r.totals;
    $("usage-totals").replaceChildren(
      ...[["Requests", fmt.num(t.api_requests)], ["Prompt tokens", fmt.num(t.prompt_tokens)],
        ["Completion tokens", fmt.num(t.completion_tokens)], ["Failed requests", fmt.num(t.failed_requests)]]
        .map(([k, v]) => el("div", {}, el("div", { class: "label", text: k }), el("div", { class: "hero-value", text: v }))),
      ...(r.truncated ? [el("div", { class: "error", text: "LiteLLM reported more data than it returned; totals may be incomplete." })] : []));
    drawDaily();
    const rows = [];
    for (const d of [...r.days].reverse()) {
      if (!d.api_requests) continue;
      const apps = d.apps.length ? d.apps : [{ app: "—", ...d }];
      apps.forEach((a, i) => rows.push(el("tr", { class: i === 0 ? "group-start" : null },
        el("td", { text: i === 0 ? d.date : "" }), el("td", { text: a.app }),
        el("td", { class: "num", text: fmt.num(a.api_requests) }), el("td", { class: "num", text: fmt.num(a.prompt_tokens) }),
        el("td", { class: "num", text: fmt.num(a.completion_tokens) }),
        el("td", { class: `num ${a.failed_requests ? "bad" : ""}`, text: fmt.num(a.failed_requests) }),
        el("td", {}, i === 0 ? [...d.active_models.map((n) => el("span", { class: "pill", text: n })),
          d.active_models.length > 1 ? el("span", { class: "muted", title: "Several models served this route today; tokens are not split between them.", text: " unallocated" }) : null] : []))));
    }
    $("daily-table").querySelector("tbody").replaceChildren(...(rows.length ? rows :
      [el("tr", {}, el("td", { colspan: 7, class: "muted", text: "No local-model requests in this range." }))]));
  } catch (e) { if (gen !== requestGeneration) return; showError(e); $("daily-state").textContent = `Unavailable: ${e.message} · last success ${fmt.time(dailyChecked)}`; }
}

function drawDaily() {
  if (!lastDaily) return;
    stackedBars($("usage-chart"), lastDaily.days.map((d) => ({
      label: d.date, a: d.prompt_tokens, b: d.completion_tokens,
      lines: [d.date, `Prompt ${fmt.num(d.prompt_tokens)}`, `Completion ${fmt.num(d.completion_tokens)}`, `${fmt.num(d.api_requests)} requests`],
    })));
}

async function loadSessions() {
  const gen = requestGeneration, page = sessPage, query = params();
  $("sessions-state").textContent = "Refreshing · previous values may be stale";
  try {
    const r = await api(`/api/usage/sessions?${query}&page=${page}`);
    if (gen !== requestGeneration || page !== sessPage) return;
    sessionsChecked = Date.now() / 1000; $("sessions-state").textContent = "Updated " + fmt.time(sessionsChecked);
    $("sessions-table").querySelector("tbody").replaceChildren(...r.sessions.map((s) => el("tr", {},
      el("td", { text: s.app }), el("td", {}, el("code", { text: s.session_id })),
      el("td", { class: "num", text: fmt.num(s.requests) }), el("td", { class: "num", text: fmt.num(s.prompt_tokens) }),
      el("td", { class: "num", text: fmt.num(s.completion_tokens) }),
      el("td", { text: fmt.time(OpsTime.epoch(s.first_ts)) }),
      el("td", { text: fmt.time(OpsTime.epoch(s.last_ts)) }),
      el("td", {}, el("span", { class: "ok", text: `${s.success} ok` }), s.failure ? el("span", { class: "bad", text: ` · ${s.failure} failed` }) : null))));
    $("page-info").textContent = `Page ${r.page} of ${r.total_pages} · ${r.total_sessions} sessions${r.truncated ? " (truncated)" : ""}`;
    $("prev").disabled = r.page <= 1; $("next").disabled = r.page >= r.total_pages;
  } catch (e) { if (gen !== requestGeneration || page !== sessPage) return; showError(e); $("sessions-state").textContent = `Unavailable: ${e.message} · last success ${fmt.time(sessionsChecked)}`; }
}

function reload() { requestGeneration++; sessPage = 1; loadDaily(); if (!document.querySelector('[data-panel="sessions"]').hidden) loadSessions(); }

document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
  range = { days: Number(b.dataset.days) };
  document.querySelectorAll("[data-days]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  reload();
}));
$("custom-range").addEventListener("submit", (e) => {
  e.preventDefault();
  range = { from: $("from").value, to: $("to").value };
  document.querySelectorAll("[data-days]").forEach((o) => o.setAttribute("aria-pressed", "false"));
  reload();
});
document.querySelectorAll("[role=tab]").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll("[role=tab]").forEach(o => { o.setAttribute("aria-selected", String(o === t)); o.tabIndex = o === t ? 0 : -1; });
  document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== t.dataset.tab));
  if (t.dataset.tab === "sessions") loadSessions();
}));
document.querySelector('[role="tablist"]').addEventListener("keydown", ev => {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  let index = tabs.indexOf(document.activeElement);
  if (ev.key === "ArrowRight") index = (index + 1) % tabs.length;
  else if (ev.key === "ArrowLeft") index = (index - 1 + tabs.length) % tabs.length;
  else if (ev.key === "Home") index = 0;
  else if (ev.key === "End") index = tabs.length - 1;
  else return;
  ev.preventDefault(); tabs[index].focus(); tabs[index].click();
});
$("prev").addEventListener("click", () => { sessPage--; loadSessions(); });
$("next").addEventListener("click", () => { sessPage++; loadSessions(); });
window.addEventListener("resize", drawDaily);

api("/api/usage/health").then((h) => {
  $("gw-health").textContent = !h.reachable ? "Gateway unreachable" : h.key_ok ? "Gateway OK" : `Gateway: ${h.error}`;
  $("gw-health").className = h.reachable && h.key_ok ? "ok" : "bad";
}).catch(e => { $("gw-health").textContent = "Gateway status unavailable: " + e.message; });
$("usage-scope").textContent = "LiteLLM gateway accounting · calendar days and timestamps in " + OpsTime.label + ". Direct Chat and native execution counters are separate. Gateway aliases are not split among historical engines.";
loadDaily();
