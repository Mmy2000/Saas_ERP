// Cash count: add up the notes and coins (or take the total), show the difference with the books
// as you type, and send the count.
import { registerShaper } from "../../core/js/forms.js";
import { formatNumber } from "../../core/js/money.js";

const form = document.getElementById("count");

if (form) {
  const expected = Number(form.dataset.expected || 0);
  const pieces = [...form.querySelectorAll("[data-denomination]")];
  const total = form.querySelector("[data-counted]");
  const $ = (name) => form.querySelector(`[data-f="${name}"]`);
  const cents = (value) => Math.round(value * 100) / 100;

  const counted = () => {
    if (!pieces.length) return Number(total.value.replace(/,/g, "")) || 0;
    return pieces.reduce((sum, input) => {
      const amount = cents(Number(input.dataset.denomination) * (parseInt(input.value, 10) || 0));
      input.closest("tr").querySelector("[data-line-total]").textContent = formatNumber(amount, 2);
      return sum + amount;
    }, 0);
  };

  const refresh = () => {
    const sum = cents(counted());
    const difference = cents(sum - expected);
    $("counted").textContent = formatNumber(sum, 2);
    $("difference").textContent = (difference > 0 ? "+" : "") + formatNumber(difference, 2);
    $("difference").classList.toggle("text-red-600", difference < 0);
    $("difference").classList.toggle("text-emerald-700", difference >= 0);
    const label = $("label");
    label.textContent = difference < 0 ? label.dataset.short : difference > 0 ? label.dataset.over : label.dataset.even;
  };

  form.addEventListener("input", refresh);
  refresh();

  registerShaper("cash-count", (values) => {
    const body = { cash_box: Number(values.cash_box), note: values.note || "" };
    if (pieces.length) {
      body.denominations = Object.fromEntries(pieces.filter((input) => parseInt(input.value, 10) > 0)
        .map((input) => [input.dataset.denomination, parseInt(input.value, 10)]));
      if (!Object.keys(body.denominations).length) body.counted = "0";
    } else {
      body.counted = total.value.trim();
    }
    return body;
  });
}
