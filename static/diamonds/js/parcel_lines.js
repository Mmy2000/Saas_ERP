// Small stones from parcels on a stone-setting form: add / remove rows, and read them.
import { enhanceSelects } from "../../core/js/selects.js";

document.querySelectorAll("[data-parcel-lines]").forEach((block) => {
  const rows = block.querySelector("[data-parcel-rows]");
  const template = block.querySelector("[data-parcel-row]");
  block.addEventListener("click", (event) => {
    if (event.target.closest("[data-add-parcel-line]")) {
      const row = template.content.firstElementChild.cloneNode(true);
      rows.append(row);
      enhanceSelects(row);
      row.querySelector("[data-parcel-carats]").focus();
    }
    const remove = event.target.closest("[data-remove-parcel-line]");
    if (remove) remove.closest("[data-parcel-line]").remove();
  });
});

/** The parcel lines inside `root` with carats filled in, as the API takes them. */
export function readParcelUses(root) {
  return [...root.querySelectorAll("[data-parcel-line]")]
    .filter((row) => row.querySelector("[data-parcel-carats]").value.trim())
    .map((row) => {
      // "held:<parcel>": stones of that parcel the setter already holds (Stones at workshops).
      const value = row.querySelector("[data-parcel-id]").value;
      const held = value.startsWith("held:");
      return {
        parcel: Number(held ? value.slice(5) : value),
        carats: row.querySelector("[data-parcel-carats]").value.trim(),
        count: Number(row.querySelector("[data-parcel-count]").value) || 0,
        held,
      };
    });
}
