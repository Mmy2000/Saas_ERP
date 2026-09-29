// Stocktake counting: scan → the server records it and returns the new totals; weights of bulk
// lots are saved as they are typed; posting asks for confirmation.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("stocktake");

if (root) {
  const endpoint = root.dataset.endpoint;
  const $ = (selector) => root.querySelector(selector);
  const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

  const showSummary = (summary) => {
    for (const [key, value] of Object.entries(summary)) {
      root.querySelectorAll(`[data-count="${key}"]`).forEach((node) => (node.textContent = value));
    }
    $("[data-progress]").style.width = `${summary.progress}%`;
  };

  const feedback = (text, tone) => {
    const node = $("[data-feedback]");
    if (!node) return;
    node.textContent = text;
    node.className = `border-t px-5 py-3 text-sm font-medium ${{
      counted: "border-emerald-100 bg-emerald-50 text-emerald-800",
      already: "border-slate-100 bg-slate-50 text-slate-700",
      unexpected: "border-amber-100 bg-amber-50 text-amber-800",
    }[tone]}`;
  };

  const recentRow = (line) => {
    const li = document.createElement("li");
    li.className = "flex items-center justify-between gap-3 px-5 py-2.5 text-sm";
    li.dataset.line = line.id;
    const info = document.createElement("div");
    info.className = "min-w-0";
    const code = document.createElement("span");
    code.className = "num font-medium text-slate-900";
    code.textContent = line.barcode;
    const detail = document.createElement("span");
    detail.className = "text-slate-500";
    detail.textContent = line.category ? ` · ${line.category}${line.karat ? ` · ${line.karat}` : ""}` : "";
    info.append(code, detail);
    const actions = document.createElement("div");
    actions.className = "flex items-center gap-2";
    const badge = document.createElement("span");
    badge.className = `badge ${line.expected ? "badge-success" : "badge-warning"}`;
    badge.textContent = line.result_label;
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "btn btn-ghost text-xs";
    undo.dataset.undo = line.id;
    undo.textContent = root.dataset.undoLabel;
    actions.append(badge, undo);
    li.append(info, actions);
    return li;
  };

  $("[data-scan]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = event.currentTarget.querySelector("input");
    const barcode = input.value.trim();
    input.value = "";
    input.focus();
    if (!barcode) return;
    try {
      const data = await api(`${endpoint}scan/`, { method: "POST", body: { barcode } });
      showSummary(data.summary);
      const line = data.line;
      const label = line.category ? `${line.barcode} · ${line.category}` : line.barcode;
      if (data.outcome === "already") return feedback(`${label} — ${root.dataset.already}`, "already");
      feedback(`${label} — ${line.result_label}${line.status_label && !line.expected ? ` (${line.status_label})` : ""}`, data.outcome);
      $("[data-recent]").prepend(recentRow(line));
      root.querySelector(`[data-pending="${CSS.escape(line.barcode)}"]`)?.remove();
    } catch (error) {
      fail(error);
    }
  });

  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-undo]");
    if (!button) return;
    try {
      const data = await api(`${endpoint}undo/`, { method: "POST", body: { line: Number(button.dataset.undo) } });
      showSummary(data.summary);
      button.closest("[data-line]").remove();
    } catch (error) {
      fail(error);
    }
  });

  root.addEventListener("change", async (event) => {
    const row = event.target.closest("[data-lot-line]");
    if (!row || !event.target.matches("[data-weigh], [data-weigh-qty]")) return;
    const weight = row.querySelector("[data-weigh]").value.trim();
    if (!weight) return;
    const qty = row.querySelector("[data-weigh-qty]").value.trim();
    try {
      const data = await api(`${endpoint}weigh/`, { method: "POST", body: {
        line: Number(row.dataset.lotLine), gross_weight_g: weight, qty: qty === "" ? null : Number(qty) } });
      showSummary(data.summary);
      toast(root.dataset.saved);
    } catch (error) {
      fail(error);
    }
  });

  $("[data-post]")?.addEventListener("click", async (event) => {
    if (!window.confirm(root.dataset.confirm)) return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await api(`${endpoint}post/`, { method: "POST", body: {}, idempotencyKey: uuid() });
      flash("");
      window.location.reload();
    } catch (error) {
      fail(error);
      button.disabled = false;
    }
  });
}
