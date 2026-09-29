// Reservations: the new-reservation screen (scan pieces, priced by the server's sales quote)
// and the payment forms on a reservation (deposit, completion, cancellation).
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { narrowHolders } from "../../treasury/js/holders.js";

const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

function branchOf(scope) {
  const select = document.querySelector("select[data-branch]");
  return select ? select.value : document.querySelector("[data-branch][data-value]")?.dataset.value;
}

// --- payment blocks: which box / bank account / terminal ---------------------------------------

function syncPayment(block) {
  const method = block.querySelector("[data-method]")?.value || "cash";
  block.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
  narrowHolders({ branch: branchOf(block), currency: block.querySelector("[data-currency]")?.value,
                  box: block.querySelector("[data-box]"), bank: block.querySelector("[data-bank]"),
                  terminal: block.querySelector("[data-terminal]") });
  const onAccount = block.querySelector("[data-on-account]");
  if (onAccount) block.querySelector("[data-pay-now]")?.classList.toggle("hidden", onAccount.checked);
}

function payment(block) {
  const method = block.querySelector("[data-method]").value;
  const pick = (selector) => {
    const value = block.querySelector(selector)?.value;
    return value ? Number(value) : null;
  };
  return {
    kind: method, currency: block.querySelector("[data-currency]").value,
    amount: block.querySelector("[data-amount]")?.value.trim() || "",
    cash_box: method === "cash" ? pick("[data-box]") : null,
    bank_account: method === "bank_transfer" ? pick("[data-bank]") : null,
    terminal: method === "card" ? pick("[data-terminal]") : null,
  };
}

document.querySelectorAll("[data-payment]").forEach((block) => {
  block.addEventListener("change", (event) => {
    if (event.target.matches("[data-method], [data-currency], [data-on-account]")) syncPayment(block);
  });
  syncPayment(block);
});
document.querySelector("select[data-branch]")?.addEventListener("change", () =>
  document.querySelectorAll("[data-payment]").forEach(syncPayment));

registerShaper("reservation-deposit", (_values, form) => payment(form.querySelector("[data-payment]")));
registerShaper("reservation-complete", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const onAccount = block.querySelector("[data-on-account]")?.checked;
  const paying = block.querySelector("[data-amount]") && !onAccount;
  return { payments: paying ? [payment(block)] : [], payment_terms: onAccount ? "credit" : "cash" };
});
registerShaper("reservation-cancel", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const chosen = block.querySelector("[data-method]") ? payment(block) : { kind: "customer_credit" };
  return { refund_method: chosen.kind, cash_box: chosen.cash_box ?? null,
           bank_account: chosen.bank_account ?? null,
           reason: block.querySelector("[data-reason]").value.trim() };
});

// --- new reservation ---------------------------------------------------------------------------

const root = document.getElementById("reservation-new");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const barcodes = [];
  const key = uuid();
  let quote = null;

  const body = () => ({
    branch: Number($("select[data-branch]").value),
    lines: barcodes.map((barcode) => ({ barcode, discount_rate: "0" })),
    customer: $("[data-customer]").value ? Number($("[data-customer]").value) : null,
    payment_terms: "credit", payments: [], trade_ins: [],
  });

  const render = () => {
    const tbody = $("[data-lines]");
    tbody.replaceChildren();
    (quote?.lines || []).forEach((line) => {
      const row = document.getElementById("reservation-line").content.firstElementChild.cloneNode(true);
      const set = (field, value) => (row.querySelector(`[data-f="${field}"]`).textContent = value);
      set("barcode", line.barcode);
      set("description", line.description);
      set("karat", line.karat);
      set("weight", formatNumber(line.gross_weight_g, 3));
      set("total", formatNumber(line.line_total));
      row.querySelector("[data-remove]").addEventListener("click", () => {
        barcodes.splice(barcodes.indexOf(line.barcode), 1);
        refresh();
      });
      tbody.append(row);
    });
    $("[data-lines-empty]").classList.toggle("hidden", barcodes.length > 0);
    $("[data-count]").textContent = barcodes.length;
    $("[data-total]").textContent = formatNumber(quote?.totals?.total || "0");
  };

  const refresh = async () => {
    if (!barcodes.length) {
      quote = null;
      return render();
    }
    try {
      quote = await api(root.dataset.quoteUrl, { method: "POST", body: body() });
    } catch (error) {
      fail(error);
      barcodes.pop(); // the last scan was refused
      if (barcodes.length) quote = await api(root.dataset.quoteUrl, { method: "POST", body: body() }).catch(() => null);
      else quote = null;
    }
    render();
  };

  $("[data-scan]").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = event.currentTarget.querySelector("input");
    const barcode = input.value.trim();
    input.value = "";
    input.focus();
    if (!barcode || barcodes.includes(barcode)) return;
    barcodes.push(barcode);
    refresh();
  });
  $("select[data-branch]").addEventListener("change", () => {
    barcodes.splice(0, barcodes.length);
    refresh();
  });

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const deposit = payment($("[data-payment]"));
    const data = { ...body(), price_locked: $("[data-locked]").checked, note: $("[data-note]").value.trim(),
                   expires_on: $("[data-expires]").value || null, deposit: deposit.amount ? deposit : null };
    delete data.payment_terms;
    delete data.payments;
    delete data.trade_ins;
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, { method: "POST", body: data, idempotencyKey: key });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      fail(error);
      button.disabled = false;
    }
  });

  render();
}
