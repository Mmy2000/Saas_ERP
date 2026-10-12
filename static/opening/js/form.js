// Opening balances: rows of party balances, cash/bank balances, balances with other branches and
// stock by weight, sent as one
// document. Field errors come back as parties.<n>.<field>, so each row's error slots are named
// after its position.
import { registerShaper } from "../../core/js/forms.js";
import { enhanceSelects } from "../../core/js/selects.js";

const form = document.getElementById("opening");

if (form) {
  const LISTS = { party: "parties", holder: "holders", branch: "branches", stock: "stock" };
  const partiesUrl = form.dataset.partiesUrl;

  const renumber = () => {
    for (const [kind, list] of Object.entries(LISTS)) {
      form.querySelectorAll(`[data-row="${kind}"]`).forEach((row, index) => {
        row.querySelectorAll("[data-e]").forEach((slot) => {
          slot.dataset.errorFor = `${list}.${index}.${slot.dataset.e}`;
        });
      });
    }
  };

  // A party row searches the names holding the chosen role (customers, suppliers…).
  const pointParty = (row) => {
    const role = row.querySelector('[data-f="role"]').value;
    const select = row.querySelector('[data-f="party"]');
    select.dataset.remote = `${partiesUrl}?role=${encodeURIComponent(role)}`;
    if (select.tomselect) {
      select.tomselect.clear();
      select.tomselect.clearOptions();
    }
  };

  const addRow = (kind) => {
    const row = document.getElementById(`row-${kind}`).content.firstElementChild.cloneNode(true);
    form.querySelector(`[data-rows="${kind}"]`).append(row);
    if (kind === "branch") {  // another branch than the one the balances are for
      const own = form.querySelector('[name="branch"]').value;
      const select = row.querySelector('[data-f="branch"]');
      const other = [...select.options].find((option) => option.value !== own);
      if (other) select.value = other.value;
    }
    if (kind === "party") {
      pointParty(row);
      row.querySelector('[data-f="role"]').addEventListener("change", () => pointParty(row));
    }
    enhanceSelects(row);
    renumber();
    row.querySelector("select, input")?.focus();
  };

  form.addEventListener("click", (event) => {
    const add = event.target.closest("[data-add]");
    if (add) addRow(add.dataset.add);
    const remove = event.target.closest("[data-remove]");
    if (remove) {
      remove.closest("[data-row]").remove();
      renumber();
    }
  });

  const value = (row, name) => row.querySelector(`[data-f="${name}"]`).value.trim();
  const rows = (kind) => [...form.querySelectorAll(`[data-row="${kind}"]`)];

  registerShaper("opening", (values) => ({
    branch: Number(values.branch),
    business_date: values.business_date || null,
    note: values.note || "",
    parties: rows("party").map((row) => {
      const [unit, id] = value(row, "unit").split(":");
      return {
        role: value(row, "role"), party: Number(value(row, "party")) || 0,
        amount: value(row, "amount"), owes_us: value(row, "owes_us") === "1",
        currency: unit === "cur" ? id : "", karat: unit === "karat" ? Number(id) : null,
      };
    }),
    holders: rows("holder").map((row) => {
      const [kind, id] = value(row, "holder").split(":");
      return { kind, holder: Number(id) || 0, amount: value(row, "amount") };
    }),
    branches: rows("branch").map((row) => {
      const [unit, id] = value(row, "unit").split(":");
      return {
        branch: Number(value(row, "branch")) || 0, amount: value(row, "amount"),
        owes_us: value(row, "owes_us") === "1",
        currency: unit === "cur" ? id : "", karat: unit === "karat" ? Number(id) : null,
      };
    }),
    stock: rows("stock").map((row) => ({
      category: Number(value(row, "category")) || 0, karat: Number(value(row, "karat")) || null,
      gross_weight_g: value(row, "gross_weight_g"), qty: Number(value(row, "qty")) || 0,
      cost_amount: value(row, "cost_amount") || "0",
    })),
  }));

  addRow("party");
}
