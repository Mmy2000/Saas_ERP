// Discount classes: one form for a new class or the one being edited.
import { registerShaper } from "../../core/js/forms.js";

registerShaper("discount-class", (values) => ({
  code: values.code || "", name: values.name || "", usual_percent: values.usual_percent || "0",
  max_percent: values.max_percent || "0", is_active: Boolean(values.is_active),
}));

const form = document.getElementById("class-form");
if (form) {
  const title = form.querySelector("[data-form-title]");
  const fresh = form.querySelector("[data-new-class]");
  const fill = ({ endpoint, method, code = "", name = "", usual = "", max = "", active = true }) => {
    form.dataset.endpoint = endpoint;
    form.dataset.method = method;
    form.elements.code.value = code;
    form.elements.name.value = name;
    form.elements.usual_percent.value = usual;
    form.elements.max_percent.value = max;
    form.elements.is_active.checked = active;
    title.textContent = method === "PUT" ? title.dataset.edit : title.dataset.new;
    fresh.classList.toggle("hidden", method !== "PUT");
  };
  document.querySelectorAll("[data-edit-class]").forEach((button) => button.addEventListener("click", () => {
    const { editClass, code, name, usual, max, active } = button.dataset;
    fill({ endpoint: editClass, method: "PUT", code, name, usual, max, active: Boolean(active) });
    form.elements.name.focus();
  }));
  fresh.addEventListener("click", () => fill({ endpoint: form.dataset.createUrl, method: "POST" }));
}
