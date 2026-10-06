// Bank reconciliation: ticking a movement saves the ticks and shows the new cleared balance and
// difference; the statement can be completed once the difference is zero.
import { api, ApiError } from "../../core/js/api.js";
import { formatNumber } from "../../core/js/money.js";
import { strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("reconcile");

if (root && !root.dataset.completed) {
  const boxes = [...root.querySelectorAll("[data-line]")];
  const all = root.querySelector("[data-all]");
  const complete = root.querySelector("[data-complete]");
  const error = root.querySelector("[data-error]");
  let timer = null;

  const show = (figures) => {
    root.querySelector('[data-f="cleared"]').textContent = formatNumber(figures.cleared, 2);
    const difference = root.querySelector('[data-f="difference"]');
    difference.textContent = formatNumber(figures.difference, 2);
    const settled = Number(figures.difference) === 0;
    difference.classList.toggle("text-red-600", !settled);
    difference.classList.toggle("text-emerald-600", settled);
    if (complete) complete.disabled = !settled;
  };

  const save = async () => {
    error.textContent = "";
    try {
      show(await api(root.dataset.tickUrl, {
        method: "POST", body: { lines: boxes.filter((box) => box.checked).map((box) => Number(box.value)) },
      }));
    } catch (failure) {
      if (failure instanceof ApiError) error.textContent = failure.message;
      else toast(strings().network, { kind: "error" });
    }
  };

  const changed = () => {
    boxes.forEach((box) => box.closest("[data-row]").classList.toggle("bg-emerald-50/40", box.checked));
    if (all) all.checked = boxes.length > 0 && boxes.every((box) => box.checked);
    if (complete) complete.disabled = true; // until the server confirms the difference
    clearTimeout(timer);
    timer = setTimeout(save, 400);
  };

  root.addEventListener("change", (event) => {
    if (event.target === all) boxes.forEach((box) => (box.checked = all.checked));
    if (event.target === all || event.target.matches("[data-line]")) changed();
  });
  if (all) all.checked = boxes.length > 0 && boxes.every((box) => box.checked);
}

document.querySelector("[data-discard]")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (!window.confirm(button.dataset.confirm)) return;
  try {
    await api(button.dataset.discard, { method: "DELETE" });
    window.location.assign(window.location.pathname);
  } catch (failure) {
    toast(failure instanceof ApiError ? failure.message : strings().network, { kind: "error" });
  }
});
