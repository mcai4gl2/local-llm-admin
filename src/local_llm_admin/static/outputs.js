// ComfyUI outputs (M5): paged, read-only gallery. Media load lazily from the admin.
"use strict";
const LIMIT = 48;
let offset = 0;
let last = null;

function contentUrl(item, download = false) {
  return `/api/comfyui/outputs/${encodeURIComponent(item.id)}/content${download ? "?download=1" : ""}`;
}

function missing(media) {
  media.replaceChildren(el("span", { class: "bad", text: "File missing or replaced. Refresh the list." }));
}

function tile(item) {
  const url = contentUrl(item);
  const media = el("div", { class: "media" });
  if (item.kind === "image") {
    const img = el("img", { src: url, alt: item.name, loading: "lazy", decoding: "async" });
    img.addEventListener("error", () => missing(media), { once: true });
    media.append(el("a", { href: url, target: "_blank", rel: "noopener" }, img));
  } else {
    const video = el("video", { src: url, controls: "", preload: "metadata", playsinline: "" });
    video.addEventListener("error", () => missing(media), { once: true });
    media.append(video);
  }
  const job = item.job ? `job ${item.job.job_id.slice(0, 8)}` : null;
  return el("div", { class: "shot" }, media,
    el("div", { class: "meta" },
      el("div", { class: "name", text: item.subfolder ? `${item.subfolder}/${item.name}` : item.name }),
      el("div", { class: "muted", text: [fmt.bytes(item.size), fmt.time(item.mtime), job].filter(Boolean).join(" · ") }),
      el("div", { class: "links" },
        el("a", { href: url, target: "_blank", rel: "noopener", text: "Open" }),
        el("a", { href: contentUrl(item, true), text: "Download" }))));
}

async function load(refresh = false) {
  const err = document.getElementById("out-error");
  let body;
  try {
    body = await api(`/api/comfyui/outputs?offset=${offset}&limit=${LIMIT}${refresh ? "&refresh=true" : ""}`);
  } catch (e) {
    err.hidden = false; err.textContent = `Could not load outputs: ${e.message}`;
    return;
  }
  last = body;
  err.hidden = body.available; err.textContent = body.available ? "" : `Outputs unavailable: ${body.reason}`;
  if (body.available && offset > 0 && !body.items.length) { offset = 0; return load(); }  // list shrank
  document.getElementById("out-grid").replaceChildren(...body.items.map(tile));
  document.getElementById("out-empty").hidden = !body.available || body.total > 0;
  const notes = [];
  if (body.available) {
    notes.push(`${fmt.num(body.total)} file(s)`, `scanned ${fmt.clock(body.scanned_at)}`);
    if (body.truncated) notes.push("scan limit reached: older files may be missing");
    if (body.skipped) notes.push(`${fmt.num(body.skipped)} other file(s) hidden`);
    if (body.history === "unavailable") notes.push("ComfyUI history unavailable");
  }
  document.getElementById("out-meta").textContent = notes.join(" · ");
  const shown = body.items.length;
  document.getElementById("out-page").textContent = shown ? `${offset + 1}–${offset + shown} of ${fmt.num(body.total)}` : "";
  document.getElementById("out-prev").disabled = offset === 0;
  document.getElementById("out-next").disabled = !body.available || body.next_offset === null;
}

document.getElementById("out-refresh").addEventListener("click", () => load(true));
document.getElementById("out-prev").addEventListener("click", () => { offset = Math.max(0, offset - LIMIT); load(); window.scrollTo(0, 0); });
document.getElementById("out-next").addEventListener("click", () => {
  if (last?.next_offset !== null && last?.next_offset !== undefined) { offset = last.next_offset; load(); window.scrollTo(0, 0); }
});
load(true);
