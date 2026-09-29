// Buy / sell scrap. Buying asks the server to price the lines (scrap price per karat, less the
// loss); selling shows weight × agreed price as a hint. The server computes what is posted.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { enhanceSelects, filterOptions } from "../../core/js/selects.js";
import { addDecimals, formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { narrowHolders } from "../../treasury/js/holders.js";

const root = document.getElementById("scrap");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const buying = root.dataset.mode === "buy";
  const key = uuid();
  let timer = null;

  const who = () => root.querySelector("[data-who]:checked").value;
  const rows = () => [...root.querySelectorAll("[data-line]")];
  const lines = () => rows().flatMap((row) => {
    const gross = row.querySelector("[data-gross]").value.trim();
    if (!gross) return [];
    const line = { karat: Number(row.querySelector("[data-karat]").value), gross_weight_g: gross,
                   price: row.querySelector("[data-price]").value.trim() };
    if (buying) line.loss_weight_g = row.querySelector("[data-loss]").value.trim() || "0";
    return [line];
  });

  const syncPayment = () => {
    const block = $("[data-payment]");
    const method = block.querySelector("[data-method]").value;
    block.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
    narrowHolders({ branch: $("select[data-branch]").value, currency: block.querySelector("[data-currency]").value,
                    box: block.querySelector("[data-box]"), bank: block.querySelector("[data-bank]") });
  };

  const syncWho = () => root.querySelectorAll("[data-who-for]").forEach((node) =>
    node.classList.toggle("hidden", node.dataset.whoFor !== who()));

  const showErrors = (error) => {
    rows().forEach((row) => (row.querySelector("[data-line-error]").textContent = ""));
    const byLine = error?.fields?.lines;
    if (byLine && typeof byLine === "object" && !Array.isArray(byLine)) {
      const filled = rows().filter((row) => row.querySelector("[data-gross]").value.trim());
      for (const [index, messages] of Object.entries(byLine)) {
        const slot = filled[Number(index)]?.querySelector("[data-line-error]");
        if (slot) slot.textContent = [].concat(messages).join(" ");
      }
    }
  };

  const totals = (amounts) => {
    let weight = "0";
    let total = "0";
    rows().forEach((row, index) => {
      const gross = row.querySelector("[data-gross]").value.trim();
      if (gross) weight = addDecimals(weight, gross, 3);
      const amount = amounts[index];
      row.querySelector("[data-amount]").textContent = amount ? formatNumber(amount) : "—";
      if (amount) total = addDecimals(total, amount);
    });
    $("[data-total-weight]").textContent = formatNumber(weight, 3);
    $("[data-total]").textContent = formatNumber(total);
  };

  const refresh = async () => {
    if (!buying) { // weight × price, for display
      totals(rows().map((row) => {
        const gross = Number(row.querySelector("[data-gross]").value || 0);
        const price = Number(row.querySelector("[data-price]").value || 0);
        return gross && price ? (gross * price).toFixed(2) : null;
      }));
      return;
    }
    const filled = rows().filter((row) => row.querySelector("[data-gross]").value.trim());
    if (!filled.length) return totals([]);
    try {
      const quote = await api(root.dataset.quoteUrl, { method: "POST", body: {
        branch: Number($("select[data-branch]").value), lines: lines() } });
      showErrors(null);
      const amounts = rows().map((row) => {
        const index = filled.indexOf(row);
        if (index < 0) return null;
        const line = quote.lines[index];
        const price = row.querySelector("[data-price]");
        if (!price.value.trim()) price.placeholder = formatNumber(line.price_per_g);
        return line.amount;
      });
      totals(amounts);
    } catch (error) {
      if (error instanceof ApiError) showErrors(error);
    }
  };

  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  };

  const addLine = () => {
    const row = document.getElementById("scrap-line").content.firstElementChild.cloneNode(true);
    row.querySelector("[data-remove]").addEventListener("click", () => {
      row.remove();
      schedule();
    });
    $("[data-lines]").append(row);
    enhanceSelects(row);
    if (!buying) {
      filterOptions(row.querySelector("[data-karat]"),
                    (option) => option.dataset.branch === $("select[data-branch]").value);
    }
    row.querySelector("[data-gross]").focus();
  };

  root.addEventListener("input", (event) => {
    if (event.target.closest("[data-line]")) schedule();
  });
  root.addEventListener("change", (event) => {
    if (event.target.matches("[data-who]")) syncWho();
    if (event.target.matches("[data-method]")) syncPayment();
    if (event.target.matches("select[data-branch]")) {
      syncPayment();
      if (!buying) rows().forEach((row) => row.remove());
    }
    if (event.target.closest("[data-line]")) schedule();
  });
  $("[data-add-line]").addEventListener("click", addLine);

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const block = $("[data-payment]");
    const method = block.querySelector("[data-method]").value;
    const party = who() === "walk-in" ? null : root.querySelector(`[data-party="${who()}"]`).value;
    const pick = (selector) => (block.querySelector(selector)?.value ? Number(block.querySelector(selector).value) : null);
    const body = {
      branch: Number($("select[data-branch]").value), lines: lines(), payment: method,
      party: party ? Number(party) : null, note: $("[data-note]").value.trim(),
      cash_box: method === "cash" ? pick("[data-box]") : null,
      bank_account: method === "bank_transfer" ? pick("[data-bank]") : null,
    };
    if (buying && who() === "walk-in") {
      Object.assign(body, { seller_name: $("[data-seller-name]").value.trim(),
                            seller_phone: $("[data-seller-phone]").value.trim(),
                            seller_id_number: $("[data-seller-id]").value.trim() });
    }
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, { method: "POST", body, idempotencyKey: key });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      if (error instanceof ApiError) showErrors(error);
      button.disabled = false;
    }
  });

  addLine();
  syncWho();
  syncPayment();
}
