// Receiving a work order: lines of what came back (new pieces, gold by weight, scrap), with
// the server's figures for fine gold, loss or gain and labour shown as you type.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { formatNumber } from "../../core/js/money.js";
import { enhanceSelects } from "../../core/js/selects.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("receipt");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const list = $("[data-lines]");
  const template = document.getElementById("receipt-line");
  const key = uuid();
  let timer = null;
  let asked = 0;

  const kindOf = (row) => row.querySelector('[data-field="category"]').selectedOptions[0]?.dataset.kind || "pieces";
  const read = (row, field) => row.querySelector(`[data-field="${field}"]`).value.trim();

  const refreshRow = (row) => {
    const kind = kindOf(row);
    row.querySelectorAll("[data-for]").forEach((node) => node.classList.toggle("hidden", !node.dataset.for.split(" ").includes(kind)));
  };

  const applyDefaultKarat = (row) => {
    const karat = row.querySelector('[data-field="category"]').selectedOptions[0]?.dataset.karat;
    const select = row.querySelector('[data-field="karat"]');
    if (!karat || select.value) return;
    if (select.tomselect) select.tomselect.setValue(karat);
    else select.value = karat;
  };

  const addLine = () => {
    const row = template.content.firstElementChild.cloneNode(true);
    list.append(row);
    enhanceSelects(row);
    applyDefaultKarat(row);
    refreshRow(row);
  };

  // Rows that are filled in enough to send; half-typed rows are left out of the preview.
  const lines = ({ all = false } = {}) => [...list.querySelectorAll("[data-line]")].flatMap((row) => {
    const kind = kindOf(row);
    const line = {
      category: Number(read(row, "category")), karat: read(row, "karat") ? Number(read(row, "karat")) : null,
      labour_rate: kind === "scrap" ? "0" : read(row, "labour_rate") || "0",
      list_making_rate: kind === "pieces" ? read(row, "list_making_rate") || "0" : "0",
    };
    if (kind === "pieces") line.piece_weights = read(row, "piece_weights").split(/[\s,،;]+/).filter(Boolean);
    else {
      line.gross_weight_g = read(row, "gross_weight_g");
      line.qty = kind === "bulk" ? Number(read(row, "qty") || 0) : 0;
    }
    const ready = line.karat && (kind === "pieces" ? line.piece_weights.length : line.gross_weight_g);
    return all || ready ? [line] : [];
  });

  const show = (preview) => {
    const gain = Number(preview.gain_fine_g) > 0;
    $('[data-p="received_fine_g"]').textContent = formatNumber(preview.received_fine_g, 4);
    $('[data-p="labour"]').textContent = formatNumber(preview.labour, 2);
    const difference = $('[data-p="difference"]');
    difference.textContent = formatNumber(gain ? preview.gain_fine_g : preview.loss_fine_g, 4);
    difference.classList.toggle("text-red-600", !gain && Number(preview.loss_fine_g) > 0);
    difference.classList.toggle("text-emerald-600", gain);
    const label = $("[data-diff-label]");
    label.textContent = gain ? label.dataset.gain : label.dataset.loss;
    $("[data-gain-box]").classList.toggle("hidden", !gain);
    $("[data-gain-box]").classList.toggle("flex", gain);
  };

  const previewNow = async () => {
    const ticket = ++asked;
    const ready = lines();
    $("[data-error]").textContent = "";
    if (!ready.length) return;
    try {
      const preview = await api(root.dataset.previewUrl, { method: "POST", body: { lines: ready } });
      if (ticket === asked) show(preview);
    } catch (error) {
      if (ticket === asked && error instanceof ApiError) $("[data-error]").textContent = error.message;
    }
  };
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(previewNow, 300);
  };

  addLine();
  $("[data-add-line]").addEventListener("click", addLine);
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-line]");
    if (button && list.querySelectorAll("[data-line]").length > 1) {
      button.closest("[data-line]").remove();
      refresh();
    }
  });
  list.addEventListener("change", (event) => {
    const row = event.target.closest("[data-line]");
    if (!row) return;
    if (event.target.matches('[data-field="category"]')) applyDefaultKarat(row);
    refreshRow(row);
    refresh();
  });
  list.addEventListener("input", refresh);

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await api(root.dataset.receiveUrl, {
        method: "POST", idempotencyKey: key,
        body: { lines: lines({ all: true }), accept_gain: $("[data-accept-gain]").checked, note: $("[data-note]").value.trim() },
      });
      flash(root.dataset.saved);
      window.location.reload();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : strings().network;
      $("[data-error]").textContent = message;
      toast(message, { kind: "error" });
      button.disabled = false;
    }
  });
}
