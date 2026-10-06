// Cheques: who the cheque is with (customer, supplier, trader or workshop), and the bodies of
// the new-cheque and endorse forms.
import { registerShaper } from "../../core/js/forms.js";

// Each picker shows the account list of the side chosen in it.
document.querySelectorAll("[data-party-picker]").forEach((picker) => {
  const sync = () => {
    const side = picker.querySelector("[data-side]:checked").value;
    picker.querySelectorAll("[data-party-for]").forEach((node) => node.classList.toggle("hidden", node.dataset.partyFor !== side));
  };
  picker.addEventListener("change", (event) => { if (event.target.matches("[data-side]")) sync(); });
  sync();
});

function picked(form) {
  const picker = form.querySelector("[data-party-picker]");
  const side = picker.querySelector("[data-side]:checked").value;
  const party = picker.querySelector(`[data-party-for="${side}"] [data-party]`).value;
  return { side, party: party ? Number(party) : null };
}

registerShaper("cheque", (values, form) => ({
  direction: values.direction, ...picked(form), branch: Number(values.branch),
  cheque_number: values.cheque_number || "", due_date: values.due_date || "", amount: values.amount || "",
  drawn_on: values.drawn_on || "", bank_account: values.bank_account ? Number(values.bank_account) : null,
  note: values.note || "",
}));

registerShaper("cheque-endorse", (_values, form) => picked(form));
