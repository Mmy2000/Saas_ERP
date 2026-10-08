// Stone parcels: the new / edit form, buying into and selling from a parcel, cancelling a deal.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { strings, toast } from "../../core/js/ui.js";

registerShaper("parcel", (values) => ({
  code: values.code || "", branch: Number(values.branch), kind: values.kind, shape: values.shape || "",
  size: values.size || "", color: values.color || "", clarity: values.clarity || "", note: values.note || "",
  sale_price_per_ct: values.sale_price_per_ct || null, is_active: Boolean(values.is_active),
}));

registerShaper("parcel-deal", (values, form) => {
  const method = values.payment;
  const pick = (selector) => Number(form.querySelector(selector)?.value) || null;
  return {
    parcel: Number(values.parcel), kind: values.kind, carats: values.carats || "", count: Number(values.count) || 0,
    price_per_ct: values.price_per_ct || "", payment: method, party: Number(values.party) || null,
    party_name: values.party_name || "", note: values.note || "",
    cash_box: method === "cash" ? pick("[data-box]") : null,
    bank_account: method === "bank_transfer" ? pick("[data-bank]") : null,
  };
});

// Which cash box / bank account the money goes through, per deal form.
document.querySelectorAll("[data-deal]").forEach((card) => {
  const sync = () => {
    const method = card.querySelector("[data-deal-method]").value;
    card.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
  };
  card.querySelector("[data-deal-method]").addEventListener("change", sync);
  sync();
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-void-deal]");
  if (!button || !window.confirm(button.dataset.confirm)) return;
  try {
    await api(button.dataset.voidDeal, { method: "POST", idempotencyKey: uuid() });
    window.location.reload();
  } catch (error) {
    toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
  }
});

const form = document.getElementById("parcel-form");
if (form) {
  const title = form.querySelector("[data-form-title]");
  const fresh = form.querySelector("[data-new-parcel]");
  const set = (name, value) => {
    const field = form.elements[name];
    if (field.tomselect) field.tomselect.setValue(value ?? "", true);
    else field.value = value ?? "";
  };
  const fill = (endpoint, method, data = {}) => {
    form.dataset.endpoint = endpoint;
    form.dataset.method = method;
    for (const name of ["code", "size", "color", "clarity", "note"]) set(name, data[name] || "");
    set("sale_price_per_ct", data.sale || "");
    set("kind", data.kind || "diamond");
    set("shape", data.shape || "");
    if (data.branch) set("branch", data.branch);
    form.elements.is_active.checked = method === "POST" || Boolean(data.active);
    title.textContent = method === "PUT" ? title.dataset.edit : title.dataset.new;
    fresh.classList.toggle("hidden", method !== "PUT");
  };
  document.querySelectorAll("[data-edit-parcel]").forEach((button) => button.addEventListener("click", () => {
    fill(button.dataset.editParcel, "PUT", button.dataset);
    form.elements.code.focus();
  }));
  fresh.addEventListener("click", () => fill(form.dataset.createUrl, "POST"));
}
