// Supplier invoice draft form: dynamic lines that switch between "weight per piece" and
// "bulk weight" by category, running totals (BigInt fixed-point, display only), request shaper.
import { registerShaper } from "../../core/js/forms.js";
import { enhanceSelects } from "../../core/js/selects.js";
import { initSeller, seller } from "./seller.js";

const form = document.querySelector('form[data-shape="supplier-invoice"]');
const list = form?.querySelector("[data-lines]");
const template = document.getElementById("line-template");
if (form) initSeller(form);
const SCALE = 3n;

function grams(text) {
  const match = /^\s*(\d+)(?:\.(\d*))?\s*$/.exec(text || "");
  if (!match) return null;
  return BigInt(match[1]) * 10n ** SCALE + BigInt((match[2] || "").padEnd(3, "0").slice(0, 3) || "0");
}

function formatGrams(value) {
  return `${(value / 10n ** SCALE).toLocaleString("en-US")}.${(value % 10n ** SCALE).toString().padStart(3, "0")}`;
}

function pieceWeights(row) {
  return row.querySelector('[data-field="piece_weights"]').value.split(/[\s,،;]+/).filter(Boolean);
}

function isSerialized(row) {
  return row.querySelector('[data-field="category"]').selectedOptions[0]?.dataset.tracking === "serialized";
}

function refreshRow(row) {
  const serialized = isSerialized(row);
  row.querySelector("[data-serialized]").classList.toggle("hidden", !serialized);
  row.querySelector("[data-bulk]").classList.toggle("hidden", serialized);
  const weights = pieceWeights(row).map(grams).filter((w) => w !== null);
  row.querySelector("[data-piece-count]").textContent = weights.length;
  row.querySelector("[data-piece-sum]").textContent = formatGrams(weights.reduce((a, b) => a + b, 0n));
}

function refreshTotals() {
  let pieces = 0;
  let weight = 0n;
  list.querySelectorAll("[data-line]").forEach((row) => {
    if (isSerialized(row)) {
      const weights = pieceWeights(row).map(grams).filter((w) => w !== null);
      pieces += weights.length;
      weight += weights.reduce((a, b) => a + b, 0n);
    } else {
      weight += grams(row.querySelector('[data-field="gross_weight_g"]').value) || 0n;
    }
  });
  form.querySelector('[data-total="pieces"]').textContent = pieces;
  form.querySelector('[data-total="weight"]').textContent = formatGrams(weight);
}

function applyCategoryDefaults(row) {
  const karat = row.querySelector('[data-field="category"]').selectedOptions[0]?.dataset.karat;
  const karatSelect = row.querySelector('[data-field="karat"]');
  if (!karat || karatSelect.value) return;
  if (karatSelect.tomselect) karatSelect.tomselect.setValue(karat);
  else karatSelect.value = karat;
}

function addLine(values = {}) {
  const row = template.content.firstElementChild.cloneNode(true);
  list.append(row);
  for (const [field, value] of Object.entries(values)) {
    const input = row.querySelector(`[data-field="${field}"]`);
    if (input && value !== null && value !== undefined) input.value = String(value);
  }
  enhanceSelects(row);
  if (!values.karat) applyCategoryDefaults(row);
  refreshRow(row);
  return row;
}

if (form) {
  const initial = JSON.parse(document.getElementById("initial-lines")?.textContent || "[]");
  if (initial.length) initial.forEach((line) => addLine(line));
  else addLine();
  refreshTotals();

  form.querySelector("[data-add-line]").addEventListener("click", () => addLine());
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-line]");
    if (button && list.querySelectorAll("[data-line]").length > 1) {
      button.closest("[data-line]").remove();
      refreshTotals();
    }
  });
  list.addEventListener("input", (event) => {
    const row = event.target.closest("[data-line]");
    if (row) refreshRow(row);
    refreshTotals();
  });
  list.addEventListener("change", (event) => {
    const row = event.target.closest("[data-line]");
    if (!row) return;
    if (event.target.matches('[data-field="category"]')) applyCategoryDefaults(row);
    refreshRow(row);
    refreshTotals();
  });
  // Show the rate field only for a foreign currency.
  const currency = form.querySelector("[data-currency]");
  currency.addEventListener("change", () => {
    form.querySelector("[data-fx]").classList.toggle("hidden", currency.value === form.dataset.homeCurrency);
  });
}

registerShaper("supplier-invoice", (values, formElement) => {
  const lines = [];
  formElement.querySelectorAll("[data-line]").forEach((row, index) => {
    row.querySelector("[data-line-error]").dataset.errorFor = `lines.${index}`;
    const read = (field) => row.querySelector(`[data-field="${field}"]`).value.trim();
    const line = {
      category: Number(read("category")),
      karat: read("karat") ? Number(read("karat")) : null,
      making_cost_rate: read("making_cost_rate") || "0",
      list_making_rate: read("list_making_rate") || "0",
    };
    if (isSerialized(row)) line.piece_weights = pieceWeights(row);
    else {
      line.gross_weight_g = read("gross_weight_g") || null;
      line.qty = Number(read("qty") || 0);
    }
    lines.push(line);
  });
  return {
    ...seller(formElement),
    branch: Number(values.branch),
    business_date: values.business_date,
    currency: values.currency,
    fx_rate: values.fx_rate ?? null,
    supplier_reference: values.supplier_reference ?? "",
    note: values.note ?? "",
    lines,
  };
});
