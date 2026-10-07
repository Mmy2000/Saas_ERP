// Sent messages: send a failed e-mail again, copy or stop a shared link, choose who gets the
// daily and monthly report e-mails, and e-mail myself last month's report.
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

const ticked = (form, name) => [...form.querySelectorAll(`[data-digest-${name}]:checked`)].map((input) => Number(input.value));
registerShaper("digest", (_values, form) => ({ daily: ticked(form, "daily"), monthly: ticked(form, "monthly") }));

const monthlyNow = document.querySelector("[data-monthly-now]");
monthlyNow?.querySelector("[data-monthly-go]").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api(monthlyNow.dataset.url, { method: "POST", idempotencyKey: uuid() });
    flash(monthlyNow.dataset.queued);
    window.location.reload();
  } catch (error) {
    toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
    button.disabled = false;
  }
});
