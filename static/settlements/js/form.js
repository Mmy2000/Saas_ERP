// Settlement form: show the fields for the chosen kind and side; send only what applies.
import { registerShaper } from "../../core/js/forms.js";
import { narrowHolders } from "../../treasury/js/holders.js";

const form = document.querySelector('form[data-shape="settlement"]');
const GROUPS = { receipt: "money", payment: "money", metal_in: "metal", metal_out: "metal", conversion: "conversion" };

function side() {
  return form.querySelector("[data-side]:checked").value;
}

function refresh() {
  const group = GROUPS[form.querySelector("[data-kind]").value];
  form.querySelectorAll("[data-group]").forEach((node) => node.classList.toggle("hidden", node.dataset.group !== group));
  form.querySelectorAll("[data-party-for]").forEach((node) => node.classList.toggle("hidden", node.dataset.partyFor !== side()));
  // Which cash box / bank account / terminal the money goes through.
  const method = form.querySelector("#f-method").value;
  form.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
  narrowHolders({ branch: form.querySelector("#f-branch").value, currency: form.querySelector("[data-currency]").value,
                  box: form.querySelector("[data-box]"), bank: form.querySelector("[data-bank]"),
                  terminal: form.querySelector("[data-terminal]") });
}

if (form) {
  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-kind], [data-side], #f-method, #f-branch, [data-currency]")) refresh();
  });
  refresh();
}

registerShaper("settlement", (values, formElement) => {
  const kind = values.kind;
  const party = formElement.querySelector(`[data-party-for="${side()}"] [data-party]`).value;
  const body = { kind, side: side(), party: party ? Number(party) : null, branch: Number(values.branch),
                 reference: values.reference ?? "", note: values.note ?? "" };
  const group = GROUPS[kind];
  if (group === "money") {
    const pick = (selector) => (formElement.querySelector(selector).value ? Number(formElement.querySelector(selector).value) : null);
    Object.assign(body, { method: values.method, currency: values.currency, amount: values.amount ?? "",
                          cash_box: values.method === "cash" ? pick("[data-box]") : null,
                          bank_account: values.method === "bank_transfer" ? pick("[data-bank]") : null,
                          terminal: values.method === "card" ? pick("[data-terminal]") : null });
  }
  if (group === "metal") Object.assign(body, { karat: Number(values.karat), gross_weight_g: values.gross_weight_g ?? "" });
  if (group === "conversion") {
    Object.assign(body, { direction: values.direction, fine_weight_g: values.fine_weight_g ?? "",
                          price_per_fine_g: values.price_per_fine_g ?? "",
                          currency: formElement.querySelector("[data-conversion-currency]").value });
  }
  return body;
});
