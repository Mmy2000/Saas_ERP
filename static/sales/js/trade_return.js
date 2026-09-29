// Wholesale return form on the trade sale page: send the ticked lines.
import { registerShaper } from "../../core/js/forms.js";

registerShaper("trade-return", (values, form) => ({
  sale: Number(form.dataset.sale),
  lines: [...form.querySelectorAll("[data-return-line]:checked")].map((box) => Number(box.dataset.returnLine)),
  reason: values.reason ?? "",
}));
