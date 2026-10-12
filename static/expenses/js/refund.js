// Refunding all or part of an expense into a cash box or bank account of its branch.
import { registerShaper } from "../../core/js/forms.js";
import { initPaymentBlocks, payment } from "../../treasury/js/payment_block.js";

initPaymentBlocks();

registerShaper("expense-refund", (_values, form) => {
  const block = form.querySelector("[data-payment]");
  const chosen = payment(block);
  return {
    amount: chosen.amount, method: chosen.kind, cash_box: chosen.cash_box,
    bank_account: chosen.bank_account, note: block.querySelector("[data-note]").value.trim(),
  };
});
