// Pinning screens: the star in the top bar pins or unpins the current screen, and the sidebar's
// "Pinned" list follows at once (a copy of the screen's own sidebar link).
import { api } from "./api.js";
import { strings, toast } from "./ui.js";

function sidebarLink(screen) {
  const list = document.querySelector("[data-pinned-list]");
  if (!list) return;
  const existing = list.querySelector(`[data-screen="${CSS.escape(screen)}"]`);
  return { list, existing };
}

function addToSidebar(screen) {
  const { list, existing } = sidebarLink(screen) || {};
  if (!list || existing) return;
  const source = document.querySelector("#sidebar nav .nav-link[aria-current=page]");
  if (!source) return;
  const link = source.cloneNode(true);
  link.removeAttribute("aria-current");
  link.title = link.textContent.trim();
  const item = document.createElement("li");
  item.dataset.screen = screen;
  item.append(link);
  list.append(item);
  list.closest("[data-pinned]").hidden = false;
}

function removeFromSidebar(screen) {
  const { list, existing } = sidebarLink(screen) || {};
  if (!existing) return;
  existing.remove();
  list.closest("[data-pinned]").hidden = !list.childElementCount;
}

function show(button, pinned) {
  button.setAttribute("aria-pressed", String(pinned));
  const label = pinned ? button.dataset.unpinLabel : button.dataset.pinLabel;
  button.title = label;
  button.setAttribute("aria-label", label);
}

export function initShortcuts() {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-pin]");
    if (!button) return;
    const pinned = button.getAttribute("aria-pressed") !== "true";
    const screen = button.dataset.pin;
    show(button, pinned);
    try {
      await api(button.dataset.endpoint, { method: "POST", body: { screen, pinned }, quiet: true });
      if (pinned) addToSidebar(screen);
      else removeFromSidebar(screen);
      if (button.hasAttribute("data-pin-remove")) button.closest("[data-shortcut]")?.remove();
      toast(pinned ? strings().pinned : strings().unpinned);
    } catch (error) {
      show(button, !pinned);
      toast(error.message || strings().network, { kind: "error" });
    }
  });
}
