// Reservations: the new-reservation screen (scan pieces, priced by the server's sales quote)
// and the payment forms on a reservation (deposit, completion, cancellation).
import { enhanceSelects } from "../../core/js/selects.js";
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { initPaymentBlocks, payment } from "../../treasury/js/payment_block.js";
import { askForCode } from "./code.js";
import { later, refreshing } from "../../core/js/loading.js";

const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

initPaymentBlocks();

registerShaper("reservation-deposit", (_values, form) => payment(form.querySelector("[data-payment]")));
registerShaper("reservation-complete", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const onAccount = block.querySelector("[data-on-account]")?.checked;
  const paying = block.querySelector("[data-amount]") && !onAccount;
  const body = { payments: paying ? [payment(block)] : [], payment_terms: onAccount ? "credit" : "cash" };
  if (form.dataset.codeId) body.code = Number(form.dataset.codeId);
  if (form.dataset.codeSkip) body.skip_code = true;
  return body;
});

// Completing may need the code sent to the customer's phone: ask for it, then submit again.
const completion = document.querySelector('form[data-shape="reservation-complete"][data-reservation]');
completion?.addEventListener("submit", async (event) => {
  const form = completion;
  const onAccount = form.querySelector("[data-on-account]")?.checked;
  const needed = onAccount ? form.dataset.codeOnAccount : form.dataset.codePaid;
  if (!needed || form.dataset.codeId || form.dataset.codeSkip) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const answer = await askForCode({
    branch: Number(form.dataset.branch), reservation: Number(form.dataset.reservation),
    reason: onAccount ? form.dataset.codeReasonOnAccount : form.dataset.codeReason,
    canSkip: Boolean(form.dataset.codeCanSkip),
  });
  if (!answer) return;
  if (answer.code) form.dataset.codeId = answer.code;
  if (answer.skip) form.dataset.codeSkip = "1";
  form.requestSubmit(); // the body is shaped right away: a failed sale asks for a new code
  delete form.dataset.codeId;
  delete form.dataset.codeSkip;
}, { capture: true });
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
  let ordersQuote = null;
  let ordersTimer;

  // Orders by weight (bullion, coins…): priced by the server as they are typed.
  const orderRows = () => [...root.querySelectorAll("[data-order]")];
  const orders = () => orderRows().flatMap((row) => {
    const weight = row.querySelector('[data-o="gross_weight_g"]').value.trim();
    if (!weight) return [];
    return [{ category: Number(row.querySelector('[data-o="category"]').value),
              karat: Number(row.querySelector('[data-o="karat"]').value), gross_weight_g: weight,
              qty: Number(row.querySelector('[data-o="qty"]').value) || 0, discount_rate: "0" }];
  });
  const priceOrders = async () => {
    const wanted = orders();
    const error = root.querySelector("[data-orders-error]");
    if (error) error.textContent = "";
    if (!wanted.length) {
      ordersQuote = null;
      return render();
    }
    const settled = later(() => refreshing([...root.querySelectorAll("[data-orders], [data-total]")]));
    try {
      ordersQuote = await api(root.dataset.ordersUrl, { method: "POST", quiet: true,
        body: { branch: Number($("select[data-branch]").value), orders: wanted } });
    } catch (failure) {
      ordersQuote = null;
      if (error) error.textContent = failure.message || "";
    } finally {
      settled();
    }
    render();
  };
  const scheduleOrders = () => {
    clearTimeout(ordersTimer);
    ordersTimer = setTimeout(priceOrders, 300);
  };
  root.querySelector("[data-add-order]")?.addEventListener("click", () => {
    const row = document.getElementById("order-line").content.firstElementChild.cloneNode(true);
    root.querySelector("[data-orders]").append(row);
    const category = row.querySelector('[data-o="category"]');
    const pickKarat = () => {
      const karat = category.selectedOptions[0]?.dataset.karat;
      if (karat) row.querySelector('[data-o="karat"]').value = karat;
    };
    pickKarat();
    enhanceSelects(row);
    category.addEventListener("change", () => {
      const karat = category.selectedOptions[0]?.dataset.karat;
      const picker = row.querySelector('[data-o="karat"]');
      if (karat && picker.tomselect) picker.tomselect.setValue(karat);
      scheduleOrders();
    });
    row.addEventListener("input", scheduleOrders);
    row.addEventListener("change", scheduleOrders);
    row.querySelector("[data-remove-order]").addEventListener("click", () => {
      row.remove();
      scheduleOrders();
    });
    row.querySelector('[data-o="gross_weight_g"]').focus();
  });

  const body = () => ({
    branch: Number($("select[data-branch]").value),
    lines: barcodes.map((barcode) => ({ barcode, discount_rate: null })),
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
    $("[data-count]").textContent = barcodes.length + orders().length;
    const priced = ordersQuote?.lines || [];
    orderRows().filter((row) => row.querySelector('[data-o="gross_weight_g"]').value.trim())
      .forEach((row, index) => {
        row.querySelector("[data-o-total]").textContent = priced[index] ? formatNumber(priced[index].line_total) : "—";
      });
    const total = Number(quote?.totals?.total || 0) + Number(ordersQuote?.total || 0);
    $("[data-total]").textContent = formatNumber(total);
  };

  const refresh = async () => {
    if (!barcodes.length) {
      quote = null;
      return render();
    }
    const settled = later(() => refreshing([...root.querySelectorAll("[data-lines], [data-total]")]));
    try {
      quote = await api(root.dataset.quoteUrl, { method: "POST", body: body(), quiet: true });
    } catch (error) {
      fail(error);
      barcodes.pop(); // the last scan was refused
      if (barcodes.length) quote = await api(root.dataset.quoteUrl, { method: "POST", body: body(), quiet: true }).catch(() => null);
      else quote = null;
    } finally {
      settled();
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
    const data = { ...body(), orders: orders(), price_locked: $("[data-locked]").checked, note: $("[data-note]").value.trim(),
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
