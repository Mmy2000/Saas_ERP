// Loading states. Pages are rendered by the server, so the wait is mostly between pages:
//
// * A thin bar at the top while another page, or a request made by the page (api()), is on
//   its way. It only appears when the wait is noticeable.
// * Going to the same page with other filters, another tab or page number: the tables keep
//   their headings and their rows become a skeleton. Going to another page: the content is
//   replaced by a page skeleton. Both only after a short wait, and undone when the browser
//   brings the page back from its cache (Back button).
// * busy(button): a spinner in a button while its action runs.
//
// Downloads (CSV, PDF, print pages, links opening a new tab) are left alone, and any link or
// form inside [data-no-loading] too.
import { strings } from "./ui.js";

const SHOW_AFTER = 150; // ms: fast answers show nothing at all
const GIVE_UP_AFTER = 20000; // a navigation that never happens (a file download) stops showing

let bar = null;
let creep = null;
let navigating = false;
let pending = 0;
let pendingTimer = null;
let skeletonTimer = null;
let giveUpTimer = null;
let trigger = null; // the button just clicked, during that click
let leaving = false;
const running = new Map(); // request id → undo the spinner of the button that started it

function progressBar() {
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "loading-bar";
    bar.setAttribute("aria-hidden", "true");
    bar.append(document.createElement("span"));
    document.body.append(bar);
  }
  return bar;
}

function barStart() {
  const node = progressBar();
  const fill = node.firstElementChild;
  if (node.classList.contains("is-active")) return;
  clearInterval(creep);
  fill.style.transition = "none";
  fill.style.width = "0";
  void fill.offsetWidth; // restart from zero
  fill.style.transition = "";
  node.classList.add("is-active");
  let width = 12;
  fill.style.width = `${width}%`;
  creep = setInterval(() => {
    width += (90 - width) * 0.08; // slows down as it nears the end, never quite gets there
    fill.style.width = `${width}%`;
  }, 300);
}

function barDone() {
  if (!bar) return;
  clearInterval(creep);
  bar.firstElementChild.style.width = "100%";
  setTimeout(() => {
    bar.classList.remove("is-active");
  }, 250);
}

export function spinner(extra = "") {
  const node = document.createElement("span");
  node.className = `busy-spinner ${extra}`.trim();
  node.setAttribute("aria-hidden", "true");
  return node;
}

/** A button doing its work: a spinner in place of its icon, and disabled unless `disable` is
 * false (its own script already handles that). Returns the undo. */
export function busy(button, { disable = true } = {}) {
  if (!button || button.getAttribute("aria-busy") === "true") return () => {};
  const wasDisabled = button.disabled;
  const icon = button.querySelector(":scope > svg");
  const turning = spinner();
  button.setAttribute("aria-busy", "true");
  if (disable) button.disabled = true;
  if (icon) {
    icon.style.display = "none";
    icon.before(turning);
  } else {
    button.prepend(turning);
  }
  return () => {
    turning.remove();
    if (icon) icon.style.display = "";
    button.removeAttribute("aria-busy");
    if (disable) button.disabled = wasDisabled;
  };
}

/** Run `show` only if the work takes longer than a moment; returns what ends it (and undoes
 * what `show` returned). */
export function later(show, wait = SHOW_AFTER) {
  let undo = null;
  const timer = setTimeout(() => { undo = show(); }, wait);
  return () => {
    clearTimeout(timer);
    if (typeof undo === "function") undo();
  };
}

/** Figures being worked out again (a live quote): dimmed and marked busy until `done()`. */
export function refreshing(elements) {
  const nodes = [].concat(elements).filter(Boolean);
  nodes.forEach((node) => {
    node.classList.add("is-refreshing");
    node.setAttribute("aria-busy", "true");
  });
  return () => nodes.forEach((node) => {
    node.classList.remove("is-refreshing");
    node.removeAttribute("aria-busy");
  });
}

