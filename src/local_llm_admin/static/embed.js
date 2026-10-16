"use strict";
// Embeddings test page (M8): similarity matrix and rerank through the admin, plus history.
(() => {
  const $ = (id) => document.getElementById(id);
  const ms = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
  let cfg = null;

  const lines = (id) => $(id).value.split("\n").map((t) => t.trim()).filter(Boolean);
  function fail(id, e) { $(id).textContent = e.message; $(id).hidden = false; }

  async function load() {
    cfg = await api("/api/embed");
    $("embed-dims").textContent = `${cfg.dims} dims · up to ${cfg.max_inputs} inputs`;
    $("embed-disabled").hidden = cfg.running;
    $("embed-disabled").textContent = cfg.running ? "" : cfg.reason === "comfyui_active"
      ? "The embedding service is paused while ComfyUI runs."
      : cfg.reason === "strata_active" ? "The embedding service is paused while Strata runs."
      : "The embedding service is stopped. Start it on the Models page (Embeddings card).";
    $("embed-sim-run").disabled = $("embed-rr-run").disabled = !cfg.running;
    const h = await api("/api/embed/history?days=14");
    const a = h.availability;
    $("embed-availability").textContent = a.ratio === null ? "" : `availability ${(a.ratio * 100).toFixed(1)}%`;
    const rows = h.daily.slice().reverse().map((r) => el("tr", {}, el("td", { text: r.day }),
      el("td", { class: "num", text: fmt.num(r.requests) }), el("td", { class: "num", text: fmt.num(r.errors) }),
      el("td", { class: "num", text: fmt.num(r.inputs) }), el("td", { class: "num", text: fmt.num(r.chars) }),
      el("td", { class: "num", text: ms(r.p50_ms) }), el("td", { class: "num", text: ms(r.p95_ms) })));
    $("embed-daily").querySelector("tbody").replaceChildren(...rows);
  }

  async function similarity(ev) {
    ev.preventDefault();
    $("embed-sim-error").hidden = true;
    const texts = lines("embed-texts");
    try {
      const r = await api("/api/embed/similarity", { method: "POST", json: { texts, query_first: $("embed-query-first").checked } });
      const head = el("tr", {}, el("th", { text: "" }), ...texts.map((_, i) => el("th", { class: "num", text: `#${i + 1}` })));
      const body = r.matrix.map((row, i) => el("tr", {}, el("th", { text: `#${i + 1} ${texts[i].slice(0, 40)}` }),
        ...row.map((v, j) => el("td", { class: i === j ? "num muted" : "num", text: v.toFixed(3) }))));
      $("embed-matrix").querySelector("tbody").replaceChildren(head, ...body);
      load().catch(() => {});
    } catch (e) { fail("embed-sim-error", e); }
  }

  async function rerank(ev) {
    ev.preventDefault();
    $("embed-rr-error").hidden = true;
    const docs = lines("embed-rr-docs");
    try {
      const r = await api("/api/embed/rerank", { method: "POST", json: { query: $("embed-rr-query").value, documents: docs } });
      $("embed-rr-results").replaceChildren(...r.results.map((x) => el("li", { text: `${x.relevance_score.toFixed(3)} — ${docs[x.index]}` })));
      load().catch(() => {});
    } catch (e) { fail("embed-rr-error", e); }
  }

  function curl() {
    const host = `${location.hostname}${cfg.gateway}`;
    const body = JSON.stringify({ model: cfg.rerank_model_id, query: $("embed-rr-query").value, documents: lines("embed-rr-docs") });
    const key = cfg.require_key ? " -H 'Authorization: Bearer <dsk_ key>'" : "";
    navigator.clipboard.writeText(`curl -s http://${host}/v1/rerank -H 'content-type: application/json'${key} -d '${body.replace(/'/g, "'\\''")}'`);
  }

  $("embed-sim-form").addEventListener("submit", similarity);
  $("embed-rr-form").addEventListener("submit", rerank);
  $("embed-curl").addEventListener("click", curl);
  load().catch(() => {});
})();
