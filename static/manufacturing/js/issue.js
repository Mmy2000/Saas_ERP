// Send gold to a workshop, or start in-house production: pick gold by weight at the branch,
// post in one request.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { flash, strings, toast } from "../../core/js/ui.js";
import { goodsPicker } from "../../inventory/js/goods_picker.js";

const root = document.getElementById("work-order");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const picker = goodsPicker(root, $("select[data-branch]"));
  const key = uuid();
  $("[data-add-bulk]").click(); // one empty line to start with

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    // In-house production has no workshop; it may name the craftsman instead.
    const inHouse = "inHouse" in root.dataset;
    const workshop = inHouse ? "" : $("[data-workshop]").value;
    const craftsman = inHouse ? $("[data-craftsman]").value : "";
    const body = {
      branch: Number($("select[data-branch]").value), workshop: workshop ? Number(workshop) : null,
      in_house: inHouse, craftsman: craftsman ? Number(craftsman) : null,
      kind: root.querySelector("[data-kind]:checked").value,
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
