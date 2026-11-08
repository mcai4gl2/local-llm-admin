"use strict";

const EXAMPLES = {
  hanfu: {
    template: "turbo-t2i", size: "1920x1088",
    prompt: "一位25岁左右的年轻成年东方女性，清丽的东方古典面容，乌黑长发，淡青与月白丝绸汉服，站在烟雨江南园林的青石桥旁，白墙黛瓦，竹林，荷花，清晨薄雾，回眸浅笑。电影级人像，85mm，浅景深，低饱和，照片级真实。",
  },
  gown: {
    template: "turbo-t2i", size: "1088x1920",
    prompt: "一位25岁左右的年轻成年亚洲女性，五官协调，真实皮肤纹理，乌黑长发。黑色修身丝绸晚礼服，自然站立，一手放在腰间，直视镜头，自信而克制。高端时尚杂志人像，柔和主光，浅景深，专业摄影棚，照片级真实。",
  },
  shrine: {
    template: "anima-turbo", size: "832x1216",
    prompt: "1girl, adult, shrine maiden, red hakama, white kosode, long black hair, hair ornament, standing, torii, maple leaves, autumn afternoon, looking at viewer, gentle smile",
  },
  city: {
    template: "anima-turbo", size: "832x1216",
    prompt: "1girl, adult, city pop, bob cut, earrings, summer dress, tokyo night, neon sign, convenience store, film grain, looking at viewer",
  },
  pilot: {
    template: "anima-turbo", size: "1024x1024",
    prompt: "1girl, adult, mecha pilot, flight suit, short hair, cockpit, instrument glow, serious expression, anime screenshot",
  },
  lantern: {
    template: "anima-turbo", size: "832x1216",
    prompt: "1girl, adult, kimono, floral pattern, hair up, paper lantern, evening street, soft smile, looking back",
  },
};

const form = document.getElementById("img-form");
const templateSel = document.getElementById("img-template");
const modelSel = document.getElementById("img-model");
const sizeSel = document.getElementById("img-size");
const note = document.getElementById("img-template-note");
const refs = document.getElementById("img-refs");
const err = document.getElementById("img-error");
const status = document.getElementById("img-status");
let catalog = null;

function showError(message) {
  err.hidden = !message;
  err.textContent = message || "";
}

function selectedTemplate() {
  return catalog.templates.find((item) => item.id === templateSel.value);
}

function fillSize(preferred) {
  sizeSel.replaceChildren(...catalog.sizes.map((size) => {
    const value = `${size.width}x${size.height}`;
    return el("option", { value, text: size.label, selected: value === preferred });
  }));
}

async function refreshEngine() {
  const body = await api("/api/images/catalog");
  catalog = body;
  document.getElementById("img-note").textContent = body.checkpoint;
  document.getElementById("img-engine").textContent = body.comfyui_ready
    ? "ComfyUI is ready"
    : "ComfyUI is stopped. Switch the engine before generating. That stops MLX and the resident services.";
  if (!templateSel.options.length) {
    templateSel.replaceChildren(...body.templates.map((item) => el("option", { value: item.id, text: item.label })));
    modelSel.replaceChildren(...body.models.map((item) => el("option", { value: item.id, text: item.label })));
    fillSize("1024x1024");
    onTemplate();
  }
}

function onTemplate() {
  const item = selectedTemplate();
  note.textContent = item ? item.note : "";
  refs.hidden = !item || item.kind !== "edit";
  modelSel.disabled = Boolean(item && item.id === "anima-turbo");
  const limit = item && item.max_edge ? item.max_edge : 4096;
  for (const option of sizeSel.options) {
    const [w, h] = option.value.split("x").map(Number);
    option.hidden = w > limit || h > limit;
  }
  if (sizeSel.selectedOptions[0]?.hidden) sizeSel.value = "832x1216";
}

templateSel.addEventListener("change", onTemplate);
document.getElementById("img-example").addEventListener("change", (event) => {
  const example = EXAMPLES[event.target.value];
  if (!example) return;
  if (example.template) templateSel.value = example.template;
  onTemplate();
  document.getElementById("img-prompt").value = example.prompt;
  if ([...sizeSel.options].some((option) => option.value === example.size && !option.hidden)) {
    sizeSel.value = example.size;
  }
});

document.getElementById("img-switch").addEventListener("click", async () => {
  showError("");
  status.textContent = "Switching to ComfyUI…";
  try {
    const job = await api("/api/engine", { method: "POST", json: { target: "comfyui" } });
    for (;;) {
      const current = await api(`/api/jobs/${job.id}`);
      if (current.status !== "running") {
        if (current.status !== "succeeded") throw new Error(current.status || "switch failed");
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    status.textContent = "ComfyUI is starting.";
    await refreshEngine();
  } catch (error) {
    showError(error.message);
    status.textContent = "";
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const [width, height] = sizeSel.value.split("x").map(Number);
  const item = selectedTemplate();
  const files = [...document.getElementById("img-files").files];
  if (item.kind === "edit" && (files.length < 1 || files.length > 10)) {
    showError("Choose 1 to 10 reference images.");
    return;
  }
  status.textContent = "Sending…";
  document.getElementById("img-go").disabled = true;
  try {
    const images = [];
    for (const file of item.kind === "edit" ? files : []) {
      const body = new FormData();
      body.append("file", file, file.name);
      const uploaded = await api("/api/images/upload", { method: "POST", body });
      images.push(uploaded.name);
    }
    const queued = await api("/api/images/generate", {
      method: "POST",
      json: {
        template: templateSel.value,
        prompt: document.getElementById("img-prompt").value,
        negative: "",
        width, height,
        seed: Number(document.getElementById("img-seed").value),
        model: modelSel.value,
        enhancer: document.getElementById("img-enhancer").checked,
        images,
      },
    });
    status.textContent = "Generating. The first image also loads the weights.";
    const imagesOut = await waitFor(queued.prompt_id);
    const result = document.getElementById("img-result");
    result.replaceChildren(...imagesOut.map((image) => {
      const params = new URLSearchParams(image);
      const img = el("img", { src: `/api/images/view?${params}`, alt: "Generated image" });
      return el("div", { class: "shot" }, img);
    }));
    status.textContent = "Done.";
  } catch (error) {
    showError(error.message);
    status.textContent = "";
  } finally {
    document.getElementById("img-go").disabled = false;
  }
});

async function waitFor(promptId) {
  for (let i = 0; i < 240; i += 1) {
    const job = await api(`/api/images/jobs/${promptId}`);
    if (job.state === "error") throw new Error(job.message || "generation failed");
    if (job.state === "done" && job.images.length) return job.images;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  throw new Error("generation timed out");
}

refreshEngine().catch((error) => showError(error.message));
