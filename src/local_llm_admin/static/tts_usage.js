"use strict";
// TTS usage (M7) on the Usage page: per day and per client.
(() => {
  const q = (id) => document.getElementById(id);
  const sec = (v) => (v === null || v === undefined ? "—" : `${(v / 1000).toFixed(1)} s`);
  const row = (label, r) => el("tr", {}, el("td", { text: label }),
    el("td", { class: "num", text: fmt.num(r.requests) }), el("td", { class: "num", text: fmt.num(r.errors) }),
    el("td", { class: "num", text: fmt.num(r.chars) }), el("td", { class: "num", text: `${Math.round(r.audio_s)} s` }),
    el("td", { class: "num", text: sec(r.p50_ms) }), el("td", { class: "num", text: sec(r.p95_ms) }));
  const empty = (text) => [el("tr", {}, el("td", { colspan: 7, class: "muted", text }))];

  async function load() {
    const [days, clients] = await Promise.all([api("/api/tts/usage?days=30&group=day"), api("/api/tts/usage?days=30&group=client")]);
    const d = days.rows.slice().reverse().map((r) => row(r.day, r));
    q("tts-usage-table").querySelector("tbody").replaceChildren(...(d.length ? d : empty("No speech in the last 30 days.")));
    const c = clients.rows.map((r) => row(r.client, r));
    q("tts-usage-clients").querySelector("tbody").replaceChildren(...(c.length ? c : empty("No clients yet.")));
  }
  load().catch(() => {});
})();
