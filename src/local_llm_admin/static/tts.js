"use strict";
// TTS test page (M7): speak through the admin, play and download the audio, show history.
(() => {
  const $ = (id) => document.getElementById(id);
  let cfg = null;
  let url = null;

  function count() {
    const n = $("tts-text").value.length;
    $("tts-count").textContent = cfg ? `${fmt.num(n)} / ${fmt.num(cfg.max_chars)} characters` : "";
    $("tts-run").disabled = !cfg || !cfg.running || n === 0 || n > cfg.max_chars;
  }

  function body() {
    const b = { model: cfg.model_id, input: $("tts-text").value, voice: $("tts-voice").value, response_format: $("tts-format").value };
    const ins = $("tts-instructions").value.trim();
    if (ins) b.instructions = ins;
    return b;
  }

  async function load() {
    cfg = await api("/api/tts");
    const sel = $("tts-voice");
    if (!sel.options.length) {
      for (const v of cfg.voices.length ? cfg.voices : [cfg.default_voice || ""]) sel.append(el("option", { value: v, text: v || "(default)" }));
      if (cfg.default_voice) sel.value = cfg.default_voice;
    }
    $("tts-text").maxLength = cfg.max_chars;
    $("tts-disabled").hidden = cfg.running;
    $("tts-disabled").textContent = cfg.running ? "" : "The TTS sidecar is stopped. Start it on the Models page (Sidecar card).";
    count();
  }

  async function speak(ev) {
    ev.preventDefault();
    $("tts-error").hidden = true;
    $("tts-run").disabled = true;
    $("tts-meta").textContent = "synthesizing…";
    try {
      const resp = await fetch("/api/tts/speak", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify(body()),
      });
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}));
        throw new Error(e.detail || `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      if (url) URL.revokeObjectURL(url);
      url = URL.createObjectURL(blob);
      $("tts-audio").src = url; $("tts-audio").hidden = false;
      $("tts-download").href = url; $("tts-download").download = `speech.${$("tts-format").value}`; $("tts-download").hidden = false;
      $("tts-meta").textContent = `${resp.headers.get("x-audio-seconds")} s audio · ${resp.headers.get("x-chunks")} chunk(s) · ${(Number(resp.headers.get("x-latency-ms")) / 1000).toFixed(1)} s`;
      loadHistory().catch(() => {});
    } catch (e) {
      $("tts-error").textContent = e.message; $("tts-error").hidden = false; $("tts-meta").textContent = "";
    } finally {
      count();
    }
  }

  function curl() {
    const b = { ...body(), input: $("tts-text").value.slice(0, 200) };
    const cmd = `curl -s http://${location.hostname}${cfg.gateway}/v1/audio/speech -H 'Content-Type: application/json' -d '${JSON.stringify(b).replace(/'/g, "'\\''")}' -o speech.${b.response_format}`;
    navigator.clipboard.writeText(cmd).then(() => { $("tts-meta").textContent = "curl copied (no key; add -H 'Authorization: Bearer dsk_…' if required)"; });
  }

  async function loadHistory() {
    const h = await api("/api/tts/history?days=14");
    const ms = (v) => (v === null ? "—" : `${(v / 1000).toFixed(1)} s`);
    const trs = h.daily.slice().reverse().map((d) => el("tr", {}, el("td", { text: d.day }),
      el("td", { class: "num", text: fmt.num(d.requests) }), el("td", { class: "num", text: fmt.num(d.errors) }),
      el("td", { class: "num", text: fmt.num(d.chars) }), el("td", { class: "num", text: `${Math.round(d.audio_s)} s` }),
      el("td", { class: "num", text: ms(d.p50_ms) }), el("td", { class: "num", text: ms(d.p95_ms) })));
    $("tts-daily").querySelector("tbody").replaceChildren(...(trs.length ? trs : [el("tr", {}, el("td", { colspan: 7, class: "muted", text: "No requests yet." }))]));
    const a = h.availability;
    $("tts-availability").textContent = a.ratio === null ? "" : `availability ${(a.ratio * 100).toFixed(1)}%`;
  }

  $("tts-text").addEventListener("input", count);
  $("tts-form").addEventListener("submit", speak);
  $("tts-curl").addEventListener("click", curl);
  load().catch((e) => { $("tts-disabled").textContent = e.message; $("tts-disabled").hidden = false; });
  loadHistory().catch(() => {});
  setInterval(() => load().catch(() => {}), 10000);
})();
