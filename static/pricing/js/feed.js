// Market prices card (pricing/_feed.html): the settings form and the buttons (update now,
// approve a held change, keep my prices, pause, resume).
import { api, ApiError, uuid } from "../../core/js/api.js";
import { registerShaper } from "../../core/js/forms.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const card = document.getElementById("price-feed");

if (card) {
  card.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-feed-action]");
    if (!button) return;
    button.disabled = true;
    try {
      await api(card.dataset.actionUrl, { method: "POST", body: { action: button.dataset.feedAction }, idempotencyKey: uuid() });
      flash(card.dataset.done);
      window.location.reload();
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
    }
  });
}

registerShaper("price-feed", (values) => ({
  is_enabled: Boolean(values.is_enabled),
  source: values.source || "",
  sell_adjust: values.sell_adjust || "0",
  buy_adjust: values.buy_adjust || "0",
  scrap_adjust: values.scrap_adjust || "0",
  rounding: values.rounding || "1",
  min_change: values.min_change || "0",
  max_jump_pct: values.max_jump_pct || "3",
  follow_silver: Boolean(values.follow_silver),
  active_from: values.active_from || null,
  active_to: values.active_to || null,
}));
