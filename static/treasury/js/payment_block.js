// A payment block ([data-payment]): method, currency, amount, and the box / bank account /
// terminal it goes through (templates/treasury/holder_selects.html). Used by reservations and
// repairs.
import { narrowHolders } from "./holders.js";

function branchOf() {
  const select = document.querySelector("select[data-branch]");
  return select ? select.value : document.querySelector("[data-branch][data-value]")?.dataset.value;
}

export function syncPayment(block) {
  const method = block.querySelector("[data-method]")?.value || "cash";
  block.querySelectorAll("[data-holder]").forEach((node) => node.classList.toggle("hidden", node.dataset.holder !== method));
  narrowHolders({ branch: branchOf(), currency: block.querySelector("[data-currency]")?.value,
                  box: block.querySelector("[data-box]"), bank: block.querySelector("[data-bank]"),
                  terminal: block.querySelector("[data-terminal]") });
  const onAccount = block.querySelector("[data-on-account]");
  if (onAccount) block.querySelector("[data-pay-now]")?.classList.toggle("hidden", onAccount.checked);
}

/** The block as the API's payment shape. */
export function payment(block) {
  const method = block.querySelector("[data-method]").value;
  const pick = (selector) => {
    const value = block.querySelector(selector)?.value;
    return value ? Number(value) : null;
  };
  return {
    kind: method, currency: block.querySelector("[data-currency]").value,
    amount: block.querySelector("[data-amount]")?.value.trim() || "",
    cash_box: method === "cash" ? pick("[data-box]") : null,
    bank_account: method === "bank_transfer" ? pick("[data-bank]") : null,
    terminal: method === "card" ? pick("[data-terminal]") : null,
  };
}

/** Keep every payment block's holder choices in step with its method, currency and branch. */
export function initPaymentBlocks() {
  document.querySelectorAll("[data-payment]").forEach((block) => {
    block.addEventListener("change", (event) => {
      if (event.target.matches("[data-method], [data-currency], [data-on-account]")) syncPayment(block);
    });
    syncPayment(block);
  });
  document.querySelector("select[data-branch]")?.addEventListener("change", () =>
    document.querySelectorAll("[data-payment]").forEach(syncPayment));
}
