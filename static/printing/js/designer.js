// Document designer: blocks dragged onto a page, styled in a properties panel, previewed live.
// The page is rendered by the server from the blocks (apps/printing/builder.py) so what you see
// is what prints; this file only edits the blocks (JSON) and shows the preview.
import { enhanceSelects } from "../../core/js/selects.js";
import { toast } from "../../core/js/ui.js";

const data = JSON.parse(document.getElementById("designer-data").textContent);
const { schema, urls, strings: S, presets, icons } = data;
const root = document.querySelector("[data-designer]");
const $ = (selector) => root.querySelector(selector);

const clone = (value) => JSON.parse(JSON.stringify(value));
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const svg = (name, cls = "size-4") => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" class="${cls} shrink-0" aria-hidden="true">${icons[name] || ""}</svg>`;
const newId = () => "b" + Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, "0")).join("");
const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]*)/) || [])[1] || "";

let state = clone(data.state);
let selected = null;
let dirty = false;
const past = [];
const future = [];
let lastCoalesce = { key: null, at: 0 };

// ---- model -----------------------------------------------------------------------------------

function defaultsOf(specs) {
  return Object.fromEntries(Object.entries(specs).map(([key, spec]) => [key, spec.default]));
}

function makeBlock(type) {
  const block = { id: newId(), type, props: defaultsOf(schema.blocks[type].props), style: defaultsOf(schema.style) };
  if (type === "columns") block.children = Array.from({ length: block.props.count }, () => []);
  return block;
}

function withNewIds(block) {
  const copy = clone(block);
  copy.id = newId();
  if (copy.children) copy.children = copy.children.map((column) => column.map(withNewIds));
  return copy;
}

// Where a block lives: its list, index, and (inside columns) the parent block.
function locate(id, list = state.blocks, parent = null) {
  for (let index = 0; index < list.length; index++) {
    const block = list[index];
    if (block.id === id) return { block, list, index, parent };
    if (block.children) {
      for (const column of block.children) {
        const found = locate(id, column, block);
        if (found) return found;
      }
    }
  }
  return null;
}

// `inspector: false` while typing in the properties panel: redrawing it would steal the focus.
function commit(change, coalesceKey = null, { inspector = true } = {}) {
  const now = Date.now();
  if (!(coalesceKey && lastCoalesce.key === coalesceKey && now - lastCoalesce.at < 900)) {
    past.push(clone(state));
    if (past.length > 80) past.shift();
    future.length = 0;
  }
  lastCoalesce = { key: coalesceKey, at: now };
  change();
  dirty = true;
  render({ inspector });
  schedulePreview();
}

function insert(block, target) {
  // target: {list, index} or null (after the selection, else at the end)
  if (!target) {
    const at = selected && locate(selected);
    target = at ? { list: at.list, index: at.index + 1 } : { list: state.blocks, index: state.blocks.length };
  }
  if (block.type === "columns" && target.list !== state.blocks && isInsideColumns(target.list)) return false;
  target.list.splice(target.index, 0, block);
  selected = block.id;
  return true;
}

function isInsideColumns(list) {
  return list !== state.blocks;
}

function removeBlock(id) {
  const at = locate(id);
  if (!at) return null;
  at.list.splice(at.index, 1);
  if (selected === id) selected = null;
  return at.block;
}

function resizeColumns(block) {
  const count = Number(block.props.count);
  while (block.children.length < count) block.children.push([]);
  if (block.children.length > count) {
    const extra = block.children.splice(count).flat();
    block.children[count - 1].push(...extra); // keep the blocks of removed columns
  }
}

// ---- rendering: palette, layers, inspector --------------------------------------------------

function renderPalette() {
  $("[data-palette]").innerHTML = Object.entries(schema.blocks).map(([type, spec]) =>
    `<button type="button" draggable="true" data-new="${type}" class="flex cursor-grab flex-col items-center gap-1 rounded-lg border border-slate-200 bg-surface px-1 py-2 text-[11px] font-medium text-slate-700 shadow-xs transition hover:border-brand-300 hover:text-brand-700 active:cursor-grabbing">
      ${svg(spec.icon, "size-4 text-slate-400")}<span class="text-center leading-tight">${esc(spec.label)}</span></button>`).join("");
}

