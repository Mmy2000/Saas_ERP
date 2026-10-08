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
  let via = "link"; // how WhatsApp goes: the shop's number, the service, or a wa.me link
  let key = uuid();

  // Help texts and the button follow the route; a help text is shown when it names the route
  // and (for those inside the message block) WhatsApp is the channel.
  const syncVia = () => {
    dialog.querySelectorAll("[data-via-help]").forEach((node) => {
      const forRoute = node.dataset.viaHelp.split(" ").includes(via);
      node.hidden = !forRoute || (node.dataset.for && node.dataset.for !== channel);
    });
    const go = $("[data-send-go]");
    if (channel === "whatsapp") go.textContent = via === "link" ? go.dataset.labelWhatsapp : go.dataset.labelGateway;
  };

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
    syncVia();
  };

  dialog.addEventListener("change", (event) => {
    if (event.target.name === "channel") setChannel(event.target.value);
    if (event.target.name === "via") {
      via = event.target.value;
      syncVia();
    }
  });

  opener.addEventListener("click", async () => {
    showError("");
    key = uuid();
    dialog.showModal();
    // Until the draft arrives: the message box shimmers and Send waits.
    const message = $("[data-send-message]");
    const go = $("[data-send-go]");
    message.value = "";
    message.classList.add("skeleton");
    go.disabled = true;
    try {
      const params = new URLSearchParams(
        Object.entries(target()).filter(([, value]) => value !== null && value !== undefined));
      const draft = await api(`${dialog.dataset.draftUrl}?${params}`);
      $("[data-send-label]").textContent = draft.label;
      $("[data-send-email]").value = draft.email || "";
      $("[data-send-phone]").value = (draft.phone || "").replace(/^\+/, "");
      texts.email = draft.email_text;
      texts.whatsapp = draft.whatsapp_text;
      const routes = draft.whatsapp_routes || ["link"];
      dialog.querySelectorAll("[data-route]").forEach((node) => (node.hidden = !routes.includes(node.dataset.route)));
      $("[data-send-routes]").hidden = routes.length < 2;
      via = routes[0];
      dialog.querySelector(`input[name="via"][value="${via}"]`).checked = true;
      syncVia();
      $("[data-send-message]").value = texts[channel];
      (channel === "email" ? $("[data-send-email]") : $("[data-send-phone]")).focus();
    } catch (error) {
      showError(error instanceof ApiError ? error.message : strings().network);
    } finally {
      message.classList.remove("skeleton");
      go.disabled = false;
    }
  });

  $("[data-send-go]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const toError = dialog.querySelector(`[data-for="${channel}"] [data-error-for="to"]`);
    showError("");
    toError.textContent = "";
    // WhatsApp has to open from the click itself, or the browser blocks the new tab.
    const tab = channel === "whatsapp" && via === "link" ? window.open("about:blank", "_blank") : null;
    button.disabled = true;
    try {
      const to = channel === "email" ? $("[data-send-email]").value : $("[data-send-phone]").value;
      const body = { ...target(), channel, to: to.trim(), message: $("[data-send-message]").value,
                     via: channel === "whatsapp" ? via : "link" };
      const sent = await api(dialog.dataset.sendUrl, { method: "POST", body, idempotencyKey: key });
      if (sent.wa_url) {
        if (tab) tab.location.href = sent.wa_url;
        else window.location.href = sent.wa_url;
      }
      dialog.close();
      const done = channel === "email" ? dialog.dataset.queued
        : via === "link" ? dialog.dataset.shared : dialog.dataset.queuedWhatsapp;
      toast(done, { kind: "success" });
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
