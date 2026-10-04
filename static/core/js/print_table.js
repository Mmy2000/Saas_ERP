// "Print table": any page with <table data-report> gets a Print button in its header. It opens
// a dialog (templates/components/print_table.html) to pick the tables, the columns, this page or
// every page, and the paper; then prints a clean copy from a hidden frame, so "Save as PDF" in
// the print dialog gives a PDF.
//
//   <table class="table" data-report="Sales">            a report table, named by its group
//          data-pages="{{ page.paginator.num_pages }}"   paginated: "All pages" fetches ?page=2…
//          data-report-caption="JE-12 · 1/5/2026"        printed above it (journal entries)
//
// Tables of one group follow each other into one printed table unless they have captions.
// Columns are matched by their header text, so the statement's per-currency tables share them.

const MAX_PAGES = 100;
const SKIP = "svg, button, input, select, textarea, [data-no-print], .sr-only";

const esc = (text) => String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const textOf = (cell) => {
  const copy = cell.cloneNode(true);
  copy.querySelectorAll(SKIP).forEach((node) => node.remove());
  return copy.textContent.replace(/\s+/g, " ").trim();
};
const alignOf = (cell) => (cell.matches(".text-end, .text-right") ? "end" : cell.matches(".text-center") ? "center" : "");
const visible = (row) => !row.hidden && !row.classList.contains("hidden");

// One <tr> as cells that know which columns they cover.
function readRow(tr) {
  let at = 0;
  return [...tr.cells].map((cell) => {
    const span = cell.colSpan || 1;
    const item = { text: textOf(cell), align: alignOf(cell), start: at, span };
    at += span;
    return item;
  });
}

function readTable(table) {
  const group = table.dataset.report || document.querySelector("main h1")?.textContent.trim() || "";
  const head = [...(table.tHead?.rows || [])].map(readRow);
  const body = [...table.tBodies].flatMap((tbody) => [...tbody.rows]).filter(visible).map(readRow);
  const foot = [...(table.tFoot?.rows || [])].filter(visible).map(readRow);
  // Column labels: the lowest header cell over each column.
  const width = Math.max(0, ...[...head, ...body, ...foot].map((row) => row.reduce((n, c) => n + c.span, 0)));
  const labels = Array.from({ length: width }, () => "");
  head.forEach((row) => row.forEach((c) => { for (let i = c.start; i < c.start + c.span; i++) if (c.text) labels[i] = c.text; }));
  // A column with no header and nothing in it (buttons, row checkboxes) is not offered.
  const used = labels.map((label, i) => Boolean(label) || body.some((row) => row.some((c) => c.start === i && c.span === 1 && c.text)));
  const keys = labels.map((label, i) => (used[i] ? label || `#${i + 1}` : null));
  return { group, caption: table.dataset.reportCaption || "", paged: Number(table.dataset.pages || 1) > 1, head, body, foot, keys };
}

// Tables of the current page, with paginated ones replaced by every page's when asked.
async function collect(all, progress) {
  const live = [...document.querySelectorAll("table[data-report]")].map(readTable);
  const pages = Math.min(MAX_PAGES, Math.max(1, ...[...document.querySelectorAll("table[data-report][data-pages]")].map((t) => Number(t.dataset.pages) || 1)));
  if (!all || pages < 2) return live;
  const fetched = [];
  for (let n = 1; n <= pages; n++) {
    progress(n, pages);
    const url = new URL(window.location.href);
    url.searchParams.set("page", n);
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(response.statusText);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    fetched.push(...[...doc.querySelectorAll("table[data-report][data-pages]")].map(readTable));
  }
  const first = live.findIndex((t) => t.paged);
  const rest = live.filter((t) => !t.paged);
  rest.splice(first < 0 ? rest.length : first, 0, ...fetched); // where the paginated tables were
  return rest;
}

