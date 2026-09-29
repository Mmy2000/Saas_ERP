// Manual journal entry form: dynamic lines, live debit/credit totals, request shaper.
// Totals use BigInt fixed-point (6 dp), never floats; the server re-validates everything.
import { registerShaper } from "../../core/js/forms.js";
import { enhanceSelects } from "../../core/js/selects.js";

const SCALE = 6;
const form = document.querySelector('form[data-shape="journal"]');
const list = form?.querySelector("[data-lines]");
const template = document.getElementById("line-template");

function toFixed(text) {
  const match = /^\s*(\d+)(?:\.(\d*))?\s*$/.exec(text || "");
  if (!match) return 0n;
  const fraction = (match[2] || "").padEnd(SCALE, "0").slice(0, SCALE);
  return BigInt(match[1]) * 10n ** BigInt(SCALE) + BigInt(fraction || "0");
}

function format(value) {
  const whole = value / 10n ** BigInt(SCALE);
  const cents = (value % 10n ** BigInt(SCALE)) / 10n ** BigInt(SCALE - 2);
  return `${whole.toLocaleString("en-US")}.${cents.toString().padStart(2, "0")}`;
}

function functionalSelected(row) {
  const option = row.querySelector('[data-field="commodity"]').selectedOptions[0];
  return option?.dataset.functional === "1";
}

function refreshRow(row) {
  // The value field only matters for foreign currency and metal lines.
  const needsValue = !functionalSelected(row);
  const value = row.querySelector('[data-field="functional_amount"]');
  value.disabled = !needsValue;
  if (!needsValue) value.value = "";
}

function refreshTotals() {
  let debit = 0n;
  let credit = 0n;
  list.querySelectorAll("[data-line]").forEach((row) => {
    const own = functionalSelected(row);
    const valueOf = (field) => {
      const amount = toFixed(row.querySelector(`[data-field="${field}"]`).value);
      if (!amount) return 0n;
      return own ? amount : toFixed(row.querySelector('[data-field="functional_amount"]').value);
    };
    debit += valueOf("debit");
    credit += valueOf("credit");
  });
  form.querySelector('[data-total="debit"]').textContent = format(debit);
  form.querySelector('[data-total="credit"]').textContent = format(credit);
  form.querySelector("[data-unbalanced]").classList.toggle("hidden", debit === credit);
}

function addLine() {
  const row = template.content.firstElementChild.cloneNode(true);
  list.append(row);
  enhanceSelects(row);
  refreshRow(row);
  return row;
}

if (form) {
  addLine();
  addLine();
  form.querySelector("[data-add-line]").addEventListener("click", () => addLine());
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-line]");
    if (button && list.querySelectorAll("[data-line]").length > 2) {
      button.closest("[data-line]").remove();
      refreshTotals();
    }
  });
  list.addEventListener("input", refreshTotals);
  list.addEventListener("change", (event) => {
    const row = event.target.closest("[data-line]");
    if (row) refreshRow(row);
    refreshTotals();
  });
}

registerShaper("journal", (values, formElement) => {
  const lines = [];
  formElement.querySelectorAll("[data-line]").forEach((row, index) => {
    row.querySelector("[data-line-error]").dataset.errorFor = `lines.${index}`;
    const read = (field) => row.querySelector(`[data-field="${field}"]`).value.trim();
    const line = { account: Number(read("account")), commodity: Number(read("commodity")), memo: "" };
    if (read("debit")) line.debit = read("debit");
    if (read("credit")) line.credit = read("credit");
    if (read("functional_amount")) line.functional_amount = read("functional_amount");
    if (read("party")) line.party = Number(read("party"));
    lines.push(line);
  });
  return { branch: Number(values.branch), business_date: values.business_date, memo: values.memo ?? "", lines };
});
