// Employees and payroll: the commission fields on the employee form, the advance and
// bonus/deduction forms, removing an unpaid adjustment, and preparing the monthly payroll.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { initPaymentBlocks, payment } from "../../treasury/js/payment_block.js";

const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

initPaymentBlocks();

// Commission: the rate only matters (and is labelled) for the chosen rule.
document.querySelectorAll("[data-commission]").forEach((card) => {
  const basis = card.querySelector("[data-basis]");
  const sync = () => {
    card.querySelector("[data-rate]").classList.toggle("hidden", basis.value === "none");
    card.querySelectorAll("[data-rate-label]").forEach((label) => label.classList.toggle("hidden", label.dataset.rateLabel !== basis.value));
  };
  basis.addEventListener("change", sync);
  sync();
});

registerShaper("hr-advance", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const chosen = payment(block);
  return {
    amount: chosen.amount, method: chosen.kind, cash_box: chosen.cash_box, bank_account: chosen.bank_account,
    branch: Number(block.querySelector("[data-branch]").value),
    note: block.querySelector("[data-note]").value.trim(),
  };
});

registerShaper("hr-adjustment", (values) => ({
  kind: values.kind, amount: values.amount || "", period: values.period, note: values.note || "",
}));

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-adjustment]");
  if (!button) return;
  button.disabled = true;
  try {
    await api(button.dataset.deleteAdjustment, { method: "DELETE" });
    window.location.reload();
  } catch (error) {
    fail(error);
    button.disabled = false;
  }
});

// --- preparing the payroll -----------------------------------------------------------------------

const root = document.getElementById("payroll-new");

if (root) {
  const key = uuid();
  const block = root.querySelector("[data-payment]");
  let timer = null;

  const advances = () => Object.fromEntries([...root.querySelectorAll("[data-line]")]
    .filter((row) => row.querySelector("[data-advance]"))
    .map((row) => [row.dataset.line, row.querySelector("[data-advance]").value.trim()]));

  const show = (plan) => {
    for (const line of plan.lines) {
      const row = root.querySelector(`[data-line="${line.employee}"]`);
      if (!row) continue;
      row.querySelector('[data-f="net"]').textContent = formatNumber(line.net, 2);
    }
    root.querySelectorAll("[data-t]").forEach((node) => (node.textContent = formatNumber(plan.totals[node.dataset.t], 2)));
  };

  const preview = async () => {
    root.querySelector("[data-error]").textContent = "";
    try {
      show(await api(root.dataset.previewUrl, { method: "POST", body: { period: root.dataset.period, advances: advances() } }));
    } catch (error) {
      if (error instanceof ApiError) root.querySelector("[data-error]").textContent = error.message;
      else fail(error);
    }
  };

  root.addEventListener("input", (event) => {
    if (!event.target.matches("[data-advance]")) return;
    clearTimeout(timer);
    timer = setTimeout(preview, 350);
  });

  root.querySelector("[data-pay]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const chosen = payment(block);
    button.disabled = true;
    try {
      const run = await api(root.dataset.postUrl, {
        method: "POST", idempotencyKey: key,
        body: {
          period: root.dataset.period, branch: Number(block.querySelector("[data-branch]").value),
          method: chosen.kind, cash_box: chosen.cash_box, bank_account: chosen.bank_account,
          advances: advances(), note: block.querySelector("[data-note]").value.trim(),
        },
      });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", run.id));
    } catch (error) {
      if (error instanceof ApiError) root.querySelector("[data-error]").textContent = error.message;
      fail(error);
      button.disabled = false;
    }
  });
}