// Consecutive tables of a group without captions become one.
function merge(tables) {
  const out = [];
  tables.forEach((table) => {
    const last = out[out.length - 1];
    if (last && last.group === table.group && !last.caption && !table.caption && last.keys.join("\u0001") === table.keys.join("\u0001")) {
      last.body.push(...table.body);
      last.foot = table.foot;
    } else {
      out.push({ ...table, body: [...table.body] });
    }
  });
  return out;
}

function rowHtml(row, chosen, tag) {
  return `<tr>${row.map((c) => {
    const covered = chosen.filter((i) => i >= c.start && i < c.start + c.span).length;
    if (!covered) return "";
    return `<${tag}${covered > 1 ? ` colspan="${covered}"` : ""}${c.align ? ` class="${c.align}"` : ""}><bdi>${esc(c.text)}</bdi></${tag}>`;
  }).join("")}</tr>`;
}

function tableHtml(table, columns) {
  const chosen = table.keys.map((key, i) => (key && columns.has(key) ? i : -1)).filter((i) => i >= 0);
  if (!chosen.length) return "";
  const caption = table.caption ? `<p class="caption">${esc(table.caption)}</p>` : "";
  return `${caption}<table><thead>${table.head.map((r) => rowHtml(r, chosen, "th")).join("")}</thead>
    <tbody>${table.body.map((r) => rowHtml(r, chosen, "td")).join("")}</tbody>
    ${table.foot.length ? `<tfoot>${table.foot.map((r) => rowHtml(r, chosen, "td")).join("")}</tfoot>` : ""}</table>`;
}

// The filters of the page's GET form, as "Label: value".
function filtersText() {
  const form = document.querySelector("main form[method=get]");
  if (!form) return "";
  const parts = [];
  form.querySelectorAll("[name]").forEach((field) => {
    if (["page", "format"].includes(field.name) || field.type === "hidden" || field.type === "submit") return;
    if ((field.type === "checkbox" || field.type === "radio") && !field.checked) return;
    const value = field.tagName === "SELECT" ? (field.value ? field.selectedOptions[0]?.textContent.trim() : "") : field.value.trim();
    if (!value) return;
    const label = (field.labels?.[0] && textOf(field.labels[0])) || field.getAttribute("aria-label") || field.placeholder || "";
    parts.push(field.type === "checkbox" ? label : label ? `${label}: ${value}` : value);
  });
  return parts.join(" · ");
}

function documentHtml({ title, tables, columns, landscape, s }) {
  const root = document.documentElement;
  const accent = getComputedStyle(root).getPropertyValue("--accent-600").trim() || "#333";
  const css = document.querySelector('link[rel=stylesheet][href*="app.css"]')?.href || "";
  const filters = filtersText();
  // A group's name is printed above it when the page has several groups or it differs from the title.
  const named = new Set(tables.map((t) => t.group)).size > 1 || tables.some((t) => t.group !== title);
  let groups = "";
  let shown = null;
  tables.forEach((table) => {
    const html = tableHtml(table, columns);
    if (!html) return;
    groups += `${named && table.group !== shown ? `<h2>${esc(table.group)}</h2>` : ""}${html}`;
    shown = table.group;
  });
  const rows = tables.reduce((n, t) => n + t.body.length, 0);
  const logo = s.logo ? `<img src="${esc(s.logo)}" alt="">` : "";
  return `<!doctype html><html lang="${esc(root.lang)}" dir="${esc(root.dir)}"><head><meta charset="utf-8"><title>${esc(title)}</title>
  ${css ? `<link rel="stylesheet" href="${esc(css)}">` : ""}
  <style>
    @page { size: A4 ${landscape ? "landscape" : "portrait"}; margin: 12mm 10mm 14mm;
      @bottom-center { content: counter(page) " / " counter(pages); font-size: 8pt; color: #777; } }
    html, body { background: #fff !important; color: #111; }
    body { margin: 0; font-size: 9.5pt; line-height: 1.35; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .top { display: flex; align-items: center; gap: 10px; padding-bottom: 8px; margin-bottom: 10px; border-bottom: 2px solid ${accent}; }
    .top img { width: 38px; height: 38px; object-fit: contain; border-radius: 8px; }
    .company { font-size: 9pt; color: #555; } h1 { margin: 0; font-size: 15pt; font-weight: 700; }
    .meta { margin-inline-start: auto; text-align: end; font-size: 8pt; color: #666; }
    .filters { margin: 0 0 8px; font-size: 8.5pt; color: #444; }
    h2 { margin: 14px 0 6px; font-size: 11pt; font-weight: 600; color: ${accent}; }
    .caption { margin: 12px 0 4px; font-size: 9pt; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin-bottom: 6px; }
    thead { display: table-header-group; } tr { break-inside: avoid; }
    th { background: #f2f2f3; font-weight: 600; text-align: start; padding: 5px 6px; border-bottom: 1px solid #cfcfd4; }
    td { padding: 4px 6px; border-bottom: 1px solid #e6e6ea; vertical-align: top; }
    tbody tr:nth-child(even) td { background: #fafafa; }
    tfoot td { font-weight: 600; border-top: 1.5px solid #999; border-bottom: 0; background: #fff; }
    .end { text-align: end; } .center { text-align: center; }
    .bottom { margin-top: 8px; font-size: 8pt; color: #666; }
  </style></head><body>
  <div class="top">${logo}<div><p class="company">${esc(s.company)}</p><h1>${esc(title)}</h1></div>
    <div class="meta">${esc(s.printed)} ${esc(new Date().toLocaleString(root.lang))}${s.user ? `<br>${esc(s.user)}` : ""}</div></div>
  ${filters ? `<p class="filters">${esc(s.filters)}: ${esc(filters)}</p>` : ""}
  ${groups}
  <p class="bottom">${esc(s.rows)}: ${rows}</p>
  </body></html>`;
}

