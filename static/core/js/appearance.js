// Appearance menu: theme (light / dark / system) and accent colour. Choices apply at once and
// are kept in cookies, so the server renders the next page the same way (apps/core/appearance.py).

const YEAR = 365 * 24 * 60 * 60;
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const root = document.documentElement;

function save(name, value) {
  const expiry = value ? `max-age=${YEAR}` : "max-age=0";
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; ${expiry}; SameSite=Lax`;
}

// A short colour fade while everything repaints (see html.theme-switching in app.css).
function repaint(change) {
  root.classList.add("theme-switching");
  change();
  window.setTimeout(() => root.classList.remove("theme-switching"), 220);
}

function resolvedTheme() {
  const pref = root.dataset.themePref || "system";
  return pref === "system" ? (dark.matches ? "dark" : "light") : pref;
}

function press(selector, attribute, value) {
  for (const button of document.querySelectorAll(selector)) {
    const on = button.getAttribute(attribute) === value;
    button.setAttribute("aria-pressed", String(on));
    if (button.classList.contains("segment")) button.classList.toggle("segment-active", on);
  }
}

function setTheme(pref) {
  repaint(() => {
    root.dataset.themePref = pref;
    root.dataset.theme = resolvedTheme();
  });
  save("gweb_theme", pref);
  press("[data-theme-choice]", "data-theme-choice", pref);
}

function setAccent(key) {
  const accent = key || root.dataset.accentDefault || "gold";
  repaint(() => {
    root.dataset.accent = accent;
  });
  save("gweb_accent", key);
  press("[data-accent-choice]:not([data-accent-choice=''])", "data-accent-choice", accent);
  for (const reset of document.querySelectorAll("[data-accent-choice='']")) reset.hidden = !key;
}

function setOpen(menu, open) {
  menu.querySelector("[data-appearance-panel]").hidden = !open;
  menu.querySelector("[data-appearance-toggle]").setAttribute("aria-expanded", String(open));
}

export function initAppearance() {
  // Follow the OS while the choice is "system".
  dark.addEventListener("change", () => {
    if (root.dataset.themePref === "system") repaint(() => (root.dataset.theme = resolvedTheme()));
  });

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-appearance-toggle]");
    for (const menu of document.querySelectorAll("[data-appearance]")) {
      if (toggle && menu.contains(toggle)) {
        setOpen(menu, menu.querySelector("[data-appearance-panel]").hidden);
      } else if (!menu.contains(event.target)) {
        setOpen(menu, false);
      }
    }
    const theme = event.target.closest("[data-theme-choice]");
    if (theme) setTheme(theme.dataset.themeChoice);
    const accent = event.target.closest("[data-accent-choice]");
    if (accent) setAccent(accent.dataset.accentChoice);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const menu of document.querySelectorAll("[data-appearance]")) {
      if (!menu.querySelector("[data-appearance-panel]").hidden) {
        setOpen(menu, false);
        menu.querySelector("[data-appearance-toggle]").focus();
      }
    }
  });
}
