"use strict";
// Public backend descriptions drive presentation; no private paths or runtime details.
const EngineUI = {
  active(st) { return st?.engine?.backends?.[st.engine.active] || null; },
  label(st) {
    const b = this.active(st);
    if (!b) return ({none: "Off", unknown: "Unknown", conflict: "Conflict"})[st?.engine?.active] || "Off";
    const ref = b.operations.includes("multi-model") ? st.selected : b.reference;
    return b.label + (ref ? " · " + ref : "");
  },
  context(n) { return n ? (n % 1024 === 0 ? n / 1024 + "K" : String(n)) : ""; },
  options(st, models = []) {
    const rows = [];
    for (const b of Object.values(st?.engine?.backends || {})) {
      if (!b.enabled) continue;
      if (b.operations.includes("multi-model")) {
        rows.push(...models.map(m => ({value: b.id + ":" + m.name,
          label: b.label + " · " + m.name + (m.selected ? " (last used)" : "")})));
      } else {
        rows.push(...(b.targets || []).map(t => ({value: t.value,
          label: t.label + (t.max_context ? " · " + this.context(t.max_context) + " context" : "")})));
      }
    }
    return [...rows, {value: "off", label: "Off"}];
  },
  defaultTarget(st, models = []) {
    const options = this.options(st, models);
    if (this.active(st)?.exclusive) {
      const model = models.find(m => m.selected) || models[0];
      const backend = Object.values(st?.engine?.backends || {}).find(b => b.enabled && b.operations.includes("multi-model"));
      if (model && backend) return backend.id + ":" + model.name;
    }
    const current = st?.engine?.active;
    return (options.find(t => t.value.split(":")[0] !== current) || options.at(-1)).value;
  },
  canUseModelActions(st) {
    return st?.engine?.active === "none" || Boolean(this.active(st)?.operations.includes("multi-model"));
  },
  badge(st) {
    const b = this.active(st);
    const states = {ready: "Ready", absent: "Stopped", starting: "Loading", released: "Idle · weights released",
      recovering: "Recovering", unknown: "Unknown"};
    return b ? b.label + " · " + (states[b.state] || b.state) : this.label(st);
  },
  chat(st) {
    const b = this.active(st);
    if (!b?.enabled || !b.operations.includes("chat")) return {ok: false,
      text: b ? b.label + " owns the GPU. Switch engines on the Models page." : "The model is not serving."};
    if (!b.ready) return {ok: false, text: this.badge(st) + ". Chat opens when loaded readiness is verified."};
    return {ok: true, text: b.note || ""};
  },
  quality(st, kind, scope) {
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
