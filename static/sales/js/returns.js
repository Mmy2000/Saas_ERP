// Customer return form on the receipt page: send the ticked lines.
import { registerShaper } from "../../core/js/forms.js";

registerShaper("sales-return", (values, form) => ({
  invoice: Number(form.dataset.invoice),
  lines: [...form.querySelectorAll("[data-return-line]:checked")].map((box) => Number(box.dataset.returnLine)),
  refund_method: values.refund_method,
  deduction_amount: values.deduction_amount || "0",
  reason: values.reason ?? "",
}));
