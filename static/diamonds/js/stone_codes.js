// Stone codes on the stone rows (legacy GHD11): picking a code fills the stone's kind, origin,
// shape, colour and clarity, and its price per carat is taken on saving.
const codes = (() => {
  try {
    return JSON.parse(document.getElementById("stone-codes")?.textContent || "[]");
  } catch {
    return [];
  }
})();
const byCode = new Map(codes.map((code) => [code.code, code]));

export const stoneCode = (code) => byCode.get(code);

// Fill a row from its code; a new code drops the price kept from the old one.
export function applyCode(row) {
  const found = byCode.get(row.querySelector('[data-s="code"]').value);
  row.querySelectorAll('[data-s="price_per_ct"], [data-s="price_currency"]').forEach((input) => (input.value = ""));
  if (!found) return;
  for (const key of ["kind", "origin", "shape", "color", "clarity"]) {
    const input = row.querySelector(`[data-s="${key}"]`);
    if (input && found[key]) input.value = found[key];
  }
}

// The stones' value at their codes' prices, when all in `currency`: { value, priced }.
export function codeValue(rows, currency) {
  let value = 0;
  let priced = false;
  for (const row of rows) {
    const found = byCode.get(row.querySelector('[data-s="code"]')?.value);
    const carat = Number(row.querySelector('[data-s="carat"]').value) || 0;
    if (found && found.currency === currency && carat) {
      value += carat * Number(found.price);
      priced = true;
    }
  }
  return { value, priced };
}
