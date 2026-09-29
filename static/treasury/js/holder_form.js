// Cash box / bank account / card terminal form: branch scope toggle, fee as a percentage.
import { registerShaper } from "../../core/js/forms.js";
import { percentToRate, rateToPercent } from "../../core/js/money.js";

const form = document.querySelector('form[data-shape="treasury-holder"]');

if (form) {
  const fee = form.querySelector("[data-percent]");
  if (fee) fee.value = rateToPercent(fee.value);
  const list = form.querySelector("[data-branch-list]");
  const refresh = () => {
    if (list) list.classList.toggle("hidden", form.querySelector("[data-scope]:checked").value !== "some");
  };
  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-scope]")) refresh();
  });
  refresh();
}

registerShaper("treasury-holder", (values, formElement) => {
  const kind = formElement.dataset.kind;
  if (kind === "box") {
    const body = { name: values.name ?? "", is_default: Boolean(values.is_default) };
    if (values.branch) body.branch = Number(values.branch);
    if (values.currency) body.currency = values.currency;
    return body;
  }
  if (kind === "bank") {
    const some = values.scope === "some";
    const branches = [...formElement.querySelector("[data-branches]").selectedOptions].map((o) => Number(o.value));
    return { name: values.name ?? "", currency: values.currency ?? "", bank_name: values.bank_name ?? "",
             account_number: values.account_number ?? "", iban: values.iban ?? "",
             branches: some ? branches : null };
  }
  return { name: values.name ?? "", bank_account: values.bank_account ? Number(values.bank_account) : null,
           branch: values.branch ? Number(values.branch) : null,
           fee_rate: values.fee_rate ? percentToRate(values.fee_rate) : "0" };
});
