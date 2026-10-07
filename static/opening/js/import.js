// Import pieces from Excel: "Check the file" sends it with dry_run (every row checked exactly as
// posting would, nothing saved) and lists the problems by row; "Import" sends the same file for
// real. The file stays chosen in the input between the two.
import { uuid } from "../../core/js/api.js";
import { flash, strings } from "../../core/js/ui.js";

const form = document.getElementById("import");

if (form) {
  const $ = (selector) => form.querySelector(selector);
  const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]*)/) || [])[1] || "";
  let key = uuid();
  let checked = false;

  const reset = () => {
    checked = false;
    $("[data-go]").disabled = true;
    ["[data-error]", "[data-ok]", "[data-result]"].forEach((s) => $(s).classList.add("hidden"));
  };
  form.addEventListener("change", (event) => {
    if (event.target.matches("input, select")) {
      key = uuid();
      reset();
    }
  });

  const send = async (dryRun) => {
    const body = new FormData();
    for (const name of ["branch", "business_date", "note"]) body.append(name, form.elements[name].value);
    const file = form.elements.file.files[0];
    if (file) body.append("file", file);
    body.append("dry_run", dryRun ? "true" : "false");
    const response = await fetch(form.dataset.url, {
      method: "POST", body, credentials: "same-origin",
      headers: { "X-CSRFToken": decodeURIComponent(csrf()), "Idempotency-Key": dryRun ? uuid() : key, Accept: "application/json" },
    });
    const data = await response.json().catch(() => null);
    return { status: response.status, data };
  };

  const showError = (message) => {
    const box = $("[data-error]");
    box.textContent = message;
    box.classList.remove("hidden");
  };

  const table = (head, rows) => {
    $("[data-head]").innerHTML = `<tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>`;
    $("[data-body]").innerHTML = rows.map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
    $("[data-result]").classList.remove("hidden");
  };

  const run = async (dryRun) => {
    reset();
    if (!form.elements.file.files.length) {
      showError(form.elements.file.closest("label").textContent.trim());
      return;
    }
    const button = $(dryRun ? "[data-check]" : "[data-go]");
    button.disabled = true;
    $("[data-status]").textContent = dryRun ? form.dataset.checking : form.dataset.importing;
    try {
      const { status, data } = await send(dryRun);
      const error = data && data.error;
      if (status >= 400 || !data) {
        showError((error && ((error.fields && error.fields.file) || error.message)) || strings().network);
        return;
      }
      if (data.url || data.id) {
        flash(form.dataset.done);
        window.location.assign(form.dataset.detailUrl.replace("{id}", data.id));
        return;
      }
      const ui = form.dataset; // translated column labels (import.html)
      if (!data.ok) {
        showError(ui.problems.replace("{n}", data.problem_count));
        table([ui.lLine, ui.lColumn, ui.lProblem], data.problems.map((p) => [
          `<span class="num">${esc(p.line)}</span>`, esc(p.column), `<span class="text-red-700">${esc(p.message)}</span>`]));
        $("[data-more]").textContent = data.problem_count > data.problems.length
          ? ui.more.replace("{n}", data.problem_count - data.problems.length) : "";
        return;
      }
      const ok = $("[data-ok]");
      ok.textContent = form.dataset.clean.replace("{n}", data.rows);
      ok.classList.remove("hidden");
      table([ui.lKarat, ui.lPieces, ui.lWeight], data.by_karat.map((k) => [
        esc(k.karat), `<span class="num">${esc(k.pieces)}</span>`, `<span class="num">${esc(k.weight)}</span>`]));
      $("[data-more]").textContent = "";
      checked = true;
    } catch {
      showError(strings().network);
    } finally {
      $("[data-status]").textContent = "";
      $("[data-check]").disabled = false;
      $("[data-go]").disabled = !checked;
    }
  };

  $("[data-check]").addEventListener("click", () => run(true));
  $("[data-go]").addEventListener("click", () => run(false));
}
