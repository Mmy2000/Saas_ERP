// Stones at workshops: the hand out / take back form (with what the parcel or the workshop has),
// the row buttons that fill it, and cancelling.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { strings, toast } from "../../core/js/ui.js";

registerShaper("handout", (values) => ({
  kind: values.kind, workshop: Number(values.workshop) || 0, parcel: Number(values.parcel) || 0,
  carats: values.carats || "", count: Number(values.count) || 0, note: values.note || "",
}));

const held = JSON.parse(document.getElementById("held-stones")?.textContent || "{}");
const form = document.getElementById("handout-form");

function setValue(select, value) {
  if (select.tomselect) select.tomselect.setValue(value, true);
  else select.value = value;
}

function sync() {
  const kind = form.elements.kind.value;
  const help = form.querySelector("[data-kind-help]");
  help.textContent = help.dataset[kind] || "";
  const hint = form.querySelector("[data-handout-hint]");
  const parcel = form.elements.parcel;
  const option = parcel.selectedOptions[0];
  let text = "";
  if (option && option.value) {
    if (kind === "out") {
      text = hint.dataset.inParcel.replace("{carats}", option.dataset.carats).replace("{count}", option.dataset.count);
    } else {
      const [carats, count] = held[`${form.elements.workshop.value}:${option.value}`] || ["0", 0];
      text = hint.dataset.withWorkshop.replace("{carats}", carats).replace("{count}", count);
    }
  }
  hint.textContent = text;
}

if (form) {
  for (const name of ["kind", "workshop", "parcel"]) form.elements[name].addEventListener("change", sync);
  sync();
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-prefill]");
    if (!button) return;
    setValue(form.elements.kind, button.dataset.prefill);
    setValue(form.elements.workshop, button.dataset.workshop);
    setValue(form.elements.parcel, button.dataset.parcel);
    const [carats, count] = held[`${button.dataset.workshop}:${button.dataset.parcel}`] || ["", 0];
    form.elements.carats.value = carats;
    form.elements.count.value = count || "";
    sync();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    form.elements.carats.focus({ preventScroll: true });
  });
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-void-handout]");
  if (!button || !window.confirm(button.dataset.confirm)) return;
  try {
    await api(button.dataset.voidHandout, { method: "POST", idempotencyKey: uuid() });
    window.location.reload();
  } catch (error) {
    toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
  }
});
