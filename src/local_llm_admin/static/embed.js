"use strict";
// Embeddings test page (M8): similarity matrix and rerank through the admin, plus history.
(() => {
  const $ = (id) => document.getElementById(id);
  const ms = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
  let cfg = null;
  let busy = false;

  const lines = (id) => $(id).value.split("\n").map((t) => t.trim()).filter(Boolean);
  function fail(id, e) { $(id).textContent = e.message; $(id).hidden = false; }

  function sync() {
    const gate = serviceNotice($("embed-disabled"), cfg);
    $("embed-sim-run").disabled = $("embed-rr-run").disabled = !gate.ok || busy;
  }
  watchService("/api/embed", data => {
    cfg = data;
    if (cfg.dims) $("embed-dims").textContent = `${cfg.dims} dims · up to ${cfg.max_inputs} inputs`;
    sync();
  });
  async function load() {
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
    if (!serviceEligibility(cfg).ok || busy) return;
    busy = true; sync();
    $("embed-sim-error").hidden = true;
    const texts = lines("embed-texts");
    try {
      const r = await api("/api/embed/similarity", { method: "POST", json: { texts, query_first: $("embed-query-first").checked } });
      const head = el("tr", {}, el("th", { text: "" }), ...texts.map((_, i) => el("th", { class: "num", text: `#${i + 1}` })));
      const body = r.matrix.map((row, i) => el("tr", {}, el("th", { text: `#${i + 1} ${texts[i].slice(0, 40)}` }),
        ...row.map((v, j) => el("td", { class: i === j ? "num muted" : "num", text: v.toFixed(3) }))));
      $("embed-matrix").querySelector("tbody").replaceChildren(head, ...body);
      trackSection("embed-daily", load);
    } catch (e) { fail("embed-sim-error", e); }
    finally { busy = false; sync(); }
  }

  async function rerank(ev) {
    ev.preventDefault();
    if (!serviceEligibility(cfg).ok || busy) return;
    busy = true; sync();
    $("embed-rr-error").hidden = true;
    const docs = lines("embed-rr-docs");
    try {
      const r = await api("/api/embed/rerank", { method: "POST", json: { query: $("embed-rr-query").value, documents: docs } });
      $("embed-rr-results").replaceChildren(...r.results.map((x) => el("li", { text: `${x.relevance_score.toFixed(3)} — ${docs[x.index]}` })));
      trackSection("embed-daily", load);
    } catch (e) { fail("embed-rr-error", e); }
    finally { busy = false; sync(); }
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
  trackSection("embed-daily", load);
  setInterval(() => trackSection("embed-daily", load), 30000);
})();
