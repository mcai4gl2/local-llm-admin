"use strict";
const history = [];
let controller = null;
const $ = (id) => document.getElementById(id);

onStatus((st) => {
  const serving = st.state === "serving";
  const box = $("chat-disabled");
  box.hidden = serving;
  box.textContent = st.engine?.active === "comfyui"
    ? "The LLM is stopped because ComfyUI owns the GPU. Switch engines on the Models page."
    : "The model is not serving. Chat is disabled.";
  $("btn-send").disabled = !serving || controller !== null;
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
  if (!text || controller) return;
  $("prompt").value = "";
  history.push({ role: "user", content: text });
  addMessage("user", text);
  const node = addMessage("assistant");
  const body = node.querySelector(".body");
  let reasoning = null, answer = "";
  const req = {
    messages: history, system: $("system").value || null,
    temperature: $("temperature").value === "" ? null : Number($("temperature").value),
    max_tokens: $("max_tokens").value === "" ? null : Number($("max_tokens").value),
    thinking: $("thinking").value === "" ? null : $("thinking").value === "true",
  };
  controller = new AbortController();
  $("btn-stop").hidden = false; $("btn-send").disabled = true;
  try {
    const resp = await fetch("/api/chat", { method: "POST", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify(req) });
    if (!resp.ok) {
      const b = await resp.json().catch(() => ({}));
      throw new Error(typeof b.detail === "string" ? b.detail : `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!raw.startsWith("data: ")) continue;
        const ev = JSON.parse(raw.slice(6));
        if (ev.type === "reasoning") {
          if (!reasoning) { reasoning = el("pre", { class: "log wrap" }); node.prepend(el("details", {}, el("summary", { text: "Thinking" }), reasoning)); }
          reasoning.textContent += ev.content;
        } else if (ev.type === "delta") {
          answer += ev.content; body.textContent = answer;
        } else if (ev.type === "done") {
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
    node.append(el("div", { class: e.name === "AbortError" ? "meta" : "error", text: e.name === "AbortError" ? "Stopped." : e.message }));
  } finally {
    if (answer) history.push({ role: "assistant", content: answer });
    else history.pop();
    controller = null; $("btn-stop").hidden = true; $("btn-send").disabled = false;
  }
}

$("chat-form").addEventListener("submit", send);
$("prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) send(e); });
$("btn-stop").addEventListener("click", () => controller?.abort());
$("btn-clear").addEventListener("click", () => { history.length = 0; $("messages").replaceChildren(); $("chat-stats").textContent = ""; });
