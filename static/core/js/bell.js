// The bell in the top bar: the unread count, refreshed every minute while the tab is visible,
// and the latest notifications with what needs attention, loaded when it opens.
import { api } from "./api.js";
import { spinner } from "./loading.js";

const EVERY = 60 * 1000;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

function setCount(bell, unread) {
  const badge = bell.querySelector("[data-bell-count]");
  badge.hidden = !unread;
  badge.textContent = unread > 99 ? "99+" : String(unread);
  bell.querySelector("[data-bell-read-all]").hidden = !unread;
}

function icon(bell, name) {
  const source = bell.querySelector(`[data-bell-icons]`).content.querySelector(`[data-icon="${name}"]`)
    || bell.querySelector(`[data-bell-icons]`).content.querySelector(`[data-icon="bell"]`);
  return source ? source.firstElementChild.cloneNode(true) : document.createElement("span");
}

function heading(value) {
  return text("p", "px-4 pt-3 pb-1 text-xs font-medium text-slate-400", value);
}

function render(bell, data) {
  const words = bell.querySelector("[data-bell-strings]").dataset;
  const body = bell.querySelector("[data-bell-body]");
  body.replaceChildren();
  setCount(bell, data.unread);

  if (data.attention.length) {
    body.append(heading(words.attention));
    for (const item of data.attention) {
      const link = document.createElement("a");
      link.href = item.url;
      link.className = "flex items-center gap-3 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50";
      const tile = document.createElement("span");
      tile.className = "grid size-7 shrink-0 place-items-center rounded-md bg-amber-50 text-amber-700";
      tile.append(icon(bell, item.icon));
      link.append(tile, text("span", "min-w-0 flex-1 truncate", item.label),
        text("span", "badge badge-warning num", String(item.count)));
      body.append(link);
    }
  }
  if (data.results.length) {
    if (data.attention.length) body.append(heading(words.latest));
    for (const item of data.results) {
      const link = document.createElement("a");
      link.href = item.url || "#";
      link.className = "notice";
      link.dataset.notice = `${bell.dataset.endpoint}${item.id}/read/`;
      if (!item.read) link.dataset.unread = "";
      const tile = document.createElement("span");
      tile.className = "notice-icon";
      tile.append(icon(bell, item.icon));
      const words_ = document.createElement("span");
      words_.className = "min-w-0 flex-1";
      words_.append(text("span", "block text-sm font-medium text-slate-900", item.title));
      if (item.body) words_.append(text("span", "mt-0.5 block text-xs text-slate-600", item.body));
      words_.append(text("span", "mt-1 block text-[11px] text-slate-400", item.ago));
      link.append(tile, words_);
      body.append(link);
    }
  }
  if (!data.attention.length && !data.results.length) {
    const empty = document.createElement("div");
    empty.className = "flex flex-col items-center gap-1 px-4 py-10 text-center";
    const tile = document.createElement("span");
    tile.className = "mb-2 grid size-10 place-items-center rounded-full bg-slate-100 text-slate-400";
    tile.append(icon(bell, "bell"));
    empty.append(tile, text("p", "text-sm font-medium text-slate-700", words.empty),
      text("p", "text-xs text-slate-500", words.emptyText));
    body.append(empty);
  }
}

async function load(bell) {
  const body = bell.querySelector("[data-bell-body]");
  if (!body.childElementCount) {
    const wait = document.createElement("div");
    wait.className = "flex justify-center py-10 text-slate-400";
    wait.append(spinner("size-5"));
    body.append(wait);
  }
  try {
    render(bell, await api(bell.dataset.endpoint, { quiet: true }));
  } catch {
    const words = bell.querySelector("[data-bell-strings]").dataset;
    body.replaceChildren(text("p", "px-4 py-8 text-center text-sm text-red-600", words.failed));
  }
}

async function refresh(bell) {
  if (document.hidden) return;
  try {
    const data = await api(`${bell.dataset.endpoint}?count=1`, { quiet: true });
    setCount(bell, data.unread);
  } catch {
    // Offline for a moment: the next round will try again.
  }
}

function setOpen(bell, open) {
  bell.querySelector("[data-bell-panel]").hidden = !open;
  bell.querySelector("[data-bell-toggle]").setAttribute("aria-expanded", String(open));
  if (open) load(bell);
}

/** Opening a notification marks it read first (the next page shows the new count). */
export function openNotice(link, event) {
  if (!link.hasAttribute("data-unread")) return;
  event.preventDefault();
  link.removeAttribute("data-unread");
  const go = () => {
    if (link.getAttribute("href") !== "#") window.location.assign(link.href);
  };
  api(link.dataset.notice, { method: "POST", quiet: true }).then(go, go);
}

export function initBell() {
  const bell = document.querySelector("[data-bell]");
  if (!bell) return;
  setCount(bell, Number(bell.querySelector("[data-bell-count]").textContent.replace("+", "")) || 0);

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-bell-toggle]");
    const panel = bell.querySelector("[data-bell-panel]");
    if (toggle && bell.contains(toggle)) {
      setOpen(bell, panel.hidden);
    } else if (!bell.contains(event.target)) {
      if (!panel.hidden) setOpen(bell, false);
    }
    const notice = event.target.closest("[data-notice]");
    if (notice) openNotice(notice, event);
    if (event.target.closest("[data-bell-read-all]")) {
      api(bell.dataset.readAll, { method: "POST", quiet: true }).then(() => load(bell));
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !bell.querySelector("[data-bell-panel]").hidden) {
      setOpen(bell, false);
      bell.querySelector("[data-bell-toggle]").focus();
    }
  });
  window.setInterval(() => refresh(bell), EVERY);
  document.addEventListener("visibilitychange", () => refresh(bell));
}
