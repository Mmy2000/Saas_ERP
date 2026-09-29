// Builds the publish-board request from the gold price form. Only entered prices are sent;
// the server derives the other karats (ADR-010: no pricing arithmetic in the browser).
import { registerShaper } from "../../core/js/forms.js";

registerShaper("price-board", (values, form) => {
  const reference = { karat: values.reference_karat, sell_price_per_g: values.sell ?? "" };
  if (values.buy) reference.buy_price_per_g = values.buy;
  if (values.scrap) reference.scrap_buy_price_per_g = values.scrap;

  const overrides = [];
  form.querySelectorAll('input[name="silver"]').forEach((input) => {
    if (input.value.trim()) overrides.push({ karat: input.dataset.karat, sell_price_per_g: input.value.trim() });
  });

  const body = { reference, overrides, note: values.note ?? "" };
  if (values.effective_at) body.effective_at = values.effective_at;
  return body;
});
