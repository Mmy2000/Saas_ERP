// Expense form: pick the cash box or bank account usable at the branch in the currency.
import { registerShaper } from "../../core/js/forms.js";
import { narrowHolders } from "../../treasury/js/holders.js";

const form = document.querySelector('form[data-shape="expense"]');

if (form) {
  const $ = (selector) => form.querySelector(selector);
  const refresh = () => {
    const method = $("[data-method-select]").value;
    form.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
    narrowHolders({ branch: $("[data-branch]").value, currency: $("[data-currency]").value, box: $("[data-box]"), bank: $("[data-bank]") });
  };
  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-method-select], [data-branch], [data-currency]")) refresh();
  });
  refresh();
}

registerShaper("expense", (values, formElement) => {
  const box = formElement.querySelector("[data-box]").value;
  const bank = formElement.querySelector("[data-bank]").value;
  return {
    branch: Number(values.branch), category: values.category ? Number(values.category) : null,
    method: values.method, currency: values.currency, amount: values.amount ?? "",
    cash_box: values.method === "cash" && box ? Number(box) : null,
    bank_account: values.method === "bank_transfer" && bank ? Number(bank) : null,
    payee: values.payee ?? "", reference: values.reference ?? "", note: values.note ?? "",
  };
});
