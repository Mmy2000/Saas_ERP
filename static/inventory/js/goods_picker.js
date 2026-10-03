// Picking goods at a branch (templates/inventory/_goods_picker.html): pieces by barcode, checked
// against the API as they are scanned, and bulk gold by weight from the branch's lots. Used by
// stock transfers, returns to suppliers and wholesale; the server re-checks everything on posting.
import { api, ApiError } from "../../core/js/api.js";
import { enhanceSelects, filterOptions } from "../../core/js/selects.js";
import { strings, toast } from "../../core/js/ui.js";
import { addDecimals, formatNumber } from "../../core/js/money.js";

/** `root` holds the picker and data-items-url / data-not-found; `branch` is the branch select.
 *  `onChange` is called whenever the picked goods change. */
export function goodsPicker(root, branch, { onChange = () => {} } = {}) {
  const $ = (selector) => root.querySelector(selector);
  const pieces = [];

  const render = () => {
    const body = $("[data-pieces]");
    if (!body) return onChange(); // the picker shows only gold by weight
    body.replaceChildren();
    let weight = "0";
    pieces.forEach((piece, index) => {
      const row = document.getElementById("piece-row").content.firstElementChild.cloneNode(true);
      const set = (field, value) => (row.querySelector(`[data-f="${field}"]`).textContent = value);
      set("barcode", piece.barcode);
      set("category", piece.category_name);
      set("karat", piece.karat_label || "");
      set("weight", formatNumber(piece.gross_weight_g, 3));
      row.querySelector("[data-remove]").addEventListener("click", () => {
        pieces.splice(index, 1);
        render();
      });
      body.append(row);
      weight = addDecimals(weight, piece.gross_weight_g, 3);
    });
    $("[data-pieces-empty]").classList.toggle("hidden", pieces.length > 0);
    root.querySelectorAll("[data-piece-count], [data-total-pieces]").forEach((node) => (node.textContent = pieces.length));
    const total = $("[data-total-weight]");
    if (total) total.textContent = formatNumber(weight, 3);
    onChange();
  };

  $("[data-scan]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = event.currentTarget.querySelector("input");
    const barcode = input.value.trim();
    input.value = "";
    if (!barcode) return;
    if (pieces.some((piece) => piece.barcode === barcode)) return toast(barcode, { kind: "error" });
    try {
      const data = await api(`${root.dataset.itemsUrl}?barcode=${encodeURIComponent(barcode)}`);
      const item = (data.results || [])[0];
      if (!item) return toast(`${barcode}: ${root.dataset.notFound || "?"}`, { kind: "error" });
      if (item.status !== "in_stock" || String(item.branch) !== branch.value) {
        return toast(`${barcode}: ${item.status_label} · ${item.branch_name}`, { kind: "error" });
      }
      pieces.push(item);
      render();
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
    } finally {
      input.focus();
    }
  });

  const narrowLots = () => root.querySelectorAll("[data-lot]").forEach((select) =>
    filterOptions(select, (option) => option.dataset.branch === branch.value));

  $("[data-add-bulk]").addEventListener("click", () => {
    const row = document.getElementById("bulk-row").content.firstElementChild.cloneNode(true);
    row.querySelector("[data-remove]").addEventListener("click", () => {
      row.remove();
      onChange();
    });
    row.addEventListener("change", () => onChange());
    $("[data-bulk]").append(row);
    enhanceSelects(row);
    narrowLots();
  });

  branch.addEventListener("change", () => {
    pieces.splice(0, pieces.length); // they were checked against the old branch
    render();
    narrowLots();
  });

  render();
  return {
    /** The pieces picked so far, as the items API returned them. */
    pieces: () => [...pieces],
    /** The lines to post: pieces by id, then bulk rows with a weight. */
    lines() {
      const bulk = [...root.querySelectorAll("[data-bulk-row]")].flatMap((row) => {
        const [category, karat] = row.querySelector("[data-lot]").value.split(":");
        const weight = row.querySelector("[data-weight]").value.trim();
        if (!category || !weight) return [];
        return [{ category: Number(category), karat: karat ? Number(karat) : null, gross_weight_g: weight,
                  qty: Number(row.querySelector("[data-qty]").value || 0) }];
      });
      return [...pieces.map((piece) => ({ item: piece.id })), ...bulk];
    },
  };
}
