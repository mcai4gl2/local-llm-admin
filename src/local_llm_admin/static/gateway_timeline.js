"use strict";

(() => {
  const node = id => document.getElementById(id);
  let generation = 0, next = null, active = null;
  const seconds = value => value == null ? "—" : `${Number(value).toFixed(2)} s`;

  function filters() {
    const days = Number(node("gateway-days").value);
    const today = OpsTime.day(new Date());
    const first = new Date(Date.parse(today + "T00:00:00Z") - (days - 1) * 86400000)
      .toISOString().slice(0, 10);
    return {from: first, to: today, backend: node("gateway-backend").value,
      outcome: node("gateway-outcome").value,
      request_id: node("gateway-request-id").value.trim()};
  }

  function row(record) {
    return el("tr", {},
      el("td", {text: fmt.time(record.started)}),
      el("td", {}, el("code", {text: record.request_id})),
      el("td", {text: record.backend ? `${record.backend} · ${record.resolved_model || "unknown"}` : "—"}),
      el("td", {text: record.outcome}),
      el("td", {class: "num", text: seconds(record.time_to_headers_s)}),
      el("td", {class: "num", text: seconds(record.time_to_first_event_s)}),
      el("td", {class: "num", text: seconds(record.time_to_first_content_s)}),
      el("td", {class: "num", text: seconds(record.elapsed_s)}),
      el("td", {}, el("button", {class: "link", text: "Detail", onclick: () => detail(record.request_id)})));
  }

  async function detail(id) {
    const current = generation;
    const target = node("gateway-detail");
    target.hidden = false;
    target.textContent = "Loading…";
    try {
      const record = await api(`/api/inference/timeline/${encodeURIComponent(id)}`);
      if (current !== generation) return;
      target.textContent = JSON.stringify(record, null, 2);
    } catch (error) {
      if (current === generation) target.textContent = `Detail unavailable: ${error.message}`;
    }
  }

  async function load(append = false) {
    const current = generation;
    const button = node("gateway-more"), state = node("gateway-state");
    button.disabled = true;
    state.textContent = "Loading gateway requests…";
    const params = new URLSearchParams({from: active.from, to: active.to, limit: "50"});
    for (const key of ["backend", "outcome", "request_id"])
      if (active[key]) params.set(key, active[key]);
    if (append && next) params.set("before", String(next));
    try {
      const page = await api(`/api/inference/timeline?${params}`);
      if (current !== generation) return;
      const body = node("gateway-table").querySelector("tbody");
      const rows = page.requests.map(row);
      if (append) body.append(...rows);
      else body.replaceChildren(...(rows.length ? rows : [el("tr", {},
        el("td", {colspan: 9, text: "No recorded gateway requests in this range."}))]));
      next = page.next;
      button.hidden = !next;
      state.textContent = `Gateway requests · ${active.from} through ${active.to} · ${page.requests.length} on this page`;
      button.disabled = false;
    } catch (error) {
      if (current === generation) state.textContent = `Gateway timeline unavailable: ${error.message}`;
    }
  }

  async function health() {
    try {
      const h = await api("/api/inference/timeline/health");
      node("gateway-coverage").textContent =
        `Coverage begins ${fmt.time(h.coverage_start)} · ${h.population} · ${h.retention_days} day retention · ` +
        `${h.pending_writes}/${h.queue_capacity} pending writes · ${h.dropped_writes} dropped · ${h.failed_writes} failed.`;
    } catch (error) {
      node("gateway-coverage").textContent = `Coverage and persistence health unavailable: ${error.message}`;
    }
  }

  function apply() {
    generation++;
    next = null;
    active = filters();
    node("gateway-detail").hidden = true;
    load();
    health();
  }

  node("gateway-apply").addEventListener("click", apply);
  node("gateway-more").addEventListener("click", () => { if (next) load(true); });
  node("gateway-request-id").addEventListener("keydown", event => {
    if (event.key === "Enter") apply();
  });
  apply();
})();
