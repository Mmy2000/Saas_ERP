// Every <select> becomes searchable (Tom Select, loaded as window.TomSelect before this module).
//
//   <select>                         searchable list of its own options
//   <select data-native>             left as a plain browser select
//   <select data-remote="/api/v1/parties/customers/" data-label-field="name">
//                                    options come from the API (?q=… search, first page)
//
// Selects with an empty option get a clear button. The original <select> keeps its value, so
// forms (including data-api-form) read it as before.
//
// filterOptions(select, keep) narrows a local select to the options `keep(option)` accepts
// (e.g. cash boxes of the chosen branch and currency); the empty option always stays.

import { api } from "./api.js";
import { strings } from "./ui.js";

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function remoteOptions(select) {
  const url = select.dataset.remote;
  const labelField = select.dataset.labelField || "name";
  return {
    valueField: "id",
    labelField,
    searchField: [labelField],
    preload: "focus",
    shouldLoad: () => true,
    load(query, callback) {
      const separator = url.includes("?") ? "&" : "?";
      api(`${url}${separator}q=${encodeURIComponent(query)}&page_size=20`)
        .then((data) => callback(data.results || data))
        .catch(() => callback());
    },
  };
}

export function enhanceSelects(root = document) {
  const TomSelect = window.TomSelect;
  if (!TomSelect) return;
  const ui = strings();

  root.querySelectorAll("select:not([data-native]):not(.tomselected)").forEach((select) => {
    const nullable = Boolean(select.querySelector('option[value=""]')) || select.dataset.remote;
    const plugins = select.multiple ? { remove_button: { title: ui.clear || "×" } } : { dropdown_input: {} };
    if (nullable && !select.multiple) plugins.clear_button = { title: ui.clear || "×", html: (data) => `<div class="${data.className}" title="${escapeHtml(data.title)}">×</div>` };

    const options = {
      plugins,
      allowEmptyOption: true,
      maxOptions: null,
      create: false,
      refreshThrottle: select.dataset.remote ? 300 : 0, // local lists filter as you type
      dropdownParent: "body",
      placeholder: select.dataset.placeholder || "",
      render: {
        no_results: () => `<div class="no-results">${escapeHtml(ui.no_results || "No results")}</div>`,
        loading: () => `<div class="loading">${escapeHtml(ui.loading || "…")}</div>`,
      },
      onInitialize() {
        const input = this.dropdown.querySelector(".dropdown-input");
        if (input) input.placeholder = ui.search || "";
      },
      ...(select.dataset.remote ? remoteOptions(select) : {}),
    };
    const control = new TomSelect(select, options);
    // Mirror server-side validation state (forms.js sets aria-invalid on the <select>).
    new MutationObserver(() => {
      if (select.getAttribute("aria-invalid") === "true") control.wrapper.setAttribute("aria-invalid", "true");
      else control.wrapper.removeAttribute("aria-invalid");
    }).observe(select, { attributes: true, attributeFilter: ["aria-invalid"] });
  });
}

export function filterOptions(select, keep) {
  if (!select._allOptions) {
    select._allOptions = [...select.querySelectorAll("option")].map((node) => ({ node, parent: node.parentElement }));
  }
  const value = select.value;
  select._allOptions.forEach(({ node }) => node.remove());
  select._allOptions.forEach(({ node, parent }) => {
    if (node.value === "" || keep(node)) parent.appendChild(node);
  });
  const stillThere = [...select.options].some((option) => option.value === value);
  select.value = stillThere ? value : (select.querySelector('option[value=""]') ? "" : select.options[0]?.value ?? "");
  const control = select.tomselect;
  if (control) {
    control.clearOptions();
    control.sync();
    control.setValue(select.value, true);
  }
  return select.options.length;
}
