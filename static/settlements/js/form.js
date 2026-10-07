// Settlement form: show the fields for the chosen kind and side; send only what applies.
// Transfers and adjustments reuse the money fields (without how it was paid) or the gold ones.
import { registerShaper } from "../../core/js/forms.js";
import { narrowHolders } from "../../treasury/js/holders.js";

const form = document.querySelector('form[data-shape="settlement"]');
const GROUPS = { receipt: "money", payment: "money", metal_in: "metal", metal_out: "metal", conversion: "conversion" };
const BALANCE_KINDS = ["transfer", "adjustment"];

const side = () => form.querySelector("[data-side]:checked").value;
const counterSide = () => form.querySelector("[data-counter-side]:checked")?.value || "";
const kind = () => form.querySelector("[data-kind]").value;
const asset = () => form.querySelector("[data-asset]:checked")?.value || "money";
const isBalance = () => BALANCE_KINDS.includes(kind());

function group() {
  if (isBalance()) return asset() === "gold" ? "metal" : "money";
  return GROUPS[kind()];
}

function refresh() {
  const current = kind();
  form.querySelectorAll("[data-group]").forEach((node) => node.classList.toggle("hidden", node.dataset.group !== group()));
  form.querySelectorAll("[data-only]").forEach((node) => {
    let shown = node.dataset.only.split(" ").includes(current);
    if (shown && node.dataset.counterFor) shown = node.dataset.counterFor === counterSide();
    node.classList.toggle("hidden", !shown);
  });
  form.querySelectorAll("[data-party-for]").forEach((node) => node.classList.toggle("hidden", node.dataset.partyFor !== side()));
  form.querySelectorAll("[data-payment-only]").forEach((node) => node.classList.toggle("hidden", isBalance()));
  form.querySelector("[data-label-with]").classList.toggle("hidden", current === "transfer");
  form.querySelector("[data-label-from]").classList.toggle("hidden", current !== "transfer");
  // Which cash box / bank account / terminal the money goes through.
  const method = form.querySelector("#f-method").value;
  form.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", isBalance() || node.dataset.holder !== method));
  narrowHolders({ branch: form.querySelector("#f-branch").value, currency: form.querySelector("[data-currency]").value,
                  box: form.querySelector("[data-box]"), bank: form.querySelector("[data-bank]"),
                  terminal: form.querySelector("[data-terminal]") });
}

if (form) {
  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-kind], [data-side], [data-counter-side], [data-asset], #f-method, #f-branch, [data-currency]")) refresh();
  });
  refresh();
}

registerShaper("settlement", (values, formElement) => {
  const party = formElement.querySelector(`[data-party-for="${side()}"] [data-party]`).value;
  const body = { kind: values.kind, side: side(), party: party ? Number(party) : null, branch: Number(values.branch),
                 reference: values.reference ?? "", note: values.note ?? "" };
  const which = group();
  if (which === "money") {
    Object.assign(body, { currency: values.currency, amount: values.amount ?? "" });
    if (!isBalance()) {
      const pick = (selector) => (formElement.querySelector(selector).value ? Number(formElement.querySelector(selector).value) : null);
      Object.assign(body, { method: values.method,
                            cash_box: values.method === "cash" ? pick("[data-box]") : null,
                            bank_account: values.method === "bank_transfer" ? pick("[data-bank]") : null,
                            terminal: values.method === "card" ? pick("[data-terminal]") : null });
    }
  }
  if (which === "metal") Object.assign(body, { karat: Number(values.karat), gross_weight_g: values.gross_weight_g ?? "" });
  if (which === "conversion") {
    Object.assign(body, { direction: values.direction, fine_weight_g: values.fine_weight_g ?? "",
                          price_per_fine_g: values.price_per_fine_g ?? "",
                          currency: formElement.querySelector("[data-conversion-currency]").value });
  }
  if (isBalance()) body.asset = asset();
  if (values.kind === "transfer") {
    const counter = formElement.querySelector(`[data-counter-for="${counterSide()}"] [data-counter]`).value;
    Object.assign(body, { counter_side: counterSide(), counter_party: counter ? Number(counter) : null });
  }
  if (values.kind === "adjustment") body.adjustment = values.adjustment;
  return body;
});
