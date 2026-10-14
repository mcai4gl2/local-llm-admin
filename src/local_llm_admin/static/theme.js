// Theme switch: "system" follows the OS (prefers-color-scheme); "light"/"dark" pin it.
// The saved choice is applied by the inline snippet in <head> before first paint.
"use strict";
(() => {
  const root = document.documentElement;
  const select = document.getElementById("theme-select");
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch (e) { /* storage blocked: follow the OS */ }
  if (select) {
    select.value = saved === "light" || saved === "dark" ? saved : "system";
    select.addEventListener("change", () => {
      const v = select.value;
      if (v === "system") delete root.dataset.theme; else root.dataset.theme = v;
      try {
        if (v === "system") localStorage.removeItem("theme"); else localStorage.setItem("theme", v);
      } catch (e) { /* not persisted; still applied for this page */ }
    });
  }
})();
