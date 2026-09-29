// Send goods to another branch: pick pieces and bulk gold at the source branch, post in one
// request. The server re-checks everything.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { goodsPicker } from "./goods_picker.js";

const root = document.getElementById("transfer");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const picker = goodsPicker(root, $("[data-from]"));
  const key = uuid();

  $("[data-send]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const body = {
      from_branch: Number($("[data-from]").value),
      to_branch: $("[data-to]").value ? Number($("[data-to]").value) : null,
      lines: picker.lines(), note: $("[data-note]").value.trim(),
    };
    button.disabled = true;
    try {
      const transfer = await api(root.dataset.postUrl, { method: "POST", body, idempotencyKey: key });
      flash(root.dataset.sent || "");
      window.location.assign(root.dataset.detailUrl.replace("{id}", transfer.id));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
      button.disabled = false;
    }
  });
}
