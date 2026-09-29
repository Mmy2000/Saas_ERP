// Return goods to a supplier: pick pieces and bulk gold at the branch, post in one request.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { goodsPicker } from "../../inventory/js/goods_picker.js";
import { initSeller, seller } from "./seller.js";

const root = document.getElementById("supplier-return");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const picker = goodsPicker(root, $("select[data-branch]"));
  const key = uuid();
  initSeller(root);

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const body = {
      branch: Number($("select[data-branch]").value), ...seller(root),
      lines: picker.lines(), note: $("[data-note]").value.trim(),
    };
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, { method: "POST", body, idempotencyKey: key });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
    }
  });
}
