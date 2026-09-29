// Declarative JSON forms: <form data-api-form data-endpoint data-method [data-redirect]
// [data-success] [data-shape]>. Values are sent as strings (the server parses decimals, never
// the browser). Field errors from the envelope land in [data-error-for="<field>"].
//
// Empty inputs: data-empty="omit" leaves the field out, "null" sends null, default sends "".

import { api, ApiError } from "./api.js";
import { flash, strings, toast } from "./ui.js";

const shapers = new Map();

/** Register a function turning the flat form values into the request body. */
export function registerShaper(name, fn) {
  shapers.set(name, fn);
}

function collect(form) {
  const values = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled || el.type === "submit" || el.type === "button") continue;
    if (el.type === "checkbox") {
      values[el.name] = el.checked;
      continue;
    }
    if (el.type === "radio") {
      if (el.checked) values[el.name] = el.value;
      continue;
    }
    const value = el.value.trim();
    if (value !== "") values[el.name] = value;
    else if (el.dataset.empty === "null") values[el.name] = null;
    else if (el.dataset.empty !== "omit") values[el.name] = "";
  }
  return values;
}

function flatten(fields, prefix = "", out = {}) {
  for (const [key, value] of Object.entries(fields || {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (Array.isArray(value) && value.every((v) => typeof v === "string")) out[path] = value.join(" ");
    else if (value && typeof value === "object") flatten(value, path, out);
    else out[path] = String(value);
  }
  return out;
}

function clearErrors(form) {
  form.querySelectorAll("[data-error-for]").forEach((node) => (node.textContent = ""));
  form.querySelectorAll("[aria-invalid]").forEach((node) => node.removeAttribute("aria-invalid"));
  const banner = form.querySelector("[data-form-error]");
  if (banner) banner.classList.add("hidden");
}

function showErrors(form, error) {
  const ui = strings();
  const fields = flatten(error.fields);
  let first = null;
  const unplaced = [];
  for (const [path, message] of Object.entries(fields)) {
    // "lines.1.account" falls back to the closest slot: "lines.1", then "lines".
    let slot = null;
    for (let parts = path.split("."); parts.length && !slot; parts.pop()) {
      slot = form.querySelector(`[data-error-for="${CSS.escape(parts.join("."))}"]`);
    }
    if (!slot) {
      if (message !== error.message) unplaced.push(message); // no repeats in the banner
      continue;
    }
    slot.textContent = slot.textContent ? `${slot.textContent} ${message}` : message;
    const input = slot.parentElement.querySelector("input, select, textarea");
    if (input) {
      input.setAttribute("aria-invalid", "true");
      first ??= input;
    }
  }
  const banner = form.querySelector("[data-form-error]");
  const summary = [error.message || ui.error, ...unplaced].filter(Boolean).join(" ");
  if (banner) {
    banner.textContent = summary;
    banner.classList.remove("hidden");
  } else {
    toast(summary, { kind: "error" });
  }
  if (first?.tomselect) first.tomselect.focus();
  else (first || banner)?.focus?.();
}

async function submit(form) {
  const ui = strings();
  const button = form.querySelector('[type="submit"]');
  const values = collect(form);
  const shaper = shapers.get(form.dataset.shape);
  const body = shaper ? shaper(values, form) : values;

  clearErrors(form);
  button?.setAttribute("disabled", "");
  try {
    const data = await api(form.dataset.endpoint, { method: form.dataset.method || "POST", body });
    const redirect = form.dataset.redirect;
    if (redirect) {
      flash(form.dataset.success || "");
      window.location.assign(redirect.replace("{id}", data && data.id));
    } else {
      flash(form.dataset.success || "");
      window.location.reload();
    }
  } catch (error) {
    if (error instanceof ApiError) showErrors(form, error);
    else toast(ui.network, { kind: "error" });
    button?.removeAttribute("disabled");
  }
}

export function initForms() {
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-api-form]");
    if (!form) return;
    event.preventDefault();
    submit(form);
  });
}
