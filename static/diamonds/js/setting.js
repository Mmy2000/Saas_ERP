// Stone setting: scan the mounting and the loose stones, weigh the result, post it.
import { api, ApiError, uuid } from "../../core/js/api.js";
import { formatNumber } from "../../core/js/money.js";
import { flash, strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("setting");

if (root) {
  const $ = (selector) => root.querySelector(selector);
  const state = { piece: null, stones: [] };
  const key = uuid();
  const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

  const render = () => {
    const piece = $("[data-piece]");
    piece.classList.toggle("hidden", !state.piece);
    if (state.piece) {
      piece.textContent = `${state.piece.name} · ${state.piece.karat} · ${formatNumber(state.piece.gross_weight_g, 3)} g`
        + (state.piece.stones ? ` · ${state.piece.stones}` : "");
    }
    const body = $("[data-stones]");
    body.replaceChildren(...state.stones.map((stone, index) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td class="num font-medium"></td><td class="text-sm text-slate-600" dir="ltr"></td>
        <td class="text-end"><span class="num"></span> ct</td><td class="text-end"><button type="button" class="btn btn-ghost text-slate-400">×</button></td>`;
      row.children[0].textContent = stone.barcode;
      row.children[1].textContent = stone.stones;
      row.children[2].querySelector("span").textContent = formatNumber(stone.stone_weight_ct, 3);
      row.querySelector("button").addEventListener("click", () => { state.stones.splice(index, 1); render(); });
      return row;
    }));
    $("[data-stones-empty]").classList.toggle("hidden", state.stones.length > 0);
    $("[data-carats]").textContent = formatNumber(
      state.stones.reduce((sum, stone) => sum + Number(stone.stone_weight_ct), 0), 3);
  };

  root.querySelectorAll("[data-scan]").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.querySelector("input");
    const term = input.value.trim();
    if (!term) return;
    try {
      const found = await api(`${root.dataset.lookupUrl}?family=${form.dataset.scan}&q=${encodeURIComponent(term)}`);
      const item = found.results[0];
      if (form.dataset.scan === "diamond") {
        state.piece = item;
        if (!$("[data-after]").value) $("[data-after]").value = item.gross_weight_g;
      } else if (!state.stones.some((stone) => stone.id === item.id)) {
        state.stones.push(item);
      }
      input.value = "";
      render();
    } catch (error) {
      fail(error);
    }
  }));

  $("[data-save]").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    root.querySelectorAll("[data-error-for]").forEach((node) => (node.textContent = ""));
    $("[data-error]").textContent = "";
    button.disabled = true;
    try {
      const made = await api(root.dataset.postUrl, {
        method: "POST", idempotencyKey: key,
        body: {
          piece: state.piece?.id || 0, stones: state.stones.map((stone) => stone.id),
          gross_after_g: $("[data-after]").value.trim(), setter: Number($("[data-setter]").value) || null,
          labour_amount: $("[data-labour]").value.trim() || "0", note: $("[data-note]").value.trim(),
        },
      });
      flash(root.dataset.saved);
      window.location.assign(root.dataset.detailUrl.replace("{id}", made.id));
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [name, problems] of Object.entries(error.fields || {})) {
          const node = root.querySelector(`[data-error-for="${name}"]`);
          if (node) node.textContent = [].concat(problems).join(" ");
        }
        $("[data-error]").textContent = error.message;
      } else fail(error);
      button.disabled = false;
    }
  });
}
