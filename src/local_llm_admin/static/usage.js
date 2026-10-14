"use strict";
const $ = (id) => document.getElementById(id);
let range = { days: 30 };
let sessPage = 1;
let lastDaily = null;

function isoLocal(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
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
  try {
    const r = await api(`/api/usage/daily?${params()}`);
    showError(null); lastDaily = r;
    $("from").value = r.from; $("to").value = r.to;
    const t = r.totals;
    $("usage-totals").replaceChildren(
      ...[["Requests", fmt.num(t.api_requests)], ["Prompt tokens", fmt.num(t.prompt_tokens)],
        ["Completion tokens", fmt.num(t.completion_tokens)], ["Failed requests", fmt.num(t.failed_requests)]]
        .map(([k, v]) => el("div", {}, el("div", { class: "label", text: k }), el("div", { class: "hero-value", text: v }))),
      ...(r.truncated ? [el("div", { class: "error", text: "LiteLLM reported more data than it returned; totals may be incomplete." })] : []));
    stackedBars($("usage-chart"), r.days.map((d) => ({
      label: d.date, a: d.prompt_tokens, b: d.completion_tokens,
      lines: [d.date, `Prompt ${fmt.num(d.prompt_tokens)}`, `Completion ${fmt.num(d.completion_tokens)}`, `${fmt.num(d.api_requests)} requests`],
    })));
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
  } catch (e) { showError(e); }
}

async function loadSessions() {
  try {
    const r = await api(`/api/usage/sessions?${params()}&page=${sessPage}`);
    showError(null);
    $("sessions-table").querySelector("tbody").replaceChildren(...r.sessions.map((s) => el("tr", {},
      el("td", { text: s.app }), el("td", {}, el("code", { text: s.session_id })),
      el("td", { class: "num", text: fmt.num(s.requests) }), el("td", { class: "num", text: fmt.num(s.prompt_tokens) }),
      el("td", { class: "num", text: fmt.num(s.completion_tokens) }),
      el("td", { text: s.first_ts ? `${s.first_ts.replace("T", " ").slice(0, 19)} UTC` : "—" }),
      el("td", { text: s.last_ts ? `${s.last_ts.replace("T", " ").slice(0, 19)} UTC` : "—" }),
      el("td", {}, el("span", { class: "ok", text: `${s.success} ok` }), s.failure ? el("span", { class: "bad", text: ` · ${s.failure} failed` }) : null))));
    $("page-info").textContent = `Page ${r.page} of ${r.total_pages} · ${r.total_sessions} sessions${r.truncated ? " (truncated)" : ""}`;
    $("prev").disabled = r.page <= 1; $("next").disabled = r.page >= r.total_pages;
  } catch (e) { showError(e); }
}

function reload() { sessPage = 1; loadDaily(); if (!document.querySelector('[data-panel="sessions"]').hidden) loadSessions(); }

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
  document.querySelectorAll("[role=tab]").forEach((o) => o.setAttribute("aria-selected", String(o === t)));
  document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== t.dataset.tab));
  if (t.dataset.tab === "sessions") loadSessions();
}));
$("prev").addEventListener("click", () => { sessPage--; loadSessions(); });
$("next").addEventListener("click", () => { sessPage++; loadSessions(); });
window.addEventListener("resize", () => { if (lastDaily) loadDaily(); });

api("/api/usage/health").then((h) => {
  $("gw-health").textContent = !h.reachable ? "Gateway unreachable" : h.key_ok ? "Gateway OK" : `Gateway: ${h.error}`;
  $("gw-health").className = h.reachable && h.key_ok ? "ok" : "bad";
}).catch(() => {});
loadDaily();
