// The notifications page: mark all read, and the member's own on/off choices per kind.
import { api } from "../../core/js/api.js";
import { busy } from "../../core/js/loading.js";
import { strings, toast } from "../../core/js/ui.js";

const readAll = document.querySelector("[data-read-all-page]");
readAll?.addEventListener("click", async () => {
  const done = busy(readAll);
  try {
    await api(readAll.dataset.readAllPage, { method: "POST" });
    window.location.reload();
  } catch {
    done();
    toast(strings().network, { kind: "error" });
  }
});

const preferences = document.querySelector("[data-preferences]");
preferences?.addEventListener("click", async (event) => {
  const toggle = event.target.closest("[role=switch]");
  if (!toggle) return;
  toggle.setAttribute("aria-checked", String(toggle.getAttribute("aria-checked") !== "true"));
  const muted = [...preferences.querySelectorAll("[role=switch][aria-checked=false]")]
    .map((node) => node.dataset.kind);
  try {
    await api(preferences.dataset.preferences, { method: "PUT", body: { muted }, quiet: true });
  } catch {
    toggle.setAttribute("aria-checked", String(toggle.getAttribute("aria-checked") !== "true"));
    toast(strings().network, { kind: "error" });
  }
});
