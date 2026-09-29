// Label template form: sections follow the chosen media/layout, fields keep the order they were
// picked in (that is the print order), and the preview redraws from the unsaved values.
import { registerShaper } from "../../core/js/forms.js";

const form = document.querySelector('form[data-shape="label-template"]');

function chosen(select) {
  return select.tomselect ? [...select.tomselect.items] : [...select.selectedOptions].map((o) => o.value);
}

function values(formElement) {
  const data = {};
  formElement.querySelectorAll("input[name], select[name]").forEach((el) => {
    if (el.type === "checkbox") data[el.name] = el.checked;
    else data[el.name] = el.value.trim();
  });
  formElement.querySelectorAll("[data-field-select]").forEach((select) => {
    data[select.dataset.fieldSelect] = chosen(select);
  });
  return data;
}

if (form) {
  const frame = form.querySelector("[data-preview-frame]");
  let timer = null;

  const refresh = () => {
    const data = values(form);
    form.querySelectorAll("[data-when-layout]").forEach((node) => node.classList.toggle("hidden", node.dataset.whenLayout !== data.layout));
    form.querySelectorAll("[data-when-media]").forEach((node) => node.classList.toggle("hidden", node.dataset.whenMedia !== data.media));
    clearTimeout(timer);
    timer = setTimeout(() => {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(data)) {
        if (Array.isArray(value)) value.forEach((v) => query.append(key, v));
        else if (typeof value !== "boolean") query.append(key, value);
      }
      frame.src = `${form.dataset.preview}?${query}`;
    }, 250);
  };

  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  // Tom Select initialises after this module; redraw once its selections exist.
  window.addEventListener("load", refresh);
  refresh();
}

registerShaper("label-template", (_values, formElement) => values(formElement));
