// The customer's one-time code (apps/sales/codes.py). askForCode() sends a code to the
// customer's phone, waits for the seller to type what the customer reads out, and resolves
// { code: id } once it is right, { skip: true } when someone allowed sells without it, or
// null when the dialog is closed.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { strings } from "../../core/js/ui.js";

// A number inside a sentence of the other script keeps its own order (LRI ... PDI).
const isolate = (text) => `${String.fromCharCode(0x2066)}${text}${String.fromCharCode(0x2069)}`;

export function askForCode({ branch, amount, customer = null, phone = "", reservation = null, reason = "", canSkip = false }) {
  const dialog = document.querySelector("[data-code-dialog]");
  if (!dialog) return Promise.resolve(null);
  const $ = (selector) => dialog.querySelector(selector);
  const input = $("[data-code-input]");
  const status = $("[data-code-status]");
  const fieldError = $('[data-error-for="code"]');
  const showError = (message) => {
    $("[data-code-error]").textContent = message || "";
    $("[data-code-error]").classList.toggle("hidden", !message);
  };
  const url = (name, id) => dialog.dataset[name].replace("/0/", `/${id}/`);
  let current = null;

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      controller.abort();
      if (dialog.open) dialog.close();
      resolve(value);
    };
    const controller = new AbortController();
    const on = (selector, handler) => $(selector).addEventListener("click", handler, { signal: controller.signal });

    const sent = (code, message) => {
      current = code;
      status.textContent = message.replace("{phone}", isolate(code.phone));
      input.disabled = false;
      $("[data-code-confirm]").disabled = false;
      $("[data-code-resend]").disabled = code.sends_left <= 0;
      $("[data-code-skip]").classList.toggle("hidden", !code.can_skip);
      input.focus();
    };
    const failed = (error, keepSkip = true) => {
      showError(error instanceof ApiError ? error.message : strings().network);
      status.textContent = "";
      if (!keepSkip) $("[data-code-skip]").classList.add("hidden");
    };

    on("[data-code-confirm]", async () => {
      if (!current) return;
      fieldError.textContent = "";
      showError("");
      try {
        await api(url("verifyUrl", current.id), { method: "POST", body: { code: input.value.trim() }, idempotencyKey: uuid() });
        finish({ code: current.id });
      } catch (error) {
        if (error instanceof ApiError && error.fields?.code) fieldError.textContent = [].concat(error.fields.code).join(" ");
        else failed(error);
        input.select();
      }
    });
    on("[data-code-resend]", async () => {
      if (!current) return;
      showError("");
      try {
        sent(await api(url("resendUrl", current.id), { method: "POST", idempotencyKey: uuid() }), dialog.dataset.resent);
        input.value = "";
      } catch (error) {
        failed(error);
      }
    });
    on("[data-code-skip]", () => finish({ skip: true }));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        $("[data-code-confirm]").click();
      }
    }, { signal: controller.signal });
    dialog.addEventListener("close", () => finish(null), { signal: controller.signal });

    input.value = "";
    input.disabled = true;
    fieldError.textContent = "";
    showError("");
    $("[data-code-why]").textContent = reason;
    $("[data-code-confirm]").disabled = true;
    $("[data-code-resend]").disabled = true;
    $("[data-code-skip]").classList.add("hidden");
    status.textContent = status.dataset.sending;
    dialog.showModal();
    api(dialog.dataset.requestUrl, {
      method: "POST", idempotencyKey: uuid(),
      body: { branch, amount: String(amount ?? "0"), customer, phone, reservation },
    }).then((code) => sent(code, dialog.dataset.sent)).catch((error) => {
      failed(error);
      // Someone allowed to skip still can, even when the code could not be sent.
      if (canSkip) $("[data-code-skip]").classList.remove("hidden");
    });
  });
}
