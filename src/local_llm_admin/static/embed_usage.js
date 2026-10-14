"use strict";
// Embeddings usage (M8) on the Usage page: per day and per client.
(() => {
  const q = (id) => document.getElementById(id);
  const ms = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
  const row = (label, r) => el("tr", {}, el("td", { text: label }),
    el("td", { class: "num", text: fmt.num(r.requests) }), el("td", { class: "num", text: fmt.num(r.errors) }),
    el("td", { class: "num", text: fmt.num(r.inputs) }), el("td", { class: "num", text: fmt.num(r.chars) }),
    el("td", { class: "num", text: ms(r.p50_ms) }), el("td", { class: "num", text: ms(r.p95_ms) }));
  const empty = (text) => [el("tr", {}, el("td", { colspan: 7, class: "muted", text }))];

  async function load() {
    const [days, clients] = await Promise.all([api("/api/embed/usage?days=30&group=day"), api("/api/embed/usage?days=30&group=client")]);
    const d = days.rows.slice().reverse().map((r) => row(r.day, r));
    q("embed-usage-table").querySelector("tbody").replaceChildren(...(d.length ? d : empty("No embedding requests in the last 30 days.")));
    const c = clients.rows.map((r) => row(r.client, r));
    q("embed-usage-clients").querySelector("tbody").replaceChildren(...(c.length ? c : empty("No clients yet.")));
  }
  load().catch(() => {});
})();
