// Repairs: the take-in screen (pieces, customer or walk-in, optional deposit) and the forms
// on a repair (send, ready, deliver, deposit, cancel).
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { enhanceSelects } from "../../core/js/selects.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { initPaymentBlocks, payment } from "../../treasury/js/payment_block.js";

initPaymentBlocks();

registerShaper("repair-send", (values) => ({ workshop: values.workshop ? Number(values.workshop) : null }));
registerShaper("repair-ready", (values, form) => ({
  labour_amount: values.labour_amount || "0",
  lines: [...form.querySelectorAll("[data-ready-line]")].map((row) => ({
    line: Number(row.dataset.readyLine),
    weight_out_g: row.querySelector("[data-weight-out]").value.trim(),
    charge: row.querySelector("[data-charge]").value.trim() || null,
  })),
}));
registerShaper("repair-deliver", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const onAccount = Boolean(block.querySelector("[data-on-account]")?.checked);
  const paying = block.querySelector("[data-amount]") && !onAccount;
  return { payments: paying ? [payment(block)] : [], on_account: onAccount };
});
registerShaper("repair-deposit", (_values, form) => payment(form.querySelector("[data-payment]")));
registerShaper("repair-cancel", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const chosen = block.querySelector("[data-method]") ? payment(block) : { kind: "cash" };
  return { refund_method: chosen.kind, cash_box: chosen.cash_box ?? null,
           bank_account: chosen.bank_account ?? null,
           reason: block.querySelector("[data-reason]").value.trim() };
});

// --- take in -------------------------------------------------------------------------------------

const root = document.getElementById("repair-new");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const list = $("[data-lines]");
  const key = uuid();

  const addLine = () => {
    const row = document.getElementById("repair-line").content.firstElementChild.cloneNode(true);
    list.append(row);
    enhanceSelects(row);
    row.querySelector('[data-field="description"]').focus();
  };
  addLine();
  $("[data-add-line]").addEventListener("click", addLine);
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-line]");
    if (button && list.querySelectorAll("[data-line]").length > 1) button.closest("[data-line]").remove();
  });

  // A registered customer replaces the walk-in name; deposits need one.
  const customer = $("[data-customer]");
  const syncCustomer = () => {
    const chosen = Boolean(customer.value);
    $("[data-walk-in]").classList.toggle("opacity-40", chosen);
    $("[data-walk-in]").querySelectorAll("input").forEach((input) => (input.disabled = chosen));
    $("[data-deposit-hint]").classList.toggle("hidden", chosen);
    $("[data-amount]").disabled = !chosen;
  };
  customer.addEventListener("change", syncCustomer);
  syncCustomer();

  const showLineErrors = (fields) => {
    list.querySelectorAll("[data-line-error]").forEach((node) => (node.textContent = ""));
    const lines = fields?.lines;
    if (!lines || Array.isArray(lines)) return;
    const rows = list.querySelectorAll("[data-line]");
    for (const [index, errors] of Object.entries(lines)) {
      const node = rows[Number(index)]?.querySelector("[data-line-error]");
      if (node) node.textContent = Object.values(errors).flat().join(" ");
    }
  };

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const read = (row, field) => row.querySelector(`[data-field="${field}"]`).value.trim();
    const deposit = payment($("[data-payment]"));
    const body = {
      branch: Number($("select[data-branch]").value),
      kind: root.querySelector("[data-kind]:checked").value,
      customer: customer.value ? Number(customer.value) : null,
      customer_name: customer.value ? "" : $("[data-name]").value.trim(),
      customer_phone: customer.value ? "" : $("[data-phone]").value.trim(),
      promised_on: $("[data-promised]").value || null,
      note: $("[data-note]").value.trim(),
      deposit: customer.value && deposit.amount ? deposit : null,
      lines: [...list.querySelectorAll("[data-line]")].map((row) => ({
        description: read(row, "description"), karat: read(row, "karat") ? Number(read(row, "karat")) : null,
        weight_in_g: read(row, "weight_in_g") || "0", charge: read(row, "charge") || "0",
      })),
    };
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, { method: "POST", body, idempotencyKey: key });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      if (error instanceof ApiError) showLineErrors(error.fields);
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
    }
  });
}
