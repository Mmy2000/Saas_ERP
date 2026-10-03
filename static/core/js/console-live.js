// Live traffic pages in the platform console. Every few seconds the page fetches its parts
// (server-rendered HTML, {"parts": {name: html}}) from [data-live-url] and swaps each into its
// [data-live-part]. Polling stops while the tab is hidden or paused, and backs off when the
// server cannot be reached. Also: switching the period without a reload, and the
// enable/disable switches (with a confirmation before disabling).
import { toast } from "./ui.js";

const INTERVAL = 5000;
const MAX_BACKOFF = 30000;
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const DOTS = { live: "bg-emerald-500", paused: "bg-slate-400", offline: "bg-amber-500" };

const fill = (template, values) => (template || "").replace(/%\((\w+)\)s/g, (_, key) => values[key] ?? "");

function readStrings() {
  try {
    return JSON.parse(document.getElementById("live-strings").textContent);
  } catch {
    return {};
  }
}

function init(root) {
  const strings = readStrings();
  const status = document.querySelector("[data-live-status]");
  const text = status?.querySelector("[data-live-text]");
  const ping = status?.querySelector("[data-live-ping]");
  const dot = status?.querySelector("[data-live-dot]");
  const toggle = status?.querySelector("[data-live-toggle]");
  let paused = false;
  let busy = false; // an enable/disable request is running: don't swap the switch away under it
  let failures = 0;
  let timer = null;
  let inflight = null;

  function show(state) {
    if (!status) return;
    status.dataset.state = state;
    ping.hidden = state !== "live";
    dot.className = `relative inline-flex size-2 rounded-full ${DOTS[state]}`;
    text.textContent = state === "live" ? fill(strings.updated, { time: TIME.format(new Date()) }) : strings[state];
    toggle.textContent = paused ? strings.resume : strings.pause;
  }

  function schedule() {
    clearTimeout(timer);
    if (paused || document.hidden) return;
    const delay = failures ? Math.min(MAX_BACKOFF, INTERVAL * 2 ** failures) : INTERVAL;
    timer = setTimeout(refresh, delay);
  }

  async function refresh() {
    clearTimeout(timer);
    inflight?.abort();
    const controller = new AbortController();
    inflight = controller;
    try {
      const url = `${root.dataset.liveUrl}?window=${encodeURIComponent(root.dataset.window)}`;
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        signal: controller.signal,
      });
      // A redirect means the session ended (to the login page): reload to show it.
      if (response.redirected) return window.location.reload();
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      if (!busy) {
        for (const [name, html] of Object.entries(data.parts)) {
          const node = document.querySelector(`[data-live-part="${name}"]`);
          if (node) node.innerHTML = html;
        }
      }
      failures = 0;
      show(paused ? "paused" : "live");
    } catch (error) {
      if (error.name === "AbortError") return;
      failures += 1;
      show("offline");
    } finally {
      if (inflight === controller) {
        inflight = null;
        schedule();
      }
    }
  }

  toggle?.addEventListener("click", () => {
    paused = !paused;
    if (paused) {
      clearTimeout(timer);
      inflight?.abort();
      show("paused");
    } else {
      refresh();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(timer);
    else if (!paused) refresh();
  });

  // Period switch: no reload, the URL keeps the choice.
  document.addEventListener("click", (event) => {
    const link = event.target.closest(".segmented a[href^='?window=']");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    const key = new URL(link.href).searchParams.get("window");
    root.dataset.window = key;
    for (const other of link.parentElement.querySelectorAll("a")) {
      const active = other === link;
      other.classList.toggle("segment-active", active);
      if (active) other.setAttribute("aria-current", "page");
      else other.removeAttribute("aria-current");
    }
    history.replaceState(null, "", `?window=${encodeURIComponent(key)}`);
    const limitForm = document.querySelector("[data-limit-form]");
    if (limitForm) limitForm.setAttribute("action", `?window=${encodeURIComponent(key)}`);
    refresh();
  });

  // Enable / disable a client.
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-access-form]");
    if (!form) return;
    event.preventDefault();
    if (form.dataset.enabled === "1" && !(await confirmDisable(form.dataset.name, strings))) return;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    busy = true;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.error?.message || strings.failed);
      toast(data.message);
    } catch (error) {
      toast(error.message || strings.failed, { kind: "error" });
      button.disabled = false;
    } finally {
      busy = false;
      refresh();
    }
  });

  show("live");
  schedule();
}

function confirmDisable(name, strings) {
  const title = fill(strings.disable_title, { name });
  const dialog = document.querySelector("[data-confirm-dialog]");
  if (!dialog || typeof dialog.showModal !== "function") return Promise.resolve(window.confirm(title));
  dialog.querySelector("[data-confirm-title]").textContent = title;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

const root = document.querySelector("[data-live]");
if (root) init(root);
