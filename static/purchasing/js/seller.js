// The "From: supplier | trade account" picker (templates purchasing/_seller_picker.html).

/** Show the party search matching the chosen kind of seller. */
export function initSeller(root) {
  const picker = root.querySelector("[data-seller-picker]");
  if (!picker) return;
  const sync = () => {
    const role = sellerRole(root);
    picker.querySelectorAll("[data-seller-for]").forEach((node) => (node.hidden = node.dataset.sellerFor !== role));
  };
  picker.addEventListener("change", (event) => {
    if (event.target.matches("[data-seller-role]")) sync();
  });
  sync();
}

export function sellerRole(root) {
  return root.querySelector("[data-seller-role]:checked")?.value || "supplier";
}

/** { seller_role, supplier } for the request body. */
export function seller(root) {
  const role = sellerRole(root);
  const value = root.querySelector(`[data-seller-for="${role}"] [data-seller-select]`)?.value;
  return { seller_role: role, supplier: value ? Number(value) : null };
}
