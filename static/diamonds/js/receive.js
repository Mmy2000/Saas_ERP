// Receive diamonds: pieces (or loose stones), each with its own stones, sent in one request.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { formatNumber } from "../../core/js/money.js";
import { enhanceSelects } from "../../core/js/selects.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("receive");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const pieces = $("[data-pieces]");
  const pieceTemplate = document.getElementById("piece-template");
  const stoneTemplate = document.getElementById("stone-template");
  const key = uuid();
  const number = (value) => Number(String(value || "").replace(/,/g, "")) || 0;

  const addStone = (piece) => {
    const row = stoneTemplate.content.firstElementChild.cloneNode(true);
    piece.querySelector("[data-stones]").append(row);
    enhanceSelects(row); // searchable, like every select in the system
  };

  const syncPiece = (piece) => {
    const option = piece.querySelector('[data-field="category"]').selectedOptions[0];
    const loose = option?.dataset.family === "stone";
    piece.querySelectorAll("[data-gold]").forEach((node) => node.classList.toggle("hidden", loose));
  };

  const addPiece = () => {
    const piece = pieceTemplate.content.firstElementChild.cloneNode(true);
    pieces.append(piece);
    enhanceSelects(piece);
    piece.querySelector('[data-field="category"]').addEventListener("change", () => syncPiece(piece));
    addStone(piece);
    syncPiece(piece);
    renumber();
  };

  const renumber = () => {
    [...pieces.querySelectorAll("[data-piece]")].forEach((piece, index) => {
      piece.querySelector("[data-piece-title]").textContent = `${index + 1}`;
    });
    refresh();
  };

  const refresh = () => {
    let carats = 0;
    let cost = 0;
    let label = 0;
    const all = [...pieces.querySelectorAll("[data-piece]")];
    for (const piece of all) {
      piece.querySelectorAll('[data-s="carat"]').forEach((input) => (carats += number(input.value)));
      cost += number(piece.querySelector('[data-field="stone_cost"]').value);
      label += number(piece.querySelector('[data-field="label_price"]').value);
    }
    $('[data-t="pieces"]').textContent = String(all.length);
    $('[data-t="carats"]').textContent = formatNumber(carats, 3);
    $('[data-t="cost"]').textContent = formatNumber(cost, 2);
    $('[data-t="label"]').textContent = formatNumber(label, 2);
  };

  const body = () => ({
    supplier: Number($("[data-supplier]").value) || null,
    branch: Number($("[data-branch]").value),
    currency: $("[data-currency]").value,
    supplier_reference: $("[data-reference]").value.trim(),
    note: $("[data-note]").value.trim(),
    pieces: [...pieces.querySelectorAll("[data-piece]")].map((piece) => {
      const field = (name) => piece.querySelector(`[data-field="${name}"]`).value.trim();
      const loose = piece.querySelector('[data-field="category"]').selectedOptions[0]?.dataset.family === "stone";
      return {
        category: Number(field("category")), karat: loose ? null : Number(field("karat")),
        gross_weight_g: loose ? null : field("gross_weight_g"),
        making_cost_rate: loose ? "0" : field("making_cost_rate") || "0",
        stone_cost: field("stone_cost") || "0", label_price: field("label_price") || null,
        stones: [...piece.querySelectorAll("[data-stone]")]
          .filter((row) => row.querySelector('[data-s="carat"]').value.trim())
          .map((row) => Object.fromEntries([...row.querySelectorAll("[data-s]")].map(
            (input) => [input.dataset.s, input.value.trim()]))),
      };
    }),
  });

  // Errors come back as pieces.<n>.<field>: show them on that piece.
  const showErrors = (error) => {
    root.querySelectorAll("[data-piece-error]").forEach((node) => (node.textContent = ""));
    const fields = error.fields || {};
    const byPiece = fields.pieces || {};
    const cards = [...pieces.querySelectorAll("[data-piece]")];
    for (const [index, problems] of Object.entries(byPiece)) {
      const card = cards[Number(index)];
      if (card && problems && typeof problems === "object") {
        card.querySelector("[data-piece-error]").textContent = Object.values(problems).flat().join(" ");
      }
    }
    for (const [name, problems] of Object.entries(fields)) {
      const match = name.match(/^pieces\.(\d+)\.stones$/);
      if (match && cards[Number(match[1])]) {
        cards[Number(match[1])].querySelector("[data-piece-error]").textContent = JSON.stringify(problems).replace(/[{}"[\]]/g, " ");
      }
    }
    const supplierError = root.querySelector('[data-error-for="supplier"]');
    supplierError.textContent = (fields.supplier || []).join(" ");
    $("[data-error]").textContent = error.message;
  };

  root.addEventListener("click", (event) => {
    const piece = event.target.closest("[data-piece]");
    if (event.target.closest("[data-add-piece]")) addPiece();
    else if (event.target.closest("[data-remove-piece]") && piece) { piece.remove(); renumber(); }
    else if (event.target.closest("[data-add-stone]") && piece) addStone(piece);
    else if (event.target.closest("[data-remove-stone]")) { event.target.closest("[data-stone]").remove(); refresh(); }
  });
  root.addEventListener("input", refresh);

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, { method: "POST", body: body(), idempotencyKey: key });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      if (error instanceof ApiError) showErrors(error);
      else toast(strings().network, { kind: "error" });
      button.disabled = false;
    }
  });

  root.querySelector("[data-add-piece]") && addPiece();
}
