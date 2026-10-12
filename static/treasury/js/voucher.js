// Money in or out: pick the cash box or bank account usable at the branch in the currency; an
// account kept in one currency sets the currency.
import { registerShaper } from "../../core/js/forms.js";
import { narrowHolders } from "./holders.js";

const form = document.querySelector('form[data-shape="money-voucher"]');

if (form) {
  const $ = (selector) => form.querySelector(selector);
  const refresh = () => {
    const method = $("[data-method-select]").value;
    form.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
    narrowHolders({ branch: $("[data-branch]").value, currency: $("[data-currency]").value, box: $("[data-box]"), bank: $("[data-bank]") });
  };
  form.addEventListener("change", (event) => {
    if (event.target.matches('[name="account"]')) {
      const only = event.target.selectedOptions[0]?.dataset.currency;
      if (only) $("[data-currency]").value = only;
    }
    if (event.target.matches('[data-method-select], [data-branch], [data-currency], [name="account"]')) refresh();
  });
  refresh();
}

registerShaper("money-voucher", (values, formElement) => {
  const box = formElement.querySelector("[data-box]").value;
  const bank = formElement.querySelector("[data-bank]").value;
  return {
    branch: Number(values.branch), direction: values.direction,
    account: values.account ? Number(values.account) : null,
    method: values.method, currency: values.currency, amount: values.amount ?? "",
    cash_box: values.method === "cash" && box ? Number(box) : null,
    bank_account: values.method === "bank_transfer" && bank ? Number(bank) : null,
    counterparty: values.counterparty ?? "", reference: values.reference ?? "", note: values.note ?? "",
  };
});