const WIDTHS = ["w-3/4", "w-2/3", "w-5/6", "w-1/2", "w-3/5", "w-4/5"];

function block(classes) {
  const node = document.createElement("span");
  node.className = `skeleton ${classes}`;
  return node;
}

/** Skeleton rows for a table, under its own headings (numbers stay at the end). */
export function skeletonRows(table, count = 6) {
  const headings = [...(table.tHead?.rows[0]?.cells || [])];
  const columns = Math.max(headings.length, 1);
  const body = document.createElement("tbody");
  body.dataset.skeleton = "";
  for (let row = 0; row < count; row += 1) {
    const tr = document.createElement("tr");
    for (let column = 0; column < columns; column += 1) {
      const td = document.createElement("td");
      const end = headings[column]?.classList.contains("text-end");
      const width = WIDTHS[(row + column) % WIDTHS.length];
      td.append(block(`h-3.5 ${end ? "ms-auto w-1/2" : width}`));
      tr.append(td);
    }
    body.append(tr);
  }
  return body;
}

function liveLabel() {
  const label = document.createElement("span");
  label.className = "sr-only";
  label.setAttribute("role", "status");
  label.textContent = strings().page_loading || "Loading…";
  return label;
}

function tablesToSkeleton(main) {
  main.querySelectorAll("table").forEach((table) => {
    if (table.closest("dialog, [data-no-loading]")) return;
    const shown = [...table.tBodies].filter((body) => !body.hidden && !("skeleton" in body.dataset));
    if (!shown.length) return;
    const rows = Math.min(Math.max(shown.reduce((n, body) => n + body.rows.length, 0), 3), 8);
    shown.forEach((body) => {
      body.hidden = true;
      body.dataset.hiddenForLoading = "";
    });
    table.append(skeletonRows(table, rows));
    table.setAttribute("aria-busy", "true");
  });
  main.querySelectorAll("[data-empty-state]").forEach((empty) => {
    if (empty.closest("dialog, [data-no-loading]")) return;
    const filler = document.createElement("div");
    filler.dataset.skeleton = "";
    filler.className = "space-y-3 p-5";
    WIDTHS.slice(0, 4).forEach((width) => filler.append(block(`h-4 ${width}`)));
    empty.hidden = true;
    empty.dataset.hiddenForLoading = "";
    empty.after(filler);
  });
  main.prepend(liveLabel());
}

function pageToSkeleton(main) {
  const content = main.firstElementChild;
  if (!content) return;
  const page = document.createElement("div");
  page.dataset.skeleton = "";
  page.className = "mx-auto max-w-7xl space-y-6";
  page.setAttribute("aria-busy", "true");
  page.append(liveLabel());
  const heading = document.createElement("div");
  heading.className = "space-y-3 pb-2";
  heading.append(block("h-3 w-24"), block("h-7 w-64 max-w-full"), block("h-4 w-96 max-w-full"));
  const card = document.createElement("div");
  card.className = "card overflow-hidden";
  const head = document.createElement("div");
  head.className = "flex items-center gap-4 border-b border-slate-100 px-5 py-4";
  head.append(block("h-4 w-40"), block("ms-auto h-8 w-28"));
  card.append(head);
  for (let row = 0; row < 7; row += 1) {
    const line = document.createElement("div");
    line.className = "flex items-center gap-6 border-t border-slate-100 px-5 py-4 first:border-t-0";
    line.append(block(`h-3.5 ${WIDTHS[row % WIDTHS.length]} max-w-56`), block("h-3.5 w-24"),
                block("ms-auto h-3.5 w-20"));
    card.append(line);
  }
  page.append(heading, card);
  content.hidden = true;
  content.dataset.hiddenForLoading = "";
  main.append(page);
}

