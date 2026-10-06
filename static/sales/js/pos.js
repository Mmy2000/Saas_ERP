// Sales screen. Holds what the seller entered (barcodes, discounts, scrap, payments), asks the
// server for a quote after every change, and renders the server's numbers. Posting sends the
// same inputs; the server recomputes everything.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { enhanceSelects, filterOptions } from "../../core/js/selects.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { narrowHolders } from "../../treasury/js/holders.js";
import { addDecimals, formatNumber, percentToRate, rateToPercent } from "../../core/js/money.js";

const root = document.getElementById("pos");

if (root) {
  const state = { lines: [], quote: null, seq: 0, key: uuid() };
  const $ = (selector) => root.querySelector(selector);
  const lineTemplate = document.getElementById("line-row");
  const tradeTemplate = document.getElementById("trade-row");
  let timer = null;

  const body = () => {
    const customer = $("[data-customer]").value;
    return {
      branch: Number($("select[data-branch]").value),
      lines: state.lines.map((line) => (line.bulk
        ? { category: line.category, karat: line.karat, gross_weight_g: line.gross_weight_g,
            qty: line.qty, discount_rate: line.discount_rate }
        : { barcode: line.barcode, discount_rate: line.discount_rate })),
      trade_ins: [...root.querySelectorAll("[data-trade]")].flatMap((row) => {
        const read = (field) => row.querySelector(`[data-t="${field}"]`)?.value.trim() || "";
        if (!read("gross_weight_g")) return [];
        const trade = { karat: Number(read("karat")), gross_weight_g: read("gross_weight_g"),
                        loss_weight_g: read("loss_weight_g") || "0" };
        if (read("price_override")) trade.price_override = read("price_override");
        return [trade];
      }),
      payments: [...root.querySelectorAll("[data-payment]")].flatMap((input) => {
        if (!input.value.trim()) return [];
        const payment = { kind: input.dataset.payment, currency: input.dataset.currency, amount: input.value.trim() };
        const terminal = $("[data-terminal]").value;
        const bank = $("[data-bank]").value;
        if (payment.kind === "card" && terminal) payment.terminal = Number(terminal);
        if (payment.kind === "bank_transfer" && bank) payment.bank_account = Number(bank);
        return [payment];
      }),
      payment_terms: root.querySelector("[data-terms]:checked").value,
      customer: customer ? Number(customer) : null,
      customer_name: customer ? "" : $("[data-customer-name]").value.trim(),
      customer_phone: customer ? "" : $("[data-customer-phone]").value.trim(),
    };
  };

  function renderLines() {
    const tbody = $("[data-lines]");
    tbody.replaceChildren();
    $("[data-lines-empty]").classList.toggle("hidden", state.lines.length > 0);
    const priced = state.quote?.lines || [];
    state.lines.forEach((line, index) => {
      const row = lineTemplate.content.firstElementChild.cloneNode(true);
      const quote = priced[index] || {};
      const set = (field, value) => (row.querySelector(`[data-f="${field}"]`).textContent = value);
      set("barcode", line.bulk ? root.dataset.byWeight : line.barcode);
      set("description", quote.description || "");
      set("karat", quote.karat || "");
      set("gross_weight_g", formatNumber(quote.gross_weight_g, 3));
      if (quote.label_price) {
        // Diamond pieces and stones sell at their label price, not gold price + making.
        set("metal_price_per_g", "—");
        set("making_rate_net", `${root.dataset.labelPrice} ${formatNumber(quote.label_price)}`);
      } else {
        set("metal_price_per_g", formatNumber(quote.metal_price_per_g));
        set("making_rate_net", formatNumber(quote.making_rate_net));
      }
      set("line_total", formatNumber(quote.line_total));
      row.querySelector("[data-floor]").classList.toggle("hidden", !quote.at_cost_floor);
      const discount = row.querySelector("[data-discount]");
      discount.value = line.discount_text ?? rateToPercent(line.discount_rate);
      discount.addEventListener("change", () => {
        line.discount_text = discount.value.trim();
        line.discount_rate = percentToRate(line.discount_text);
        requestQuote();
      });
      row.querySelector("[data-remove]").addEventListener("click", () => {
        state.lines.splice(index, 1);
        requestQuote();
      });
      tbody.append(row);
    });
  }

  function render() {
    const quote = state.quote;
    renderLines();
    const totals = quote?.totals || {};
    for (const key of ["subtotal", "discount", "trade_in", "due", "paid", "remaining", "change", "balance"]) {
      const node = root.querySelector(`[data-total="${key}"]`);
      if (node) node.textContent = formatNumber(totals[key] ?? "0");
    }
    const show = (row, visible) => {
      const node = root.querySelector(`[data-row="${row}"]`);
      node.classList.toggle("hidden", !visible);
      node.classList.toggle("flex", visible);
    };
    show("remaining", Number(totals.remaining || 0) > 0);
    show("change", Number(totals.change || 0) > 0);
    show("balance", Number(totals.balance || 0) > 0);
    const trades = quote?.trade_ins || [];
    root.querySelectorAll("[data-trade]").forEach((row) => {
      const filled = row.querySelector('[data-t="gross_weight_g"]').value.trim();
      const index = [...root.querySelectorAll("[data-trade]")].filter(
        (r) => r.querySelector('[data-t="gross_weight_g"]').value.trim()).indexOf(row);
      row.querySelector("[data-t-amount]").textContent = filled && trades[index]
        ? formatNumber(trades[index].amount) : "—";
    });
    const problems = $("[data-problems]");
    problems.replaceChildren(...(quote?.problems || [])
      .filter((p) => p.code !== "SALES_EMPTY")
      .map((p) => Object.assign(document.createElement("li"), { textContent: p.message })));
    $("[data-complete]").disabled = !quote || !state.lines.length || (quote.problems || []).length > 0;
  }

  async function requestQuote() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const seq = ++state.seq;
      try {
        const quote = await api(root.dataset.quoteUrl, { method: "POST", body: body() });
        if (seq !== state.seq) return;
        state.quote = quote;
      } catch (error) {
        if (seq !== state.seq) return;
        if (!(error instanceof ApiError)) return toast(strings().network, { kind: "error" });
        // A bad line (unknown barcode, sold piece, discount over the limit) is taken out.
        const bad = Object.keys(error.fields?.lines || {}).map(Number);
        const badTrades = Object.keys(error.fields?.trade_ins || {}).map(Number);
        toast(error.message, { kind: "error" });
        if (bad.length) {
          const [index] = bad;
          if (state.lines[index]?.fresh) state.lines.splice(index, 1);
          else if (state.lines[index]) {
            state.lines[index].discount_rate = "0";
            state.lines[index].discount_text = "";
          }
          return requestQuote();
        }
        if (!badTrades.length) state.quote = null;
      }
      state.lines.forEach((line) => (line.fresh = false));
      render();
    }, 150);
  }

  $("[data-scan]").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = event.target.elements.barcode;
    const barcode = input.value.trim();
    input.value = "";
    input.focus();
    if (!barcode) return;
    if (state.lines.some((line) => line.barcode === barcode)) return toast(barcode, { kind: "error" });
    state.lines.push({ barcode, discount_rate: "0", fresh: true });
    requestQuote();
  });

  // Bulk gold by weight: one line per entry; the server checks the lot has enough.
  const bulkForm = $("[data-bulk-form]");
  const narrowLots = () => {
    if (bulkForm) filterOptions(bulkForm.querySelector("[data-bulk-lot]"),
                                (option) => option.dataset.branch === $("select[data-branch]").value);
  };
  bulkForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const [category, karat] = bulkForm.querySelector("[data-bulk-lot]").value.split(":");
    const weight = bulkForm.querySelector("[data-bulk-weight]");
    const qty = bulkForm.querySelector("[data-bulk-qty]");
    if (!category || !weight.value.trim()) return weight.focus();
    state.lines.push({ bulk: true, category: Number(category), karat: Number(karat),
                       gross_weight_g: weight.value.trim(), qty: Number(qty.value || 0),
                       discount_rate: "0", fresh: true });
    weight.value = "";
    qty.value = "";
    requestQuote();
  });
  $("select[data-branch]").addEventListener("change", narrowLots);
  narrowLots();

  root.querySelector("[data-add-trade]").addEventListener("click", () => {
    const row = tradeTemplate.content.firstElementChild.cloneNode(true);
    $("[data-trades]").append(row);
    enhanceSelects(row);
    row.querySelector('[data-t="gross_weight_g"]').focus();
    row.querySelector("[data-remove-trade]").addEventListener("click", () => {
      row.remove();
      requestQuote();
    });
  });

  root.addEventListener("input", (event) => {
    if (event.target.closest("[data-trade], [data-payments], [data-walk-in]")) requestQuote();
  });
  // Terminal / bank account pickers appear only when the branch has more than one.
  const syncHolders = () => {
    const counts = narrowHolders({ branch: $("select[data-branch]").value, currency: root.dataset.homeCurrency,
                                   terminal: $("[data-terminal]"), bank: $("[data-bank]") });
    $('[data-holder-row="terminal"]').classList.toggle("hidden", counts.terminal < 2);
    $('[data-holder-row="bank"]').classList.toggle("hidden", counts.bank < 2);
  };

  root.addEventListener("change", (event) => {
    if (event.target.matches("select[data-branch]")) syncHolders();
    if (event.target.matches("[data-branch], [data-terms], [data-customer]") ||
        event.target.closest("[data-trade]")) requestQuote();
    if (event.target.matches("[data-customer]")) {
      root.querySelector("[data-walk-in]").classList.toggle("hidden", Boolean(event.target.value));
    }
  });

  $("[data-pay-rest]").addEventListener("click", () => {
    const remaining = state.quote?.totals?.remaining;
    if (!remaining || Number(remaining) <= 0) return;
    const cash = root.querySelector(`[data-payment="cash"][data-currency="${root.dataset.homeCurrency}"]`);
    cash.value = addDecimals(cash.value || "0", remaining);
    requestQuote();
  });

  $("[data-complete]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const invoice = await api(root.dataset.postUrl, { method: "POST", body: body(), idempotencyKey: state.key });
      flash(strings().sale_done || "");
      window.location.assign(root.dataset.receiptUrl.replace("{id}", invoice.id));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
      if (error instanceof ApiError) state.key = uuid(); // a rejected sale may be corrected and retried
    }
  });

  syncHolders();
  render();
}
