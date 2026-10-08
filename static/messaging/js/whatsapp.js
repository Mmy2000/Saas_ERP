// Settings → WhatsApp: link the shop's number (start, show the QR, follow the scan), send a
// test, unlink. The status is asked every 3 s while waiting, and stops once linked.
import { api, ApiError } from "../../core/js/api.js";
import { busy } from "../../core/js/loading.js";
import { strings, toast } from "../../core/js/ui.js";

const card = document.querySelector("[data-wa-link]");
const WAIT = 3000;
let timer = null;

function show(state) {
  card.dataset.status = state.status;
  card.querySelectorAll("[data-wa-step]").forEach((step) => {
    step.hidden = !step.dataset.waStep.split(" ").includes(state.status);
  });
  const badge = card.querySelector("[data-wa-badge]");
  badge.textContent = state.label;
  badge.className = `badge ${state.status === "connected" ? "badge-success" : state.status === "qr" ? "badge-warning" : ""}`;
  const qr = card.querySelector("[data-wa-qr]");
  if (qr) {
    qr.hidden = !state.qr;
    if (state.qr) qr.src = state.qr;
    card.querySelector("[data-wa-wait]").hidden = Boolean(state.qr);
  }
  const phone = card.querySelector("[data-wa-phone]");
  if (phone && state.phone) phone.textContent = `+${state.phone}`;
  const error = card.querySelector("[data-wa-error]");
  if (error) {
    error.textContent = state.error || "";
    error.classList.toggle("hidden", !state.error);
  }
  clearTimeout(timer);
  if (state.status === "starting" || state.status === "qr") timer = setTimeout(poll, WAIT);
}

async function poll() {
  try {
    show(await api(card.dataset.endpoint, { quiet: true }));
  } catch {
    timer = setTimeout(poll, WAIT * 2);
  }
}

function fail(error) {
  toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
}

if (card && card.querySelector("[data-wa-step]")) {
  show({ status: card.dataset.status, label: card.querySelector("[data-wa-badge]").textContent,
         error: card.querySelector("[data-wa-error]")?.textContent.trim() || "" });
  if (card.dataset.status !== "not_started") poll();

  card.querySelector("[data-wa-start]")?.addEventListener("click", async (event) => {
    const done = busy(event.currentTarget);
    try {
      show(await api(card.dataset.endpoint, { method: "POST" }));
    } catch (error) {
      fail(error);
    } finally {
      done();
    }
  });

  card.querySelector("[data-wa-unlink]")?.addEventListener("click", async () => {
    if (!window.confirm(card.dataset.confirmUnlink)) return;
    try {
      show(await api(card.dataset.endpoint, { method: "DELETE" }));
    } catch (error) {
      fail(error);
    }
  });

  card.querySelector("[data-wa-test]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const slot = form.querySelector("[data-error-for='to']");
    slot.textContent = "";
    const done = busy(form.querySelector("[type=submit]"));
    try {
      const sent = await api(card.dataset.testUrl, { method: "POST", body: { to: form.elements.to.value } });
      if (sent.status === "sent") toast(card.dataset.sent);
      else toast(sent.error, { kind: "error" });
    } catch (error) {
      if (error instanceof ApiError && error.fields.to) slot.textContent = error.fields.to.join(" ");
      else fail(error);
    } finally {
      done();
    }
  });
}
