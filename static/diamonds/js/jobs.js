// Setters: hand pieces and stones over (scan list), record what came back set or unused, and
// take stones out of a piece again.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { startNavigation } from "../../core/js/loading.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const checked = (form, selector) => [...form.querySelectorAll(`${selector}:checked`)].map((input) => Number(input.value));

registerShaper("job-set", (values, form) => {
  const mounting = form.querySelector('input[name="mounting"]:checked');
  const gold = mounting?.dataset.family === "gold";
  return {
    mounting: mounting ? Number(mounting.value) : 0, stones: checked(form, "[data-job-stone]"),
    gross_after_g: values.gross_after_g || "", labour_amount: values.labour_amount || "0",
    label_price: values.label_price || "", category: gold ? Number(values.category) || null : null,
    note: values.note || "",
  };
});
registerShaper("job-return", (_values, form) => ({ lines: checked(form, "[data-job-return]") }));
registerShaper("stone-removal", (values, form) => ({
  stones: checked(form, "[data-remove-stone]"), gross_after_g: values.gross_after_g || "",
  fee_amount: values.fee_amount || "0", setter: Number(values.setter) || null, note: values.note || "",
}));

// A gold mounting becomes a diamond piece: its new category is asked for only then.
const setForm = document.querySelector('form[data-shape="job-set"]');
if (setForm) {
  const sync = () => {
    const mounting = setForm.querySelector('input[name="mounting"]:checked');
    setForm.querySelector("[data-for-gold]").classList.toggle("hidden", mounting?.dataset.family !== "gold");
  };
  setForm.addEventListener("change", (event) => {
    if (event.target.name === "mounting") {
      sync();
      setForm.querySelector("[data-after]").value = "";
    }
  });
  sync();
}

// --- handing over --------------------------------------------------------------------------------

const root = document.getElementById("job-new");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const items = [];
  const key = uuid();
  const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

  const render = () => {
    $("[data-items]").replaceChildren(...items.map((item, index) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td><span class="num font-medium text-slate-900"></span></td><td></td><td></td>
        <td class="text-end"><span class="num"></span></td><td class="text-xs text-slate-600" dir="ltr"></td>
        <td class="text-end"><button type="button" class="btn btn-ghost text-slate-400" aria-label="×">×</button></td>`;
      row.children[0].firstElementChild.textContent = item.barcode;
      row.children[1].textContent = item.category;
      row.children[2].textContent = item.karat || "—";
      row.children[3].firstElementChild.textContent = formatNumber(item.gross_weight_g, 3);
      row.children[4].textContent = item.stones || "—";
      row.querySelector("button").addEventListener("click", () => { items.splice(index, 1); render(); });
      return row;
    }));
    $("[data-items-empty]").classList.toggle("hidden", items.length > 0);
    $("[data-count]").textContent = items.length;
    $("[data-carats]").textContent = formatNumber(items.reduce((sum, item) => sum + Number(item.stone_weight_ct || 0), 0), 3);
  };

  $("[data-scan]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = event.currentTarget.querySelector("input");
    const term = input.value.trim();
    if (!term) return;
    try {
      const branch = $("[data-branch]").value;
      const found = await api(`${root.dataset.lookupUrl}?family=job&branch=${branch}&q=${encodeURIComponent(term)}`);
      const item = found.results[0];
      if (!items.some((existing) => existing.id === item.id)) items.push(item);
      input.value = "";
      render();
    } catch (error) {
      fail(error);
    }
    input.focus();
  });

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    root.querySelectorAll("[data-error-for]").forEach((node) => (node.textContent = ""));
    $("[data-error]").textContent = "";
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, {
        method: "POST", idempotencyKey: key,
        body: { branch: Number($("[data-branch]").value), setter: Number($("[data-setter]").value) || null,
                items: items.map((item) => item.id), note: $("[data-note]").value.trim() },
      });
      flash(root.dataset.saved);
      const next = root.dataset.detailUrl.replace("{id}", made.id);
      startNavigation(next);
      window.location.assign(next);
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [name, problems] of Object.entries(error.fields || {})) {
          const node = root.querySelector(`[data-error-for="${name}"]`);
          if (node) node.textContent = [].concat(problems).join(" ");
        }
        $("[data-error]").textContent = error.message;
      } else fail(error);
      button.disabled = false;
    }
  });
  render();
}
