// Wholesale to a trade account: pick goods, set the making charge per gram for each category,
// see the server's pricing live, and post in one request (the server prices it again).
import { api, ApiError, uuid } from "../../core/js/api.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { goodsPicker } from "../../inventory/js/goods_picker.js";

const root = document.getElementById("trade-sale");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const rates = new Map(); // category id → making charge per gram, as typed
  const key = uuid();
  let timer = null;
  let asked = 0;

  const basis = () => root.querySelector("[data-basis]:checked").value;

  const lines = () => {
    const categories = new Map(picker.pieces().map((piece) => [piece.id, piece.category]));
    return picker.lines().map((line) => {
      const category = line.item ? categories.get(line.item) : line.category;
      return { ...line, making_rate: rates.has(category) ? rates.get(category) : null };
    });
  };

  const body = () => {
    const account = $("[data-account]").value;
    return {
      branch: Number($("select[data-branch]").value), trade_account: account ? Number(account) : null,
      settlement_basis: basis(), lines: lines(), note: $("[data-note]").value.trim(),
    };
  };

  const showBasis = () => {
    root.querySelectorAll("[data-basis-hint]").forEach((node) => node.classList.toggle("hidden", node.dataset.basisHint !== basis()));
    root.querySelectorAll("[data-show-for]").forEach((node) => node.classList.toggle("hidden", node.dataset.showFor !== basis()));
  };

  const renderRates = (categories) => {
    const box = $("[data-rates]");
    box.replaceChildren();
    categories.forEach((category) => {
      const row = document.getElementById("rate-row").content.firstElementChild.cloneNode(true);
      row.querySelector('[data-f="name"]').textContent = category.name;
      const input = row.querySelector("[data-rate]");
      input.value = rates.has(category.id) ? rates.get(category.id) : category.list_rate;
      input.dataset.category = category.id;
      input.addEventListener("change", () => {
        rates.set(category.id, input.value.trim() || "0");
        refresh();
      });
      box.append(row);
    });
    $("[data-rates-empty]").classList.toggle("hidden", categories.length > 0);
  };

  const renderQuote = (quote) => {
    const table = $("[data-priced]");
    table.replaceChildren();
    quote.lines.forEach((line) => {
      const row = document.getElementById("priced-row").content.firstElementChild.cloneNode(true);
      const set = (field, value) => (row.querySelector(`[data-f="${field}"]`).textContent = value);
      set("barcode", line.barcode);
      set("category", line.category_name);
      set("karat", line.karat);
      set("weight", formatNumber(line.gross_weight_g, 3));
      set("fine", formatNumber(line.fine_weight_g, 4));
      set("metal", formatNumber(line.metal_amount, 2));
      set("making", formatNumber(line.making_amount, 2));
      table.append(row);
    });
    const places = { qty: 0, gross_weight_g: 3, fine_weight_g: 4 };
    root.querySelectorAll("[data-t]").forEach((node) => {
      node.textContent = formatNumber(quote.totals[node.dataset.t], places[node.dataset.t] ?? 2);
    });
    renderRates(quote.categories);
  };

  const quoteNow = async () => {
    const payload = body();
    const ticket = ++asked;
    if (!payload.lines.length) {
      return renderQuote({ lines: [], categories: [], totals: { qty: 0, gross_weight_g: 0, fine_weight_g: 0, metal_amount: 0, making_amount: 0, money_amount: 0 } });
    }
    try {
      const quote = await api(root.dataset.quoteUrl, { method: "POST", body: payload });
      if (ticket === asked) renderQuote(quote);
    } catch (error) {
      if (ticket === asked) toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
    }
  };

  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(quoteNow, 250);
  }

  const picker = goodsPicker(root, $("select[data-branch]"), { onChange: refresh });

  root.addEventListener("change", (event) => {
    if (event.target.matches("[data-basis]")) {
      showBasis();
      refresh();
    }
  });
  showBasis();

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, { method: "POST", body: body(), idempotencyKey: key });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
    }
  });
}
