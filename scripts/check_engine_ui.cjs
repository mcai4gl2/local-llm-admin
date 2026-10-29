"use strict";
// Dependency-free decision checks; run with node scripts/check_engine_ui.cjs.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/local_llm_admin/static/engine_ui.js"), "utf8"), context);
const ui = vm.runInContext("EngineUI", context);
const backends = {
  mlx: {id: "mlx", label: "MLX", enabled: true, operations: ["chat", "quality", "multi-model"], ready: false, state: "absent", model_sidecars: true, targets: []},
  comfyui: {id: "comfyui", label: "ComfyUI", enabled: true, operations: ["queue"], exclusive: true, state: "absent", targets: [{value: "comfyui", label: "ComfyUI"}]},
  splash: {id: "splash", label: "Splash", enabled: true, operations: ["chat", "quality", "profiles"], exclusive: true, ready: true, state: "ready", reference: "stock-8bit-32k", note: "Reasoning off is unreliable. Gateway compatibility is unverified.", targets: [
    {value: "splash:stock-8bit-32k", label: "Splash · stock-8bit", max_context: 32768, reasoning: true},
    {value: "splash:modified-4bit-32k", label: "Splash · modified-4bit", max_context: 32768}]}
};
const st = {engine: {active: "splash", backends}, selected: "model-a"};
const models = [{name: "model-a", selected: true}, {name: "model-b", selected: false}];
let options = ui.options(st, models);
assert.equal(options.filter(x => x.value.startsWith("splash:")).length, 2);
assert.equal(options.find(x => x.value === "splash:stock-8bit-32k").label.includes("32K"), true);
assert.equal(options.some(x => x.value === "mlx:model-a"), true);
assert.equal(options.at(-1).value, "off");
assert.equal(ui.defaultTarget(st, models), "mlx:model-a");
assert.equal(ui.label(st).includes("stock-8bit-32k"), true);
assert.equal(ui.chat(st).ok, true);
assert.equal(ui.reasoningDefault(st), true);
assert.equal(ui.quality(st, "compatibility", "current").ok, true);
assert.equal(ui.quality(st, "eval", "current").ok, true);
assert.equal(ui.quality(st, "benchmark", "current").ok, false);
assert.equal(ui.quality(st, "eval", "all").ok, false);
assert.equal(ui.prompt(st, "splash:modified-4bit-32k").body.includes("unverified"), true);
assert.equal(ui.canUseModelActions(st), false);
for (const state of ["starting", "released", "recovering", "unknown"]) {
  backends.splash.state = state; backends.splash.ready = false;
  assert.equal(ui.chat(st).ok, false, state);
  assert.equal(ui.quality(st, "compatibility", "current").ok, false, state);
  assert.equal(ui.badge(st).includes("Splash"), true);
}
backends.splash.enabled = false;
assert.equal(ui.options(st, models).some(x => x.value.startsWith("splash:")), false);
assert.equal(ui.chat(st).ok, false);
assert.equal(ui.prompt(st, "comfyui").body.includes("LLM"), true);
st.engine.active = "comfyui";
assert.equal(ui.chat(st).ok, false);
assert.equal(ui.quality(st, "eval", "current").ok, false);
st.engine.active = "mlx"; backends.mlx.ready = true; backends.mlx.state = "ready";
assert.equal(ui.canUseModelActions(st), true);
assert.equal(ui.quality(st, "eval", "all").handled, false);
for (const state of ["unknown", "conflict"]) {
  st.engine.active = state;
  assert.equal(ui.chat(st).ok, false);
  assert.equal(ui.canUseModelActions(st), false);
}
assert.equal(ui.runtimeLabel({original: {engine: "splash"}, runtime_version: "1.4.0"}), "splash 1.4.0");
assert.equal(ui.runtimeLabel({original: {engine: "mlx"}, runtime_version: null}), "unavailable");
console.log("Engine UI decisions: all checks passed.");
