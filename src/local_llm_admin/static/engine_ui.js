"use strict";
// Public backend descriptions drive presentation; no private paths or runtime details.
const EngineUI = {
  active(st) { return st?.engine?.backends?.[st.engine.active] || null; },
  currentTarget(st) {
    const b = this.active(st);
    if (!b) return st?.engine?.active === "none" ? "off" : "";
    const ref = b.operations.includes("multi-model") ? st.selected : b.reference;
    return b.id + (ref ? ":" + ref : "");
  },
  target(st, b = this.active(st)) {
    return b?.targets?.find(t => t.value === b.id + (b.reference ? ":" + b.reference : "")) || null;
  },
  targetLabel(b, target) {
    // Capacity is a separate fact. Raw identifiers stay in technical details.
    if (!target) return b.label + " · target unverified";
    let label = target.label || b.label;
    if (target.cohort) label = b.label + " · " + target.cohort;
    return label.replace(/(?:\s*[·—–-]\s*)?\b\d+(?:\.\d+)?[kKmM](?:\s+(?:total\s+)?context)?\b/g, "").trim();
  },
  label(st) {
    const b = this.active(st);
    if (!b) return ({none: "Off", unknown: "Unknown", conflict: "Conflict"})[st?.engine?.active] || "Unknown";
    if (b.operations.includes("multi-model")) return b.label + (st.selected ? " · " + st.selected : "");
    if (!b.operations.includes("profiles")) return b.label;
    return this.targetLabel(b, this.target(st, b));
  },
  context(n) { return n ? (n % 1024 === 0 ? n / 1024 + "K" : String(n)) : ""; },
  contexts(st, b = this.active(st)) {
    const configured = this.target(st, b)?.max_context ?? null;
    // Adapter max_context is populated only after its runtime verification.
    const loaded = b?.ready ? (b.id === "mlx" ? st?.health?.loaded_context_size : st?.engine?.[b?.id]?.max_context) ?? null : null;
    const state = !loaded ? "Unverified" : configured && configured !== loaded ? "Mismatch" : "Verified";
    return {configured, loaded, state};
  },
  options(st, models = []) {
    const rows = [];
    for (const b of Object.values(st?.engine?.backends || {})) {
      if (!b.enabled) continue;
      if (b.operations.includes("multi-model")) {
        rows.push(...models.map(m => ({value: b.id + ":" + m.name,
          label: b.label + " · " + m.name + (m.selected ? " (last used)" : "")})));
      } else {
        rows.push(...(b.targets || []).map(t => ({value: t.value,
          label: this.targetLabel(b, t) + (t.max_context ? " · " + this.context(t.max_context) + " context" : "")})));
      }
    }
    return [...rows, {value: "off", label: "Off"}];
  },
  defaultTarget(st) { return this.currentTarget(st); },
  fresh(st) { return Boolean(st && st._fresh !== false); },
  lifecycle(st) {
    if (!this.fresh(st)) return {ok: false, text: "Status is stale or unavailable. Waiting for a fresh check."};
    if (st.job || st.quality_active) return {ok: false, text: "An engine job or quality run is active. Wait for it to finish."};
    if (["unknown", "conflict"].includes(st.engine?.active)) return {ok: false,
      text: "Engine ownership is " + st.engine.active + ". Use the recovery controls when available."};
    const b = this.active(st);
    if (b && ["starting", "recovering", "unknown"].includes(b.state)) return {ok: false, text: this.badge(st) + ". Wait for readiness."};
    return {ok: true, text: ""};
  },
  canUseModelActions(st) {
    return this.lifecycle(st).ok && (st.engine?.active === "none" || Boolean(this.active(st)?.operations.includes("multi-model")));
  },
  tone(st, b = this.active(st)) {
    if (!this.fresh(st)) return "unknown";
    if (!b) return st?.engine?.active === "conflict" ? "error" : st?.engine?.active === "none" ? "stopped" : "unknown";
    if (b.state === "absent" || b.state === "disabled") return "stopped";
    if (b.ready) return "ready";
    return ["starting", "recovering", "released"].includes(b.state) ? "busy" : "unknown";
  },
  badge(st) {
    const b = this.active(st);
    const states = {ready: "Ready", absent: "Stopped", disabled: "Disabled", starting: "Loading", released: "Idle · weights released",
      recovering: "Recovering", unknown: "Unknown"};
    return b ? b.label + " · " + (states[b.state] || b.state) : this.label(st);
  },
  chat(st) {
    const gate = this.lifecycle(st);
    if (!gate.ok) return gate;
    const b = this.active(st);
    if (!b?.enabled || !b.operations.includes("chat")) return {ok: false,
      text: b ? b.label + " owns the GPU. Switch engines on the Models page." : "The model is not serving."};
    if (!b.ready) return {ok: false, text: this.badge(st) + ". Chat opens when loaded readiness is verified."};
    return {ok: true, text: b.note || ""};
  },
  quality(st, kind, scope) {
    const gate = this.lifecycle(st);
    if (!gate.ok) return {handled: true, ...gate};
    const b = this.active(st);
    if (b?.enabled && b.operations.includes("multi-model")) return {handled: false};
    if (st?.engine?.active === "none") return {handled: false};
    if (!b?.enabled || !b.operations.includes("quality") || !b.ready) return {
      handled: true, ok: false, text: this.badge(st) + ". Load an engine that supports quality checks first."};
    const ok = ["compatibility", "eval"].includes(kind) && scope === "current";
    return {handled: true, ok, off: false, text: ok
      ? "Runs " + kind + " on " + this.label(st) + "; nothing is switched. " + (b.note || "")
      : b.label + " supports current-engine compatibility checks and evals only. Use an external judge or none."};
  },
  wireLabel(value) {
    if (!value) return "—";
    const raw = String(value), split = raw.indexOf(":");
    const engine = split < 0 ? "" : raw.slice(0, split);
    const label = engine === "mlx" ? "MLX" : engine ? engine[0].toUpperCase() + engine.slice(1) : "";
    return this.targetLabel({label: label || "Target"}, {label: label ? label + " · " + raw.slice(split + 1) : raw});
  },
  runLabel(run) {
    const original = run.original || {}, settings = run.settings || {};
    const identities = original.target_identities || {};
    const selected = run.model || (run.scope === "current" ? original.name : null);
    const identity = identities[selected] || (Object.keys(identities).length === 1 ? Object.values(identities)[0] : original);
    const engine = (identity.engine || original.engine) === "none" ? "mlx" : identity.engine || original.engine;
    const label = engine ? engine === "mlx" ? "MLX" : engine[0].toUpperCase() + engine.slice(1) : "Engine unrecorded";
    const target = run.scope === "all" ? "all MLX models" : identity.target_label || run.model || original.target_label || original.reference || original.name || "target unrecorded";
    const raw = target.startsWith(engine + ":") ? target.slice(engine.length + 1) : target;
    const human = this.targetLabel({label}, {label: raw.startsWith(label + " · ") ? raw : label + " · " + raw});
    const context = identity.loaded_context || original.loaded_context;
    const contextText = run.scope === "all" && Object.keys(identities).length > 1 ? "per-target context in details" : context ? this.context(context) + " total context" : "context unrecorded";
    const thinking = settings.thinking ?? settings.enable_thinking;
    return [human, contextText,
      thinking == null ? "reasoning unrecorded" : "reasoning " + (thinking ? "on" : "off"),
      identity.profile_fingerprint ? "profile " + identity.profile_fingerprint.slice(0, 12) : null,
      run.runtime_version ? "v" + run.runtime_version : "version unrecorded",
      run.runtime_fingerprint ? "runtime " + run.runtime_fingerprint.slice(0, 12) : "runtime unrecorded"].filter(Boolean).join(" · ");
  },
  runtimeLabel(run) {
    return run.runtime_version ? (run.original?.engine || "engine") + " " + run.runtime_version : "unavailable";
  },
  reasoningDefault(st) {
    const b = this.active(st);
    return b?.targets?.find(t => t.value === b.id + ":" + b.reference)?.reasoning ?? null;
  },
  prompt(st, target) {
    if (target === "off") return {title: "Stop the running engines?", body: "In-flight requests may fail while the engines stop."};
    const id = target.split(":")[0], b = st?.engine?.backends?.[id];
    const option = this.options(st).find(t => t.value === target);
    const label = option?.label || (b?.label || id) + " · " + target.slice(id.length + 1);
    let body = "The current engine stops before the target starts. In-flight requests may fail.";
    if (b?.exclusive) body += " GPU sidecars and the embedding service stop.";
    if (b && !b.operations.includes("chat")) body += " LLM chat and inference will be unavailable.";
    if (b?.note) body += " " + b.note;
    return {title: "Run " + label + "?", body};
  },
};
