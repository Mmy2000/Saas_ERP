// Display helpers for decimal strings from the API. Exact (BigInt fixed-point), never floats.
// Formatting only: prices and totals are always computed by the server (ADR-010).

function parse(text, scale) {
  const match = /^\s*(-)?(\d+)(?:\.(\d*))?\s*$/.exec(String(text ?? ""));
  if (!match) return null;
  const digits = (match[3] || "").padEnd(scale + 1, "0");
  let value = BigInt(match[2]) * 10n ** BigInt(scale) + BigInt(digits.slice(0, scale) || "0");
  if (Number(digits[scale]) >= 5) value += 1n; // round half up
  return match[1] ? -value : value;
}

/** "3428.5700" → "3,428.57" (places decimals, grouped, half-up). Non-numbers → "—". */
export function formatNumber(text, places = 2) {
  const value = parse(text, places);
  if (value === null) return "—";
  const negative = value < 0n;
  const abs = negative ? -value : value;
  const unit = 10n ** BigInt(places);
  const whole = (abs / unit).toLocaleString("en-US");
  const fraction = places ? `.${(abs % unit).toString().padStart(places, "0")}` : "";
  return `${negative ? "-" : ""}${whole}${fraction}`;
}

/** Exact sum of decimal strings, as a string with `places` decimals. */
export function addDecimals(a, b, places = 2) {
  const total = (parse(a, places) ?? 0n) + (parse(b, places) ?? 0n);
  const negative = total < 0n;
  const abs = negative ? -total : total;
  const unit = 10n ** BigInt(places);
  return `${negative ? "-" : ""}${abs / unit}.${(abs % unit).toString().padStart(places, "0")}`;
}

/** Percent text to a fraction string without floats: "12.5" → "0.125". */
export function percentToRate(text) {
  const match = /^\s*(\d+)(?:\.(\d+))?\s*$/.exec(String(text ?? ""));
  if (!match) return "0";
  const digits = (match[1] + (match[2] || "")).replace(/^0+(?=\d)/, "");
  const point = match[1].replace(/^0+(?=\d)/, "").length - 2;
  const padded = point <= 0 ? "0".repeat(1 - point) + digits : digits;
  const at = point <= 0 ? 1 : point;
  return `${padded.slice(0, at)}.${padded.slice(at) || "0"}`;
}

/** Fraction string to percent text: "0.125" → "12.5". */
export function rateToPercent(text) {
  const value = parse(text, 6);
  if (!value) return "";
  const hundredths = value * 100n;
  const whole = hundredths / 1000000n;
  const fraction = (hundredths % 1000000n).toString().padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : `${whole}`;
}
