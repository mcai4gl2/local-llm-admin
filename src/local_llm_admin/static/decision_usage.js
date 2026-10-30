"use strict";
// Decision gateway clients and usage (M6 Phase 5): on the Decision page and the Usage page.
(() => {
  const q = (id) => document.getElementById(id);
  const msOrDash = (v) => (v === null || v === undefined ? "—" : `${fmt.num(Math.round(v))} ms`);
  const cells = (r) => [
    el("td", { class: "num", text: fmt.num(r.requests) }), el("td", { class: "num", text: fmt.num(r.errors) }),
    el("td", { class: "num", text: fmt.num(r.questions) }),
  ];

  async function loadUsageDays() {
    const { rows } = await api("/api/decision/usage?days=30&group=day");
    const trs = rows.slice().reverse().map((r) => el("tr", {}, el("td", { text: r.day }), ...cells(r),
      el("td", { class: "num", text: fmt.num(r.samples) }), el("td", { class: "num", text: fmt.num(r.input_tokens) }),
      el("td", { class: "num", text: msOrDash(r.p50_ms) }), el("td", { class: "num", text: msOrDash(r.p95_ms) })));
    q("decision-usage-table").querySelector("tbody").replaceChildren(...(trs.length ? trs
      : [el("tr", {}, el("td", { colspan: 8, class: "muted", text: "No decision reads in the last 30 days." }))]));
  }

  async function loadClientUsage() {
    const u = await api("/api/decision/usage?days=30&group=client");
    q("gateway-addr").textContent = `gateway on port ${u.gateway.slice(1)}`
      + (u.require_key ? ", key required" : ", key optional");
    const trs = u.rows.map((r) => el("tr", {}, el("td", { text: r.client }), ...cells(r),
      el("td", { class: "num", text: fmt.num(r.input_tokens) }),
      el("td", { class: "num", text: msOrDash(r.p50_ms) }), el("td", { class: "num", text: msOrDash(r.p95_ms) })));
    q("client-usage").querySelector("tbody").replaceChildren(...(trs.length ? trs
      : [el("tr", {}, el("td", { colspan: 7, class: "muted", text: "No reads yet." }))]));
  }

  function showError(e) {
    q("client-error").textContent = e.message; q("client-error").hidden = false;
  }

  async function loadClients() {
    const { clients } = await api("/api/decision/clients");
    const trs = clients.map((c) => {
      const revoked = c.revoked_at !== null;
      const actions = revoked ? [] : [
        el("button", { class: "link", text: "Rename", onclick: () => rename(c) }),
        el("button", { class: "link danger", text: "Revoke", onclick: () => revoke(c) }),
      ];
      return el("tr", {}, el("td", { text: c.name }), el("td", {}, el("code", { text: `dsk_${c.prefix}_…` })),
        el("td", { text: fmt.time(c.created_at) }), el("td", { text: fmt.time(c.last_used_at) }),
        el("td", { class: revoked ? "" : "ok", text: revoked ? `revoked ${fmt.time(c.revoked_at)}` : "active" }),
        el("td", {}, ...actions));
    });
    q("clients-table").querySelector("tbody").replaceChildren(...(trs.length ? trs
      : [el("tr", {}, el("td", { colspan: 6, class: "muted", text: "No clients yet." }))]));
  }

  async function rename(c) {
    const name = prompt(`New name for ${c.name}:`, c.name);
    if (!name || name === c.name) return;
    try { await api(`/api/decision/clients/${c.id}/rename`, { method: "POST", json: { name } }); await loadClients(); }
    catch (e) { showError(e); }
  }

  async function revoke(c) {
    if (!confirm(`Revoke the key for ${c.name}? Its next request gets 401.`)) return;
    try { await api(`/api/decision/clients/${c.id}/revoke`, { method: "POST" }); await loadClients(); }
    catch (e) { showError(e); }
  }

  async function create(ev) {
    ev.preventDefault();
    q("client-error").hidden = true;
    try {
      const c = await api("/api/decision/clients", { method: "POST",
        json: { name: q("client-name").value.trim(), note: q("client-note").value.trim() || null } });
      q("new-key-value").textContent = c.key; q("new-key").hidden = false;
      q("client-name").value = ""; q("client-note").value = "";
      await loadClients();
    } catch (e) { showError(e); }
  }

  if (q("decision-usage-table")) trackSection("decision-usage-table", loadUsageDays);
  if (q("clients-card")) {
    q("client-form").addEventListener("submit", create);
    q("copy-key").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(q("new-key-value").textContent); q("copy-key").textContent = "Copied"; }
      catch (e) { prompt("Copy the key:", q("new-key-value").textContent); }
    });
    loadClients().catch(showError);
    trackSection("client-usage", loadClientUsage);
    setInterval(() => trackSection("client-usage", loadClientUsage), 30000);
  }
})();
