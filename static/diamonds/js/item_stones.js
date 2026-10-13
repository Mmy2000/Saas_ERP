// A diamond piece's stones on its page: add/remove rows, then save them with the label price.
import { registerShaper } from "../../core/js/forms.js";
import { enhanceSelects } from "../../core/js/selects.js";
import { applyCode } from "./stone_codes.js";

const form = document.querySelector('form[data-shape="item-stones"]');

if (form) {
  const rows = form.querySelector("[data-stones]");
  const template = form.querySelector("#stone-row");
  form.addEventListener("click", (event) => {
    if (event.target.closest("[data-add-stone]")) {
      const row = template.content.firstElementChild.cloneNode(true);
      rows.append(row);
      enhanceSelects(row);
    }
    else if (event.target.closest("[data-remove-stone]")) event.target.closest("[data-stone]").remove();
  });
  form.addEventListener("change", (event) => {
    if (event.target.matches('[data-s="code"]')) applyCode(event.target.closest("[data-stone]"));
  });
  registerShaper("item-stones", (values) => ({
    label_price: values.label_price || null,
    discount_class: Number(values.discount_class) || null,
    model_code: values.model_code ?? "",
    model_classes: [...(form.querySelector("[data-model-classes]")?.selectedOptions || [])].map((option) => option.value),
    stones: [...rows.querySelectorAll("[data-stone]")]
      .filter((row) => row.querySelector('[data-s="carat"]').value.trim())
      .map((row) => Object.fromEntries([...row.querySelectorAll("[data-s]")].map(
        (input) => [input.dataset.s, input.value.trim()]))),
  }));
}
