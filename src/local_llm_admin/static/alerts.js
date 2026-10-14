"use strict";
let historyBefore = null;

function alertRow(a, withAck) {
  const since = a.kind === "condition" ? `since ${fmt.time(a.first_seen)}` : fmt.time(a.first_seen);
  const meta = [since, a.kind === "condition" ? `last seen ${fmt.time(a.last_seen)}` : "event"];
  if (a.count > 1) meta.push(`${a.count}×`);
  if (a.resolved_at && a.kind === "condition") meta.push(`resolved ${fmt.time(a.resolved_at)}`);
  if (a.acked_at) meta.push("acknowledged");
  const ack = withAck && !a.acked_at
    ? el("button", { text: "Acknowledge", onclick: async () => { await api(`/api/alerts/${a.id}/ack`, { method: "POST" }); refreshAll(); } })
    : null;
  return el("li", { class: "alert", "data-acked": String(Boolean(a.acked_at)) },
    el("div", { class: "check-head" },
      el("span", { class: "sev", "data-status": a.severity, text: a.severity }),
      el("strong", { text: a.title })),
    a.detail ? el("div", { text: a.detail }) : null,
    el("div", { class: "muted", text: meta.join(" · ") }),
    ack ? el("div", { class: "actions" }, ack) : null);
}

async function refreshOpen() {
  const { alerts, summary } = await api("/api/alerts?state=open&limit=200");
  document.getElementById("alert-list").replaceChildren(...alerts.map((a) => alertRow(a, true)));
  document.getElementById("alert-empty").hidden = alerts.length > 0;
  document.getElementById("alert-summary").textContent = `${summary.open} open · ${summary.unacked} unacknowledged`;
  document.getElementById("ack-all").disabled = summary.unacked === 0;
}

async function refreshHistory(more = false) {
  const q = more && historyBefore ? `&before=${historyBefore}` : "";
  const { alerts } = await api(`/api/alerts?state=all&limit=50${q}`);
  // unacknowledged events are still listed under open alerts
  const rows = alerts.filter((a) => a.resolved_at && (a.kind === "condition" || a.acked_at))
    .map((a) => alertRow(a, false));
  const list = document.getElementById("history-list");
  if (more) list.append(...rows); else list.replaceChildren(...rows);
  historyBefore = alerts.length ? alerts[alerts.length - 1].id : historyBefore;
  document.getElementById("history-more").hidden = alerts.length < 50;
}

async function refreshDiagnostics() {
  const { checks } = await api("/api/diagnostics");
  const order = { fail: 0, warn: 1, unknown: 2, ok: 3 };
  checks.sort((a, b) => order[a.status] - order[b.status]);
  document.getElementById("diag-list").replaceChildren(...checks.map((c) => checkRow(c, refreshAll)));
}

function refreshAll() {
  refreshOpen().catch(() => {});
  refreshHistory().catch(() => {});
  refreshDiagnostics().catch(() => {});
}

document.getElementById("ack-all").addEventListener("click", async () => {
  await api("/api/alerts/ack-all", { method: "POST" });
  refreshStatus();
  refreshAll();
});
document.getElementById("diag-refresh").addEventListener("click", () => refreshDiagnostics().catch(() => {}));
document.getElementById("history-more").addEventListener("click", () => refreshHistory(true).catch(() => {}));
refreshAll();
setInterval(() => refreshOpen().catch(() => {}), 15000);