function layerRows(list, depth = 0) {
  return list.map((block) => {
    const spec = schema.blocks[block.type];
    const active = block.id === selected;
    let html = `<li data-id="${block.id}" draggable="true" class="group flex items-center gap-1.5 rounded-md border px-1.5 py-1 text-xs transition ${active ? "border-brand-300 bg-brand-50 text-brand-800" : "border-transparent text-slate-700 hover:bg-slate-100"}" style="margin-inline-start:${depth * 14}px">
      <span class="cursor-grab text-slate-400">${svg("grip-vertical", "size-3.5")}</span>
      ${svg(spec.icon, "size-3.5 text-slate-400")}
      <span class="min-w-0 flex-1 truncate">${esc(spec.label)}</span>
      <span class="hidden gap-0.5 group-hover:flex">
        <button type="button" data-act="up" title="${esc(S.up)}" class="rounded p-0.5 hover:bg-slate-200">${svg("arrow-up", "size-3")}</button>
        <button type="button" data-act="down" title="${esc(S.down)}" class="rounded p-0.5 hover:bg-slate-200">${svg("arrow-down", "size-3")}</button>
        <button type="button" data-act="dup" title="${esc(S.duplicate)}" class="rounded p-0.5 hover:bg-slate-200">${svg("copy", "size-3")}</button>
        <button type="button" data-act="del" title="${esc(S.delete)}" class="rounded p-0.5 hover:bg-red-100 hover:text-red-700">${svg("trash-2", "size-3")}</button>
      </span></li>`;
    if (block.children) {
      block.children.forEach((column, i) => {
        html += `<li class="pt-1 text-[11px] font-medium text-slate-400" style="margin-inline-start:${(depth + 1) * 14}px">${esc(S.column.replace("%(n)s", i + 1))}</li>`;
        html += layerRows(column, depth + 1);
        html += `<li data-drop-col="${block.id}:${i}" class="rounded-md border border-dashed border-slate-300 px-2 py-1.5 text-center text-[11px] text-slate-400" style="margin-inline-start:${(depth + 1) * 14}px">${esc(S.empty)}</li>`;
      });
    }
    return html;
  }).join("");
}

function renderLayers() {
  $("[data-layers]").innerHTML = layerRows(state.blocks) +
    `<li data-drop-end class="mt-2 rounded-md border border-dashed border-slate-300 px-2 py-2 text-center text-[11px] text-slate-400">${esc(S.empty)}</li>`;
}

function field(path, spec, value) {
  const id = "f-" + path.replace(/\./g, "-");
  const label = `<label for="${id}" class="mb-1 block text-[11px] font-medium text-slate-500">${esc(spec.label)}</label>`;
  if (spec.kind === "bool") {
    return `<label class="flex items-center gap-2 py-1 text-xs text-slate-700"><input type="checkbox" id="${id}" data-field="${path}" class="size-4 rounded border-slate-300 accent-brand-600" ${value ? "checked" : ""}>${esc(spec.label)}</label>`;
  }
  if (spec.kind === "choice") {
    return `<div>${label}<select id="${id}" data-field="${path}" class="form-input text-xs">${spec.choices.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`;
  }
  if (spec.kind === "color") {
    return `<div>${label}<div class="flex items-center gap-1.5">
      <input type="color" id="${id}" data-field="${path}" value="${value || "#000000"}" class="h-8 w-10 cursor-pointer rounded-md border border-slate-200 bg-surface p-0.5 ${value ? "" : "opacity-40"}">
      <span class="num flex-1 text-xs ${value ? "text-slate-700" : "text-slate-400"}">${value || esc(S.inherit)}</span>
      ${value ? `<button type="button" data-clear="${path}" class="rounded px-1.5 py-0.5 text-[11px] text-slate-500 hover:bg-slate-100">${esc(S.clear)}</button>` : ""}</div></div>`;
  }
  if (spec.kind === "num") {
    return `<div>${label}<div class="relative" dir="ltr"><input type="number" id="${id}" data-field="${path}" value="${value}" min="${spec.min}" max="${spec.max}" step="${spec.max > 20 ? 1 : 0.5}" class="form-input num h-8 w-full py-0 pe-9 text-start text-xs">
      ${spec.unit ? `<span class="pointer-events-none absolute inset-y-0 end-2 flex items-center text-[10px] text-slate-400">${spec.unit}</span>` : ""}</div></div>`;
  }
  const input = spec.multiline
    ? `<textarea id="${id}" data-field="${path}" rows="3" class="form-input text-xs">${esc(value)}</textarea>`
    : `<input id="${id}" data-field="${path}" value="${esc(value)}" class="form-input h-8 py-0 text-xs">`;
  return `<div>${label}${input}</div>`;
}

