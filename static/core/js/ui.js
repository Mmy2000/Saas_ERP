// Shell behaviour: sidebar, toasts, one-shot flash messages across a redirect.

const FLASH_KEY = "gweb:flash";

export function strings() {
  const node = document.getElementById("ui-strings");
  try {
    return node ? JSON.parse(node.textContent) : {};
  } catch {
    return {};
  }
}

const ICONS = {
  info: '<path d="M20 6 9 17l-5-5"/>',
  error: '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>',
};

export function toast(message, { kind = "info", timeout = 4500 } = {}) {
  const host = document.getElementById("toasts");
  if (!host || !message) return;
  const node = document.createElement("div");
  node.className = "toast" + (kind === "error" ? " toast-error" : "");
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  const tone = kind === "error" ? "text-red-500" : "text-emerald-500";
  node.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mt-px size-[18px] shrink-0 ${tone}"
    aria-hidden="true">${ICONS[kind === "error" ? "error" : "info"]}</svg><span class="flex-1"></span>`;
  node.querySelector("span").textContent = message;
  host.append(node);
  setTimeout(() => {
    node.style.transition = "opacity 200ms, transform 200ms";
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

export function flash(message) {
  try {
    sessionStorage.setItem(FLASH_KEY, message);
  } catch {
    /* storage unavailable: the message is simply skipped */
  }
}

function showFlash() {
  try {
    const message = sessionStorage.getItem(FLASH_KEY);
    if (message) {
      sessionStorage.removeItem(FLASH_KEY);
      toast(message);
    }
  } catch {
    /* ignore */
  }
}

function setSidebar(open) {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.querySelector("[data-sidebar-backdrop]");
  if (!sidebar) return;
  sidebar.dataset.open = String(open);
  if (backdrop) backdrop.hidden = !open;
}

export function initShell() {
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-sidebar-open]")) setSidebar(true);
    else if (event.target.closest("[data-sidebar-close], [data-sidebar-backdrop]")) setSidebar(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setSidebar(false);
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-print]")) window.print();
  });
  // <input type="checkbox" data-check-all="item"> ticks every checkbox named "item";
  // [data-needs-checked="item"] buttons are enabled only while one is ticked.
  const syncChecked = () => {
    document.querySelectorAll("[data-needs-checked]").forEach((button) => {
      button.disabled = !document.querySelector(`input[name="${button.dataset.needsChecked}"]:checked`);
    });
  };
  document.addEventListener("change", (event) => {
    const all = event.target.closest("[data-check-all]");
    if (all) {
      document.querySelectorAll(`input[type="checkbox"][name="${all.dataset.checkAll}"]`)
        .forEach((box) => (box.checked = all.checked));
    }
    if (event.target.matches('input[type="checkbox"]')) syncChecked();
  });
  syncChecked();
  document.querySelectorAll("[data-toast]").forEach((node) => setTimeout(() => node.remove(), 4500));
  showFlash();
}
