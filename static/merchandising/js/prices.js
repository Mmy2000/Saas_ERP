// Price changes: "Show the pieces" previews what would change (nothing saved), "Change the
// prices" applies it; on a change's page, "Undo this change".
import { api, ApiError, uuid } from "../../core/js/api.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function readFilters(root) {
  const values = (name) => {
    const el = root.querySelector(`[data-filter="${name}"]`);
    if (!el) return [];
    return [...el.selectedOptions].map((o) => Number(o.value));
  };
  const text = (name) => root.querySelector(`[data-filter="${name}"]`)?.value.trim() || "";
  return {
    branch_ids: values("branch_ids"), category_ids: values("category_ids"),
    karat_ids: values("karat_ids"), supplier_ids: values("supplier_ids"),
    min_weight: text("min_weight") || null, max_weight: text("max_weight") || null,
    barcodes: text("barcodes"),
  };
}

// The filters in words, for the history ("Rings · 21K · Main branch").
export function describe(root) {
  const parts = [];
  for (const name of ["branch_ids", "category_ids", "karat_ids", "supplier_ids"]) {
    const el = root.querySelector(`[data-filter="${name}"]`);
    const chosen = el ? [...el.selectedOptions].map((o) => o.textContent.trim()) : [];
    if (chosen.length) parts.push(chosen.join(", "));
  }
  const min = root.querySelector('[data-filter="min_weight"]')?.value.trim();
  const max = root.querySelector('[data-filter="max_weight"]')?.value.trim();
  if (min || max) parts.push(`${min || "0"}–${max || "∞"} g`);
  const codes = root.querySelector('[data-filter="barcodes"]')?.value.trim().split(/\s+/).filter(Boolean) || [];
  if (codes.length) parts.push(codes.length > 3 ? `${codes.slice(0, 3).join(", ")}… (${codes.length})` : codes.join(", "));
  return parts.join(" · ").slice(0, 300);
}

const root = document.getElementById("price-change");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  let key = uuid();
  let count = 0;
  const body = () => ({
    target: $('[data-field="target"]').value, method: $('[data-field="method"]').value,
    value: $('[data-field="value"]').value.trim(), filters: readFilters(root),
    summary: describe(root), note: $('[data-field="note"]').value.trim(),
  });
  const showError = (message) => {
    $("[data-error]").textContent = message || "";
    $("[data-error]").classList.toggle("hidden", !message);
  };
  root.addEventListener("change", () => { $("[data-apply]").disabled = true; key = uuid(); });
  root.addEventListener("input", () => { $("[data-apply]").disabled = true; });

  $("[data-preview]").addEventListener("click", async () => {
    showError("");
    root.querySelector('[data-error-for="value"]').textContent = "";
    try {
      const result = await api(root.dataset.previewUrl, { method: "POST", body: body(), idempotencyKey: uuid() });
      count = result.count;
      $("[data-count-text]").textContent = count ? root.dataset.count.replace("{n}", count) : root.dataset.none;
      $("[data-sample]").innerHTML = result.sample.map((row) => `<tr><td><span class="num">${esc(row.barcode)}</span></td><td>${esc(row.category)}</td><td>${esc(row.karat)}</td>
        <td class="text-end"><span class="num">${esc(row.weight)}</span></td><td class="text-end"><span class="num text-slate-500">${row.old == null || Number(row.old) === 0 ? "—" : esc(formatNumber(row.old))}</span></td>
        <td class="text-end"><span class="num font-semibold">${esc(formatNumber(row.new))}</span></td></tr>`).join("");
      $("[data-result]").classList.remove("hidden");
      $("[data-apply]").disabled = !count;
    } catch (error) {
      if (error instanceof ApiError && error.fields?.value) root.querySelector('[data-error-for="value"]').textContent = [].concat(error.fields.value).join(" ");
      else showError(error instanceof ApiError ? error.message : strings().network);
    }
  });

  $("[data-apply]").addEventListener("click", async (event) => {
    if (!window.confirm(root.dataset.confirm.replace("{n}", count))) return;
    event.currentTarget.disabled = true;
    try {
      const made = await api(root.dataset.applyUrl, { method: "POST", body: body(), idempotencyKey: key });
      flash(root.dataset.applied);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      showError(error instanceof ApiError ? error.message : strings().network);
      event.currentTarget.disabled = false;
    }
  });
}

const undo = document.querySelector("[data-undo]");
undo?.addEventListener("click", async () => {
  if (!window.confirm(undo.dataset.confirm)) return;
  undo.disabled = true;
  try {
    await api(undo.dataset.undo, { method: "POST", idempotencyKey: uuid() });
    flash(undo.dataset.done);
    window.location.reload();
  } catch (error) {
    toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
    undo.disabled = false;
  }
});