// Four sides around a box, like a browser's box-model view.
function sides(prefix, keys, values, title) {
  const [top, bottom, left, right] = keys;
  const box = (key) => `<input type="number" data-field="${prefix}.${key}" value="${values[key]}" min="${schema.style[key].min}" max="${schema.style[key].max}" step="0.5" title="${esc(schema.style[key].label)}" aria-label="${esc(schema.style[key].label)}" class="num h-7 w-14 rounded-md border border-slate-200 bg-surface text-center text-xs text-slate-800 focus:border-brand-400 focus:outline-none">`;
  return `<div><p class="mb-1.5 text-[11px] font-medium text-slate-500">${esc(title)} <span class="text-slate-400">(mm)</span></p>
    <div class="grid grid-cols-3 items-center justify-items-center gap-1 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-2" dir="ltr">
      <span></span>${box(top)}<span></span>
      ${box(left)}<span class="h-7 w-full rounded border border-slate-200 bg-surface"></span>${box(right)}
      <span></span>${box(bottom)}<span></span></div></div>`;
}

function section(title, body, open = true) {
  return `<details class="border-b border-slate-100" ${open ? "open" : ""}><summary class="flex cursor-pointer items-center justify-between px-4 py-2.5 text-xs font-semibold text-slate-800">${esc(title)}${svg("chevron-down", "size-3.5 text-slate-400")}</summary><div class="space-y-3 px-4 pb-4">${body}</div></details>`;
}

// The panel is redrawn often: drop the old searchable selects first (their dropdowns live in
// <body>), then make the new ones searchable like everywhere else in the system.
function dropSelects(panel) {
  panel.querySelectorAll("select.tomselected").forEach((select) => select.tomselect?.destroy());
}

function renderInspector() {
  const panel = $("[data-inspector]");
  dropSelects(panel);
  paintInspector(panel);
  enhanceSelects(panel);
}

function paintInspector(panel) {
  const at = selected && locate(selected);
  if (!at) {
    panel.innerHTML = `<div class="flex items-center gap-2 border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-900">${esc(S.page)}</div>
      <p class="flex items-start gap-2 px-4 py-3 text-xs text-slate-500">${svg("mouse-pointer-click", "size-4 mt-0.5")}${esc(S.nothing)}</p>
      ${section(S.page, `<div class="grid grid-cols-2 gap-3">${Object.entries(schema.page).map(([k, spec]) => field(`page.${k}`, spec, state.page[k])).join("")}</div>`)}`;
    return;
  }
  const { block } = at;
  const spec = schema.blocks[block.type];
  const props = Object.entries(spec.props).map(([k, s]) => field(`props.${k}`, s, block.props[k])).join("");
  const groups = schema.style_groups.map(([title, keys], i) => {
    if (i === 0) return section(title, sides("style", ["mt", "mb", "ml", "mr"], block.style, title));
    if (i === 1) return section(title, sides("style", ["pt", "pb", "pl", "pr"], block.style, title), false);
    return section(title, `<div class="grid grid-cols-2 gap-3">${keys.map((k) => field(`style.${k}`, schema.style[k], block.style[k])).join("")}</div>`, i === 2);
  }).join("");
  panel.innerHTML = `<div class="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
      <span class="grid size-7 place-items-center rounded-md bg-brand-50 text-brand-600">${svg(spec.icon, "size-4")}</span>
      <p class="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900">${esc(spec.label)}</p>
      <button type="button" data-act="dup" data-id="${block.id}" title="${esc(S.duplicate)}" class="btn btn-ghost h-7 px-1.5">${svg("copy", "size-3.5")}</button>
      <button type="button" data-act="del" data-id="${block.id}" title="${esc(S.delete)}" class="btn btn-ghost h-7 px-1.5 hover:text-red-700">${svg("trash-2", "size-3.5")}</button></div>
    ${props ? section(S.properties, `<div class="grid grid-cols-2 gap-3 [&>div:has(textarea)]:col-span-2">${props}</div>`) : ""}
    ${groups}`;
}

