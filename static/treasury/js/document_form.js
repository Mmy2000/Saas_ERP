// Treasury movement form: show the fields for the chosen kind, narrow "To" to what can receive
// from "From", and send only what applies. Amount hints are display-only; the server computes.
import { registerShaper } from "../../core/js/forms.js";
import { filterOptions } from "../../core/js/selects.js";
import { formatNumber } from "../../core/js/money.js";

const form = document.querySelector('form[data-shape="treasury-document"]');

function selected(select) {
  return select.selectedOptions[0] || null;
}

function holder(value) {
  const [type, id] = (value || "").split(":");
  return type && id ? { type, id: Number(id) } : null;
}

if (form) {
  const $ = (selector) => form.querySelector(selector);
  const kindSelect = $("[data-kind]");
  const source = $("[data-source]");
  const dest = $("[data-dest]");
  const terminal = $("[data-terminal]");

  const refresh = () => {
    const kind = kindSelect.value;
    form.querySelectorAll("[data-for]").forEach((node) => {
      const shown = node.dataset.for.split(" ").includes(kind);
      node.classList.toggle("hidden", !shown);
      // Only the visible "source" error slot receives errors.
      node.querySelectorAll("[data-error-for], [data-error-slot]").forEach((slot) => {
        if (slot.dataset.errorFor === "source" || slot.dataset.errorSlot === "source") {
          if (shown) { slot.dataset.errorFor = "source"; delete slot.dataset.errorSlot; }
          else { slot.dataset.errorSlot = "source"; delete slot.dataset.errorFor; }
        }
      });
    });

    if (kind === "card_settlement") {
      const option = selected(terminal);
      $("[data-branch-field]").classList.toggle("hidden", !option || Boolean(option.dataset.hasBranch));
      $("[data-currency-label]").textContent = option ? `(${option.dataset.currency})` : "";
      const amount = Number($("#f-amount").value || 0);
      const fee = $("#f-fee");
      const rate = Number(option?.dataset.feeRate || 0);
      fee.placeholder = amount ? formatNumber((amount * rate).toFixed(2)) : "";
      const feeValue = fee.value.trim() ? Number(fee.value) : Number((amount * rate).toFixed(2));
      $("[data-net]").textContent = amount ? formatNumber((amount - feeValue).toFixed(2)) : "—";
      return;
    }

    // Exchange: from a cash box only; to a box of the same branch in another currency.
    // Transfer: to anything in the same currency.
    filterOptions(source, (option) => kind !== "exchange" || option.value.startsWith("box:"));
    const from = selected(source);
    const fromValue = source.value;
    filterOptions(dest, (option) => {
      if (!from || !fromValue) return true;
      if (option.value === fromValue) return false;
      if (kind === "exchange") {
        return option.value.startsWith("box:") && option.dataset.branch === from.dataset.branch
          && option.dataset.currency !== from.dataset.currency;
      }
      return option.dataset.currency === from.dataset.currency;
    });
    const to = selected(dest);
    const bothBanks = source.value.startsWith("bank:") && dest.value.startsWith("bank:");
    $("[data-branch-field]").classList.toggle("hidden", kind !== "transfer" || !bothBanks);
    $("[data-transit-hint]").classList.toggle("hidden", !(kind === "transfer" && from?.dataset.branch
      && to?.dataset.branch && from.dataset.branch !== to.dataset.branch));
    $("[data-currency-label]").textContent = from?.dataset.currency ? `(${from.dataset.currency})` : "";
    $("[data-dest-currency-label]").textContent = to?.dataset.currency ? `(${to.dataset.currency})` : "";
    const sold = Number($("#f-amount").value || 0);
    const bought = Number($("#f-dest-amount").value || 0);
    $("[data-rate]").textContent = sold && bought ? formatNumber((bought / sold).toFixed(4), 4) : "—";
  };

  // Settling a terminal: start from everything it has not been paid for yet.
  const prefill = () => {
    const option = selected(terminal);
    if (kindSelect.value === "card_settlement" && option && Number(option.dataset.unsettled) > 0) {
      $("#f-amount").value = option.dataset.unsettled;
    }
  };
  terminal.addEventListener("change", prefill);
  prefill();
  form.addEventListener("change", refresh);
  form.addEventListener("input", (event) => {
    if (event.target.matches("#f-amount, #f-dest-amount, #f-fee")) refresh();
  });
  refresh();
}

registerShaper("treasury-document", (values, formElement) => {
  const kind = values.kind;
  const body = { kind, amount: values.amount ?? "", reference: values.reference ?? "", note: values.note ?? "" };
  if (!formElement.querySelector("[data-branch-field]").classList.contains("hidden")) body.branch = Number(values.branch);
  if (kind === "card_settlement") {
    const terminal = formElement.querySelector("[data-terminal]").value;
    body.source_terminal = terminal ? Number(terminal) : null;
    body.fee_amount = values.fee_amount ?? "";
    return body;
  }
  const from = holder(formElement.querySelector("[data-source]").value);
  const to = holder(formElement.querySelector("[data-dest]").value);
  if (from) body[from.type === "box" ? "source_box" : "source_bank"] = from.id;
  if (to) body[to.type === "box" ? "dest_box" : "dest_bank"] = to.id;
  if (kind === "exchange") body.dest_amount = values.dest_amount ?? "";
  return body;
});
