// Sent messages: send a failed e-mail again, copy or stop a shared link, and choose who gets
// the daily reminders.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("messages");

if (root) {
  const url = (template, id) => template.replace(/\/0\//, `/${id}/`);
  root.addEventListener("click", async (event) => {
    const again = event.target.closest("[data-again]");
    const stop = event.target.closest("[data-stop]");
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      try {
        await navigator.clipboard.writeText(copy.dataset.copy);
        toast(root.dataset.copied, { kind: "success" });
      } catch {
        window.prompt("", copy.dataset.copy);
      }
      return;
    }
    const button = again || stop;
    if (!button) return;
    button.disabled = true;
    try {
      if (again) await api(url(root.dataset.againUrl, again.dataset.again), { method: "POST", idempotencyKey: uuid() });
      else await api(url(root.dataset.stopUrl, stop.dataset.stop), { method: "POST", idempotencyKey: uuid() });
      flash(again ? root.dataset.queued : root.dataset.stopped);
      window.location.reload();
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
    }
  });
}

registerShaper("digest", (_values, form) => ({
  members: [...form.querySelectorAll("[data-digest-member]:checked")].map((input) => Number(input.value)),
}));