function renderSettings() {
  const fill = (select, options, value) => {
    // The page may enhance the select before its options arrive; load them into Tom Select too.
    const control = select.tomselect;
    if (!control || Object.keys(control.options).length !== options.length) {
      select.innerHTML = options.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`).join("");
      if (control) { control.clearOptions(); control.sync(); }
    }
    select.tomselect?.setValue(value ?? "", true);
  };
  fill($("[data-setting=paper]"), data.papers, state.paper);
  fill($("[data-setting=lang]"), data.languages, state.lang);
  $("[data-undo]").disabled = !past.length;
  $("[data-redo]").disabled = !future.length;
  $("[data-status]").textContent = dirty ? S.unsaved : $("[data-status]").dataset.title;
}

function render({ inspector = true } = {}) {
  renderLayers();
  if (inspector) renderInspector();
  renderSettings();
  highlight();
}

// ---- preview ---------------------------------------------------------------------------------

const frame = $("[data-frame]");
let previewTimer = null;
let previewController = null;

function paperWidth() {
  return state.paper === "roll80" ? 340 : state.paper === "a5" ? 620 : 840;
}

function fit() {
  const width = paperWidth();
  frame.style.width = `${width}px`;
  const doc = frame.contentDocument;
  const height = Math.max(400, doc?.documentElement?.scrollHeight || 1100);
  frame.style.height = `${height}px`;
  const scale = Math.min(1, ($("[data-canvas]").clientWidth - 48) / width);
  frame.style.transform = `scale(${scale})`;
  const clip = $("[data-frame-clip]");
  clip.style.width = `${Math.round(width * scale)}px`;
  clip.style.height = `${Math.round(height * scale)}px`;
}

function highlight() {
  const at = selected && locate(selected);
  frame.contentWindow?.postMessage({ designer: "highlight", id: selected, label: at ? schema.blocks[at.block.type].label : "" }, "*");
}

async function preview() {
  previewController?.abort();
  previewController = new AbortController();
  try {
    const response = await fetch(urls.preview, {
      method: "POST", credentials: "same-origin", signal: previewController.signal,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(state),
    });
    if (!response.ok) throw new Error(String(response.status));
    const html = await response.text();
    const keep = $("[data-canvas]").scrollTop;
    frame.srcdoc = html;
    frame.onload = () => { fit(); highlight(); $("[data-canvas]").scrollTop = keep; };
  } catch (error) {
    if (error.name !== "AbortError") toast(S.failed, { kind: "error" });
  }
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(preview, 220);
}

// ---- events ----------------------------------------------------------------------------------

function select(id) {
  selected = id;
  renderLayers();
  renderInspector();
  highlight();
}

function act(action, id) {
  const at = locate(id);
  if (!at) return;
  commit(() => {
    if (action === "del") removeBlock(id);
    if (action === "dup") { const copy = withNewIds(at.block); at.list.splice(at.index + 1, 0, copy); selected = copy.id; }
    if (action === "up" && at.index > 0) at.list.splice(at.index - 1, 0, ...at.list.splice(at.index, 1));
    if (action === "down" && at.index < at.list.length - 1) at.list.splice(at.index + 1, 0, ...at.list.splice(at.index, 1));
  });
}

function setField(path, raw, coalesce) {
  const [scope, key] = path.split(".");
  let target;
  let spec;
  if (scope === "page") { target = state.page; spec = schema.page[key]; } else {
    const at = locate(selected);
    if (!at) return;
    target = at.block[scope];
    spec = scope === "props" ? schema.blocks[at.block.type].props[key] : schema.style[key];
  }
  let value = raw;
  if (spec.kind === "num") value = Math.max(spec.min, Math.min(spec.max, Number(raw) || 0));
  commit(() => {
    target[key] = value;
    if (scope === "props" && key === "count") resizeColumns(locate(selected).block);
  }, coalesce ? path + selected : null, { inspector: spec.kind === "color" && !coalesce });
}

root.addEventListener("input", (event) => {
  const el = event.target.closest("[data-field]");
  if (!el || el.type === "checkbox" || el.tagName === "SELECT") return;
  setField(el.dataset.field, el.value, true);
});
root.addEventListener("change", (event) => {
  const el = event.target;
  if (el.matches("[data-field]") && el.type === "color") {
    renderInspector(); // show the new hex and the "clear" button
  } else if (el.matches("[data-field]") && (el.type === "checkbox" || el.tagName === "SELECT")) {
    setField(el.dataset.field, el.type === "checkbox" ? el.checked : el.value, false);
  } else if (el.matches("[data-setting]")) {
    commit(() => { state[el.dataset.setting] = el.value; });
  } else if (el.matches("[data-preset]") && el.value) {
    const name = el.value;
    if (el.tomselect) el.tomselect.clear(true); else el.value = "";
    if (!window.confirm(S.confirm_preset)) return;
    commit(() => {
      state.blocks = presets[name].map(withNewIds);
      if (name === "thermal") state.paper = "roll80";
      else if (state.paper === "roll80") state.paper = "a4";
      selected = null;
    });
  }
});

root.addEventListener("click", (event) => {
  const button = event.target.closest("button, [data-id]");
  if (!button) return;
  if (button.matches("[data-new]")) { commit(() => insert(makeBlock(button.dataset.new), null)); return; }
  if (button.matches("[data-clear]")) { setField(button.dataset.clear, "", false); renderInspector(); return; }
  if (button.matches("[data-act]")) { act(button.dataset.act, button.dataset.id || button.closest("[data-id]").dataset.id); return; }
  if (button.matches("[data-undo]")) { undo(); return; }
  if (button.matches("[data-redo]")) { redo(); return; }
  if (button.matches("[data-save]")) { save(); return; }
  if (button.matches("li[data-id]")) select(button.dataset.id);
});

function undo() {
  if (!past.length) return;
  future.push(clone(state));
  state = past.pop();
  dirty = true;
  if (selected && !locate(selected)) selected = null;
  render();
  schedulePreview();
}

function redo() {
  if (!future.length) return;
  past.push(clone(state));
  state = future.pop();
  dirty = true;
  render();
  schedulePreview();
}

async function save() {
  const button = $("[data-save]");
  button.disabled = true;
  try {
    const response = await fetch(urls.save, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(state),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result?.ok) throw new Error();
    state.blocks = result.blocks;
    state.page = result.page;
    dirty = false;
    render();
    toast(result.message || S.saved);
  } catch {
    toast(S.failed, { kind: "error" });
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("keydown", (event) => {
  const typing = event.target.closest("input, textarea, select");
  const mod = event.ctrlKey || event.metaKey;
  if (mod && event.key.toLowerCase() === "s") { event.preventDefault(); save(); return; }
  if (typing) return;
  if (mod && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redo() : undo(); }
  else if (mod && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); }
  else if (mod && event.key.toLowerCase() === "d" && selected) { event.preventDefault(); act("dup", selected); }
  else if ((event.key === "Delete" || event.key === "Backspace") && selected) { event.preventDefault(); act("del", selected); }
  else if (event.key === "Escape") select(null);
});

window.addEventListener("message", (event) => {
  if (event.source !== frame.contentWindow || !event.data) return;
  if (event.data.designer === "select") select(event.data.id);
  if (event.data.designer === "ready") { fit(); highlight(); }
});
window.addEventListener("resize", fit);
window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

// ---- drag and drop ---------------------------------------------------------------------------

let dragging = null; // {kind: "new"|"move", value}

root.addEventListener("dragstart", (event) => {
  const item = event.target.closest("[data-new], li[data-id]");
  if (!item) return;
  dragging = item.dataset.new ? { kind: "new", value: item.dataset.new } : { kind: "move", value: item.dataset.id };
  event.dataTransfer.effectAllowed = "copyMove";
  event.dataTransfer.setData("text/plain", dragging.value);
  $("[data-drop-hint]").classList.remove("hidden");
});
root.addEventListener("dragend", () => {
  dragging = null;
  $("[data-drop-hint]").classList.add("hidden");
  clearMarks();
});

function clearMarks() {
  root.querySelectorAll(".drop-before, .drop-after, .drop-into").forEach((el) => el.classList.remove("drop-before", "drop-after", "drop-into", "border-t-brand-500", "border-b-brand-500", "!border-brand-500"));
  frame.contentWindow?.postMessage({ designer: "dropmark", id: null }, "*");
}

// Where a drop over the page lands: the block under the pointer (before or after it, by
// which half), an empty column, or the end of the page.
function pageTarget(event) {
  const doc = frame.contentDocument;
  if (!doc) return null;
  const box = frame.getBoundingClientRect();
  const scale = box.width / frame.offsetWidth || 1;
  const x = (event.clientX - box.left) / scale;
  const y = (event.clientY - box.top) / scale;
  const hit = doc.elementFromPoint(x, y);
  const column = hit?.closest(".bk-col");
  const block = hit?.closest("[data-block]");
  if (column && (!block || !column.contains(block))) {
    const parent = locate(column.closest("[data-block]")?.dataset.block)?.block;
    const list = parent?.children?.[Number(column.dataset.column)];
    if (list) return { list, index: list.length, mark: { id: parent.id, column: column.dataset.column } };
  }
  if (block) {
    const at = locate(block.dataset.block);
    if (at) {
      const rect = block.getBoundingClientRect();
      const after = y > rect.top + rect.height / 2;
      return { list: at.list, index: at.index + (after ? 1 : 0), mark: { id: at.block.id, after } };
    }
  }
  return { list: state.blocks, index: state.blocks.length, mark: null };
}

function dropTarget(event) {
  const row = event.target.closest("li[data-id], [data-drop-col], [data-drop-end]");
  if (row?.dataset.dropCol) {
    const [id, i] = row.dataset.dropCol.split(":");
    const columns = locate(id)?.block;
    return columns ? { el: row, mark: "drop-into", list: columns.children[Number(i)], index: columns.children[Number(i)].length } : null;
  }
  if (row?.hasAttribute("data-drop-end")) return { el: row, mark: "drop-into", list: state.blocks, index: state.blocks.length };
  if (row?.dataset.id) {
    const at = locate(row.dataset.id);
    const box = row.getBoundingClientRect();
    const after = event.clientY > box.top + box.height / 2;
    return { el: row, mark: after ? "drop-after" : "drop-before", list: at.list, index: at.index + (after ? 1 : 0) };
  }
  if (event.target.closest("[data-drop-hint]")) {
    const target = pageTarget(event);
    return target ? { el: null, ...target } : null;
  }
  return null;
}

root.addEventListener("dragover", (event) => {
  if (!dragging) return;
  // Near the top or bottom of the page area: scroll, so long documents can be reached.
  const canvas = $("[data-canvas]");
  const area = canvas.getBoundingClientRect();
  if (event.target.closest("[data-drop-hint]")) {
    if (event.clientY < area.top + 48) canvas.scrollTop -= 16;
    else if (event.clientY > area.bottom - 48) canvas.scrollTop += 16;
  }
  const target = dropTarget(event);
  if (!target) return;
  event.preventDefault();
  clearMarks();
  if (!target.el) {
    frame.contentWindow?.postMessage({ designer: "dropmark", ...(target.mark || { id: null }) }, "*");
  } else {
    target.el.classList.add(target.mark);
    target.el.classList.add(target.mark === "drop-before" ? "border-t-brand-500" : target.mark === "drop-after" ? "border-b-brand-500" : "!border-brand-500");
  }
});

root.addEventListener("drop", (event) => {
  if (!dragging) return;
  const target = dropTarget(event);
  if (!target) return;
  event.preventDefault();
  const drag = dragging;
  commit(() => {
    let place = target.list ? { list: target.list, index: target.index } : null;
    if (drag.kind === "new") { insert(makeBlock(drag.value), place); return; }
    const at = locate(drag.value);
    if (!at) return;
    // A block cannot go inside itself, and columns cannot go inside columns.
    if (place && at.block.children?.some((column) => column === place.list)) return;
    if (place && at.block.type === "columns" && place.list !== state.blocks) return;
    const sameList = place && place.list === at.list;
    const [moved] = at.list.splice(at.index, 1);
    if (place && sameList && place.index > at.index) place.index -= 1;
    insert(moved, place);
  });
  dragging = null;
  $("[data-drop-hint]").classList.add("hidden");
  clearMarks();
});

// ---- start -----------------------------------------------------------------------------------

$("[data-status]").dataset.title = $("[data-status]").textContent;
renderPalette();
render();
enhanceSelects(root.querySelector("header"));
preview();
