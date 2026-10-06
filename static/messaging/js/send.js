// The Send dialog (template: messaging/_send.html): e-mail a document or statement as a PDF,
// or open WhatsApp with a private link to it. The dialog starts with the party's contact and a
// message in the workspace language (GET draft/).
import { api, ApiError, uuid } from "../../core/js/api.js";
import { strings, toast } from "../../core/js/ui.js";

const opener = document.querySelector("[data-send-open]");
const dialog = document.querySelector("[data-send-dialog]");

if (opener && dialog) {
  const $ = (selector) => dialog.querySelector(selector);
  const target = () => {
    const d = opener.dataset;
    const out = { doc_type: d.docType, pk: Number(d.pk) };
    if (d.docType === "statement") {
      Object.assign(out, { role: d.role, date_from: d.dateFrom || null, date_to: d.dateTo || null });
    }
    return out;
  };
  const texts = { email: "", whatsapp: "" };
  let channel = "email";
  let key = uuid();

  const showError = (message) => {
    const box = $("[data-send-error]");
    box.textContent = message || "";
    box.classList.toggle("hidden", !message);
  };

  const setChannel = (value) => {
    texts[channel] = $("[data-send-message]").value; // keep what was typed for the other one
    channel = value;
    dialog.querySelectorAll("[data-for]").forEach((node) => (node.hidden = node.dataset.for !== value));
    $("[data-send-message]").value = texts[value];
    const go = $("[data-send-go]");
    go.textContent = value === "email" ? go.dataset.labelEmail : go.dataset.labelWhatsapp;
    showError("");
  };

  dialog.addEventListener("change", (event) => {
    if (event.target.name === "channel") setChannel(event.target.value);
  });

  opener.addEventListener("click", async () => {
    showError("");
    key = uuid();
    dialog.showModal();
    try {
      const params = new URLSearchParams(
        Object.entries(target()).filter(([, value]) => value !== null && value !== undefined));
      const draft = await api(`${dialog.dataset.draftUrl}?${params}`);
      $("[data-send-label]").textContent = draft.label;
      $("[data-send-email]").value = draft.email || "";
      $("[data-send-phone]").value = (draft.phone || "").replace(/^\+/, "");
      texts.email = draft.email_text;
      texts.whatsapp = draft.whatsapp_text;
      $("[data-send-message]").value = texts[channel];
      (channel === "email" ? $("[data-send-email]") : $("[data-send-phone]")).focus();
    } catch (error) {
      showError(error instanceof ApiError ? error.message : strings().network);
    }
  });

  $("[data-send-go]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const toError = dialog.querySelector('[data-error-for="to"]');
    showError("");
    toError.textContent = "";
    // WhatsApp has to open from the click itself, or the browser blocks the new tab.
    const tab = channel === "whatsapp" ? window.open("about:blank", "_blank") : null;
    button.disabled = true;
    try {
      const to = channel === "email" ? $("[data-send-email]").value : $("[data-send-phone]").value;
      const body = { ...target(), channel, to: to.trim(), message: $("[data-send-message]").value };
      const sent = await api(dialog.dataset.sendUrl, { method: "POST", body, idempotencyKey: key });
      if (sent.wa_url) {
        if (tab) tab.location.href = sent.wa_url;
        else window.location.href = sent.wa_url;
      }
      dialog.close();
      toast(channel === "email" ? dialog.dataset.queued : dialog.dataset.shared, { kind: "success" });
      key = uuid();
    } catch (error) {
      if (tab) tab.close();
      if (error instanceof ApiError && error.fields?.to) {
        toError.textContent = [].concat(error.fields.to).join(" ");
      } else {
        showError(error instanceof ApiError ? error.message : strings().network);
      }
    } finally {
      button.disabled = false;
    }
  });
}
