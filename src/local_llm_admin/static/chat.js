"use strict";
const history = [];
let controller = null;
let chatStatus = null;
let historyGeneration = 0, lastReplyTarget = null, configKey = null, configChecked = 0, configRevision = 0;
function syncChat() {
  const state = EngineUI.chat(chatStatus);
  $("btn-send").disabled = !state.ok || controller !== null;
  return state;
}
const $ = (id) => document.getElementById(id);

function replyLabel(identity) {
  if (!identity?.engine) return "Engine attribution unavailable";
  const backend = {label: identity.engine_label || identity.engine};
  return identity.target_label ? EngineUI.targetLabel(backend, {label: identity.target_label}) : backend.label + (identity.model || identity.reference ? " · " + (identity.model || identity.reference) : "");
}
async function loadChatConfig(st) {
  const target = EngineUI.currentTarget(st);
  if (!EngineUI.chat(st).ok) {
    configRevision++; configKey = null; configChecked = 0;
    $("chat-defaults").textContent = "Resolved defaults unavailable until serving readiness is verified.";
    $("chat-capacity").textContent = "Loaded context unverified.";
    return;
  }
  if (configKey === target && Date.now() - configChecked < 15000) return;
  const revision = ++configRevision;
  configKey = target; configChecked = Date.now();
  $("chat-defaults").textContent = "Resolving active engine defaults…";
  $("chat-capacity").textContent = "Checking loaded context…";
  try {
    const data = await api("/api/chat/config");
    if (revision !== configRevision) return;
    $("chat-defaults").textContent = replyLabel(data.identity) + " defaults: " + Object.entries(data.defaults).map(([k, v]) => `${k}=${v}`).join(" · ");
    const context = EngineUI.context(data.identity.loaded_context) || "Unverified";
    $("chat-capacity").textContent = `Loaded total context: ${context} (prompt plus output). Output field cap: ${fmt.num(data.output_cap)}; actual allowance also depends on the remaining context after your prompt.`;
    $("max_tokens").max = Math.min(data.output_cap, data.identity.loaded_context || data.output_cap);
  } catch (e) {
    if (revision === configRevision) { $("chat-defaults").textContent = "Resolved defaults unavailable: " + e.message; configKey = null; }
  }
}
function showReplyIdentity(node, identity, settings) {
  const caption = node.querySelector(".reply-identity");
  caption.textContent = replyLabel(identity);
  let details = node.querySelector(".reply-details");
  if (!details) { details = el("details", {class: "reply-details"}); node.append(details); }
  details.replaceChildren(el("summary", {text: "Serving identity and settings"}), el("pre", {text: JSON.stringify({identity, settings}, null, 2)}));
  if (identity?.target_value) lastReplyTarget = identity.target_value;
  else if (identity?.engine) lastReplyTarget = identity.engine + (identity.reference ? ":" + identity.reference : "");
  syncConversation();
}
function syncConversation() {
  const target = EngineUI.currentTarget(chatStatus);
  $("conversation-engine").textContent = "Next request: " + EngineUI.label(chatStatus);
  const changed = history.length > 0 && lastReplyTarget && target !== lastReplyTarget;
  $("chat-context-note").hidden = !changed;
  $("chat-context-note").textContent = "Engine changed. Conversation context is retained; the next reply is a new engine segment. Clear starts a new conversation.";
}

onStatus((st) => {
  chatStatus = st;
  syncConversation();
  loadChatConfig(st);
  const state = syncChat();
  const box = $("chat-disabled");
  box.hidden = state.ok;
  box.textContent = state.text;
  const note = $("chat-engine-note");
  if (note) { note.hidden = !state.ok || !state.text; note.textContent = state.text; }
  $("btn-send").disabled = !state.ok || controller !== null;
});

function addMessage(role, text = "") {
  const body = el("div", { class: "body", text });
  const node = el("div", { class: `msg ${role}` }, body);
  $("messages").append(node);
  $("messages").scrollTop = $("messages").scrollHeight;
  return node;
}

async function send(ev) {
  ev?.preventDefault();
  const text = $("prompt").value.trim();
  if (!text || controller || !syncChat().ok) return;
  $("prompt").value = "";
  history.push({ role: "user", content: text });
  addMessage("user", text);
  const requestGeneration = historyGeneration;
  const node = addMessage("assistant");
  node.prepend(el("div", {class: "reply-identity meta", text: "Awaiting resolved engine identity…"}));
  const body = node.querySelector(".body");
  let reasoning = null, answer = "";
  const req = {
    messages: history, system: $("system").value || null,
    temperature: $("temperature").value === "" ? null : Number($("temperature").value),
    max_tokens: $("max_tokens").value === "" ? null : Number($("max_tokens").value),
    thinking: $("thinking").value === "" ? null : $("thinking").value === "true",
  };
  const requestController = new AbortController();
  controller = requestController;
  $("btn-stop").hidden = false; $("btn-send").disabled = true;
  try {
    const resp = await fetch("/api/chat", { method: "POST", signal: requestController.signal,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify(req) });
    if (!resp.ok) {
      const b = await resp.json().catch(() => ({}));
      throw new Error(typeof b.detail === "string" ? b.detail : `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (requestGeneration !== historyGeneration) { await reader.cancel(); return; }
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!raw.startsWith("data: ")) continue;
        const ev = JSON.parse(raw.slice(6));
        if (ev.type === "identity") {
          showReplyIdentity(node, ev.identity, ev.settings);
        } else if (ev.type === "reasoning") {
          if (!reasoning) { reasoning = el("pre", { class: "log wrap" }); node.prepend(el("details", {}, el("summary", { text: "Thinking" }), reasoning)); }
          reasoning.textContent += ev.content;
        } else if (ev.type === "delta") {
          answer += ev.content; body.textContent = answer;
        } else if (ev.type === "done") {
          showReplyIdentity(node, ev.identity, ev.settings);
          const u = ev.usage || {};
          const used = Object.entries(ev.settings || {}).map(([k, v]) => `${k}=${v}`).join(", ");
          const meta = `prompt ${fmt.num(u.prompt_tokens)} · completion ${fmt.num(u.completion_tokens)} · TTFT ${ev.ttft_ms ?? "—"} ms · ${fmt.tok(ev.tok_s)}${used ? ` · ${used}` : ""}`;
          node.append(el("div", { class: "meta", text: meta }));
          $("chat-stats").textContent = `Last reply: ${meta}`;
        } else if (ev.type === "error") {
          node.append(el("div", { class: "error", text: ev.message }));
        }
        $("messages").scrollTop = $("messages").scrollHeight;
      }
    }
  } catch (e) {
    if (requestGeneration !== historyGeneration) return;
    node.append(el("div", { class: e.name === "AbortError" ? "meta" : "error", text: e.name === "AbortError" ? "Stopped." : e.message }));
  } finally {
    if (requestGeneration !== historyGeneration) return;
    if (answer) history.push({ role: "assistant", content: answer });
    else history.pop();
    controller = null; $("btn-stop").hidden = true; syncChat();
  }
}

$("chat-form").addEventListener("submit", send);
$("prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) send(e); });
$("btn-stop").addEventListener("click", () => controller?.abort());
$("btn-clear").addEventListener("click", () => {
  historyGeneration++; controller?.abort(); controller = null;
  history.length = 0; lastReplyTarget = null;
  $("messages").replaceChildren(); $("chat-stats").textContent = ""; $("btn-stop").hidden = true;
  syncChat(); syncConversation();
});