function printHtml(html) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;inset-inline-start:-10000px;top:0;width:1000px;height:800px;border:0;";
  frame.srcdoc = html;
  document.body.append(frame);
  frame.addEventListener("load", async () => {
    const win = frame.contentWindow;
    await win.document.fonts?.ready;
    await Promise.all([...win.document.images].map((img) => (img.complete ? null : new Promise((r) => { img.onload = img.onerror = r; }))));
    win.addEventListener("afterprint", () => setTimeout(() => frame.remove(), 500));
    win.focus();
    win.print();
  }, { once: true });
}

// ---- the dialog ----------------------------------------------------------------------------

function storageKey() { return `print-table:${window.location.pathname}`; }
function remembered() { try { return JSON.parse(localStorage.getItem(storageKey())) || {}; } catch { return {}; } }
function remember(value) { try { localStorage.setItem(storageKey(), JSON.stringify(value)); } catch { /* private mode */ } }

function toggle(name, value, label, checked) {
  return `<label class="flex min-w-0 items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
    <input type="checkbox" name="${name}" value="${esc(value)}" ${checked ? "checked" : ""}><span class="truncate">${esc(label)}</span></label>`;
}

function openDialog(dialog) {
  const s = dialog.dataset;
  const tables = [...document.querySelectorAll("table[data-report]")].map(readTable);
  const groups = [...new Set(tables.map((t) => t.group))];
  const saved = remembered();
  const hiddenGroups = new Set(saved.hiddenGroups || []);
  const hiddenColumns = new Set(saved.hiddenColumns || []);
  const pages = Math.max(1, ...[...document.querySelectorAll("table[data-report][data-pages]")].map((t) => Number(t.dataset.pages) || 1));

  const form = dialog.querySelector("form");
  form.elements.title.value = document.querySelector("main h1")?.textContent.replace(/\s+/g, " ").trim() || document.title;
  const groupBox = dialog.querySelector("[data-print-groups]");
  groupBox.hidden = groups.length < 2;
  groupBox.querySelector("[data-list]").innerHTML = groups.map((g) => toggle("group", g, g, !hiddenGroups.has(g))).join("");

  const columnList = dialog.querySelector("[data-print-columns]");
  const allColumns = dialog.querySelector("[data-print-all-columns]");
  const chosenGroupSet = () => new Set(groups.length < 2 ? groups : [...form.querySelectorAll("input[name=group]:checked")].map((i) => i.value));
  const drawColumns = () => {
    const chosenGroups = chosenGroupSet();
    const keys = [...new Set(tables.filter((t) => chosenGroups.has(t.group)).flatMap((t) => t.keys.filter(Boolean)))];
    const before = new Map([...columnList.querySelectorAll("input")].map((i) => [i.value, i.checked]));
    columnList.innerHTML = keys.map((k) => toggle("column", k, k, before.has(k) ? before.get(k) : !hiddenColumns.has(k))).join("")
      || `<p class="col-span-full px-2 py-3 text-sm text-slate-500">${esc(s.noColumns)}</p>`;
    syncAll();
  };
  const syncAll = () => {
    const boxes = [...columnList.querySelectorAll("input")];
    const on = boxes.filter((b) => b.checked).length;
    allColumns.checked = boxes.length > 0 && on === boxes.length;
    allColumns.indeterminate = on > 0 && on < boxes.length;
    dialog.querySelector("[data-print-go]").disabled = on === 0;
  };
  groupBox.onchange = drawColumns;
  columnList.onchange = syncAll;
  allColumns.onchange = () => { columnList.querySelectorAll("input").forEach((b) => { b.checked = allColumns.checked; }); syncAll(); };
  columnList.innerHTML = "";
  drawColumns();

  const rowsBox = dialog.querySelector("[data-print-rows]");
  rowsBox.hidden = pages < 2;
  dialog.querySelector("[data-print-pages]").textContent = String(pages);
  form.elements.rows.value = "page";
  const columnCount = tables[0]?.keys.filter(Boolean).length || 0;
  form.elements.paper.value = saved.paper || (columnCount > 7 ? "landscape" : "portrait");
  dialog.querySelector("[data-print-status]").textContent = "";
  dialog.showModal();

  form.onsubmit = async (event) => {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { dialog.close(); return; }
    const chosenGroups = chosenGroupSet();
    const columns = new Set([...columnList.querySelectorAll("input:checked")].map((i) => i.value));
    remember({
      hiddenGroups: groups.filter((g) => !chosenGroups.has(g)),
      hiddenColumns: [...columnList.querySelectorAll("input:not(:checked)")].map((i) => i.value),
      paper: form.elements.paper.value,
    });
    const status = dialog.querySelector("[data-print-status]");
    const go = dialog.querySelector("[data-print-go]");
    go.disabled = true;
    try {
      const all = form.elements.rows.value === "all";
      const collected = await collect(all, (n, total) => { status.textContent = `${s.loading} ${n} / ${total}`; });
      const chosen = merge(collected.filter((t) => chosenGroups.has(t.group)));
      printHtml(documentHtml({ title: form.elements.title.value.trim() || document.title, tables: chosen, columns,
                               landscape: form.elements.paper.value === "landscape", s }));
      dialog.close();
    } catch {
      status.textContent = s.failed;
    } finally {
      go.disabled = false;
    }
  };
}

function placeButton(button) {
  const h1 = document.querySelector("main h1");
  const header = h1?.closest("main > div > *");
  const firstTable = document.querySelector("table[data-report]");
  if (!header || header === h1) {
    const wrap = document.createElement("div");
    wrap.className = "mb-4 flex justify-end";
    wrap.append(button);
    (firstTable.closest(".card") || firstTable).before(wrap);
    return;
  }
  const actions = [...header.children].find((child) => !child.contains(h1) && child.querySelector(".btn"));
  if (actions) { actions.prepend(button); return; }
  const wrap = document.createElement("div");
  wrap.className = "flex shrink-0 items-center gap-2";
  wrap.append(button);
  header.append(wrap);
}

export function initPrintTables() {
  const dialog = document.querySelector("[data-print-dialog]");
  const template = document.querySelector("template[data-print-button]");
  if (!dialog || !template || !document.querySelector("table[data-report]")) return;
  const button = template.content.firstElementChild.cloneNode(true);
  button.addEventListener("click", () => openDialog(dialog));
  placeButton(button);
}