function restore() {
  clearTimeout(skeletonTimer);
  clearTimeout(giveUpTimer);
  navigating = false;
  document.querySelectorAll("[data-skeleton]").forEach((node) => node.remove());
  document.querySelectorAll("[data-hidden-for-loading]").forEach((node) => {
    node.hidden = false;
    delete node.dataset.hiddenForLoading;
  });
  document.querySelectorAll("main [aria-busy='true']").forEach((node) => node.removeAttribute("aria-busy"));
  document.querySelectorAll("main span[role='status'].sr-only").forEach((node) => node.remove());
  if (!pending) barDone();
}

/** Show that another page is on its way (the skeleton and the bar). Pages that move on by
 * themselves (window.location.assign after saving) call it first. */
export function startNavigation(url, trigger = null) {
  navigate(new URL(url, window.location.href), trigger);
}

function navigate(url, trigger) {
  if (navigating) return;
  navigating = true;
  barStart();
  const main = document.querySelector("main");
  const samePage = url.pathname === window.location.pathname;
  skeletonTimer = setTimeout(() => {
    if (!main) return;
    if (samePage) tablesToSkeleton(main);
    else pageToSkeleton(main);
  }, SHOW_AFTER);
  if (trigger) busy(trigger);
  giveUpTimer = setTimeout(restore, GIVE_UP_AFTER);
}

function leavesThePage(link, event) {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return null;
  if (link.closest("[data-no-loading]")) return null;
  const href = link.getAttribute("href") || "";
  if (!href || href.startsWith("#") || /^(mailto|tel|javascript):/i.test(href)) return null;
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin) return null;
  if (url.pathname === window.location.pathname && url.search === window.location.search) return null;
  if (url.searchParams.has("format") || /\/(print|pdf)(\/|$)/.test(url.pathname)) return null;
  return url;
}

export function initLoading() {
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link) return;
    // Decide after every other handler had its say (one may take the click over).
    setTimeout(() => {
      const url = leavesThePage(link, event);
      if (url) navigate(url);
    }, 0);
  });
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form.matches("[data-api-form], [data-no-loading]") || (form.target && form.target !== "_self")) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    setTimeout(() => {
      if (event.defaultPrevented) return;
      const url = new URL(form.action || window.location.href, window.location.href);
      navigate(url, event.submitter || form.querySelector('[type="submit"]'));
    }, 0);
  });
  // Back/forward from the browser's cache: the page comes back as it was left.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      document.querySelectorAll("[aria-busy='true']").forEach((node) => {
        if (node.matches("button")) {
          node.removeAttribute("aria-busy");
          node.disabled = false;
          node.querySelector(".busy-spinner")?.remove();
          const icon = node.querySelector(":scope > svg");
          if (icon) icon.style.display = "";
        }
      });
      restore();
    }
  });
  // The button (or form) that was just used: a request it starts right away is its action.
  document.addEventListener("click", (event) => {
    trigger = event.target.closest?.("button");
    setTimeout(() => { trigger = null; }, 0);
  }, true);
  document.addEventListener("submit", (event) => {
    trigger = event.submitter || event.target.querySelector?.('[type="submit"]');
    setTimeout(() => { trigger = null; }, 0);
  }, true);
  window.addEventListener("beforeunload", () => { leaving = true; });
  // Requests made by the page itself (apps' api() calls).
  window.addEventListener("api:request", ({ detail }) => {
    if (detail.step > 0 && detail.writes && trigger) running.set(detail.id, busy(trigger, { disable: false }));
    if (detail.step < 0 && running.has(detail.id)) {
      const undo = running.get(detail.id);
      running.delete(detail.id);
      // Undone when the request ends, unless the page is moving on (a redirect after it).
      setTimeout(() => { if (!leaving) undo(); }, 60);
    }
    if (detail.quiet) return;
    pending = Math.max(0, pending + detail.step);
    clearTimeout(pendingTimer);
    if (pending > 0) pendingTimer = setTimeout(barStart, SHOW_AFTER * 2);
    else if (!navigating) barDone();
  });
}
