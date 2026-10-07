// The offer form: its fields plus the pieces it covers (lists of ids and pasted barcodes).
import { registerShaper } from "../../core/js/forms.js";
import { readFilters } from "./prices.js";

registerShaper("offer", (values, form) => {
  const filters = readFilters(form.querySelector("[data-offer-filters]"));
  return {
    name: values.name || "", kind: values.kind, value: values.value || "0",
    starts_on: values.starts_on || null, ends_on: values.ends_on || null,
    is_active: Boolean(values.is_active), note: values.note || "",
    branch_ids: filters.branch_ids, category_ids: filters.category_ids,
    karat_ids: filters.karat_ids, supplier_ids: filters.supplier_ids, barcodes: filters.barcodes,
  };
});
