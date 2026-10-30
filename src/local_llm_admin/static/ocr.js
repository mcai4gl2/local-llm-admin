let ocrStatus = null, ocrResult = null, ocrAbort = null;
const ocrForm = document.getElementById("ocr-form");
const ocrInput = document.getElementById("ocr-document");
const ocrError = document.getElementById("ocr-error");

function ocrButtons() {
  document.getElementById("ocr-run").disabled = !!ocrAbort || !ocrInput.files.length || !ocrStatus || !!ocrStatus.reason;
  document.getElementById("ocr-cancel").hidden = !ocrAbort;
  document.getElementById("ocr-progress").hidden = !ocrAbort;
}

async function loadOcrStatus() {
  try {
    ocrStatus = await api("/api/ocr");
    const admission = ocrStatus.admission;
    document.getElementById("ocr-state").textContent = ocrStatus.reason
      ? "OCR unavailable: " + ocrStatus.reason.replaceAll("_", " ")
      : admission.uncertain ? "OCR work may still be executing; restart the service."
      : admission.active ? "Recognizing a document · " + admission.queued + " queued" : "Ready";
    document.getElementById("ocr-limits").textContent = "Up to " + fmt.bytes(ocrStatus.limits.max_bytes) + " and " + ocrStatus.limits.max_pages + " PDF pages per document.";
    if (admission.uncertain) ocrStatus.reason = "work state unknown";
  } catch (error) {
    ocrStatus = null;
    document.getElementById("ocr-state").textContent = "OCR status unavailable: " + error.message;
  }
  ocrButtons();
}

function clearOcr() {
  ocrResult = null;
  document.getElementById("ocr-output").hidden = true;
  document.getElementById("ocr-results").replaceChildren();
  ocrError.hidden = true;
}

ocrForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (ocrAbort || document.getElementById("ocr-run").disabled) return;
  const file = ocrInput.files[0];
  if (file.size > ocrStatus.limits.max_bytes) {
    ocrError.textContent = "Document exceeds the upload byte limit."; ocrError.hidden = false; return;
  }
  clearOcr();
  const controller = new AbortController();
  ocrAbort = controller; ocrButtons();
  document.getElementById("ocr-progress-text").textContent = "Queued or recognizing document…";
  const body = new FormData(); body.append("document", file); body.append("output", "json");
  try {
    ocrResult = await api("/api/ocr/recognize", { method: "POST", body, signal: controller.signal });
    document.getElementById("ocr-results").replaceChildren(...ocrResult.pages.map(page =>
      el("details", { open: true, class: "ocr-page" },
        el("summary", { text: "Page " + page.number + (page.empty ? " · empty" : "") }),
        el("pre", { text: page.markdown }))));
    document.getElementById("ocr-result-meta").textContent = ocrResult.pages.length + " page(s) · " + ocrResult.model;
    document.getElementById("ocr-output").hidden = false;
    document.getElementById("ocr-progress-text").textContent = "Recognition complete.";
  } catch (error) {
    ocrResult = null;
    ocrError.textContent = error.name === "AbortError"
      ? "Cancellation requested. The service stays busy until downstream work stops."
      : (error.body?.detail?.message || error.message);
    ocrError.hidden = false;
    document.getElementById("ocr-progress-text").textContent = "";
  } finally { ocrAbort = null; ocrButtons(); loadOcrStatus(); }
});

document.getElementById("ocr-cancel").addEventListener("click", () => ocrAbort?.abort());
document.getElementById("ocr-clear").addEventListener("click", () => { ocrAbort?.abort(); clearOcr(); ocrInput.value = ""; ocrButtons(); });
ocrInput.addEventListener("change", ocrButtons);
for (const [mode, extension, media] of [["text", "txt", "text/plain"], ["markdown", "md", "text/markdown"], ["json", "json", "application/json"]]) {
  document.getElementById("ocr-download-" + mode).addEventListener("click", () => {
    if (!ocrResult) return;
    const content = mode === "json" ? JSON.stringify(ocrResult, null, 2) : ocrResult[mode];
    const url = URL.createObjectURL(new Blob([content], { type: media + ";charset=utf-8" }));
    const anchor = el("a", { href: url, download: "ocr-result." + extension });
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
window.addEventListener("pagehide", () => { ocrAbort?.abort(); ocrResult = null; });
loadOcrStatus();
setInterval(loadOcrStatus, 5000);
