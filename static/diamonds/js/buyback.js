// Buying old diamonds: pieces (with the gold in each at today's value as a guide), the seller,
// how it is paid, then post.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { later, refreshing, startNavigation } from "../../core/js/loading.js";
import { addDecimals, formatNumber } from "../../core/js/money.js";
import { enhanceSelects } from "../../core/js/selects.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { narrowHolders } from "../../treasury/js/holders.js";

const root = document.getElementById("buy-back");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const key = uuid();
  const template = document.getElementById("buy-back-piece");
  let timer = null;
  const pieces = () => [...root.querySelectorAll("[data-piece]")];
  const who = () => root.querySelector("[data-who]:checked").value;
  const field = (row, name) => row.querySelector(`[data-f="${name}"]`).value.trim();
  const loose = (row) => row.querySelector('[data-f="category"]').selectedOptions[0]?.dataset.family === "stone";

  const body = (row) => ({
    category: Number(field(row, "category")), karat: loose(row) ? null : Number(field(row, "karat")),
    gross_weight_g: loose(row) ? null : field(row, "gross_weight_g"),
    stones_carat: field(row, "stones_carat") || "0", stones_count: Number(field(row, "stones_count")) || 1,
    stones_note: field(row, "stones_note"), price: field(row, "price"), label_price: field(row, "label_price") || null,
  });

  const totals = (golds = null) => {
    $("[data-total]").textContent = formatNumber(pieces().reduce((sum, row) => addDecimals(sum, field(row, "price") || "0"), "0"));
    if (golds) $("[data-total-gold]").textContent = formatNumber(golds.reduce((sum, gold) => addDecimals(sum, gold || "0"), "0"));
  };

  const quote = async () => {
    const rows = pieces();
    totals();
    if (!rows.length) return totals(["0"]);
    const settled = later(() => refreshing([...root.querySelectorAll("[data-gold-value], [data-total-gold]")]));
    try {
      const answer = await api(root.dataset.quoteUrl, { method: "POST", quiet: true, body: { pieces: rows.map(body) } });
      answer.pieces.forEach((piece, index) => {
        rows[index].querySelector("[data-gold-value]").textContent = piece.gold_value == null ? "—" : formatNumber(piece.gold_value);
      });
      totals(answer.pieces.map((piece) => piece.gold_value));
    } catch {
      // A half-typed line: the guide waits for the next change.
    } finally {
      settled();
    }
  };
  const schedule = () => { clearTimeout(timer); timer = setTimeout(quote, 300); };

  const syncPiece = (row) => row.querySelectorAll("[data-gold]").forEach((node) => node.classList.toggle("hidden", loose(row)));
  const addPiece = () => {
    const row = template.content.firstElementChild.cloneNode(true);
    $("[data-pieces]").append(row);
    enhanceSelects(row);
    syncPiece(row);
    row.querySelector('[data-f="gross_weight_g"]').focus();
  };

  const syncWho = () => root.querySelectorAll("[data-who-for]").forEach((node) => node.classList.toggle("hidden", node.dataset.whoFor !== who()));
  const syncHolders = () => {
    const method = $("[data-method]").value;
    root.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
    narrowHolders({ branch: $("[data-branch]").value, currency: root.dataset.homeCurrency, box: $("[data-box]"), bank: $("[data-bank]") });
  };

  root.addEventListener("click", (event) => {
    if (event.target.closest("[data-add-piece]")) addPiece();
    const remove = event.target.closest("[data-remove-piece]");
    if (remove) { remove.closest("[data-piece]").remove(); schedule(); }
  });
  root.addEventListener("input", (event) => { if (event.target.closest("[data-piece]")) schedule(); });
  root.addEventListener("change", (event) => {
    const row = event.target.closest("[data-piece]");
    if (row) { syncPiece(row); schedule(); }
    if (event.target.matches("[data-who]")) syncWho();
    if (event.target.matches("[data-method], [data-branch]")) syncHolders();
  });

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    root.querySelectorAll("[data-error-for], [data-piece-error]").forEach((node) => (node.textContent = ""));
    $("[data-error]").textContent = "";
    button.disabled = true;
    const method = $("[data-method]").value;
    const pick = (selector) => Number($(selector)?.value) || null;
    try {
      const made = await api(root.dataset.postUrl, {
        method: "POST", idempotencyKey: key,
        body: {
          branch: Number($("[data-branch]").value), pieces: pieces().map(body), payment: method,
          seller: who() === "customer" ? Number($("[data-seller]").value) || null : null,
          seller_name: who() === "walk-in" ? $("[data-seller-name]").value.trim() : "",
          seller_phone: who() === "walk-in" ? $("[data-seller-phone]").value.trim() : "",
          seller_id_number: who() === "walk-in" ? $("[data-seller-id]").value.trim() : "",
          cash_box: method === "cash" ? pick("[data-box]") : null,
          bank_account: method === "bank_transfer" ? pick("[data-bank]") : null,
          note: $("[data-note]").value.trim(),
        },
      });
      flash(root.dataset.saved);
      const next = root.dataset.detailUrl.replace("{id}", made.id);
      startNavigation(next);
      window.location.assign(next);
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [name, problems] of Object.entries(error.fields || {})) {
          const piece = name.match(/^pieces\.(\d+)$/);
          const node = piece ? pieces()[Number(piece[1])]?.querySelector("[data-piece-error]")
            : root.querySelector(`[data-error-for="${name}"]`);
          if (node) node.textContent = [].concat(problems).join(" ");
        }
        $("[data-error]").textContent = error.message;
      } else toast(strings().network, { kind: "error" });
      button.disabled = false;
    }
  });

  addPiece();
  syncWho();
  syncHolders();
}
