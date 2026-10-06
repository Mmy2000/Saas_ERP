// Hover for the server-rendered line charts (templates/console/_line_chart.html): a crosshair
// on the nearest reading and a tooltip with its time and values. Delegated from the document,
// so it keeps working when the live page swaps the charts.

const cache = new WeakMap();

function dataOf(root) {
  if (!cache.has(root)) {
    try {
      cache.set(root, JSON.parse(root.dataset.chart));
    } catch {
      cache.set(root, null);
    }
  }
  return cache.get(root);
}

function hide(plot) {
  plot.querySelector("[data-chart-cursor]")?.setAttribute("hidden", "");
  plot.querySelector("[data-chart-tip]")?.classList.add("hidden");
}

function show(plot, clientX) {
  const root = plot.closest("[data-chart]");
  const data = root && dataOf(root);
  const rows = data?.rows || [];
  if (rows.length < 2) return;
  const rect = plot.getBoundingClientRect();
  const fraction = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  const index = Math.round(fraction * (rows.length - 1));
  const [time, ...values] = rows[index];
  const x = (index / (rows.length - 1)) * 600;

  const cursor = plot.querySelector("[data-chart-cursor]");
  cursor.setAttribute("x1", x);
  cursor.setAttribute("x2", x);
  cursor.removeAttribute("hidden");

  const tip = plot.querySelector("[data-chart-tip]");
  tip.replaceChildren();
  const head = document.createElement("p");
  head.className = "num mb-1 text-slate-500";
  head.textContent = time;
  tip.append(head);
  data.labels.forEach((label, i) => {
    const row = document.createElement("p");
    row.className = "flex items-center justify-between gap-4";
    const name = document.createElement("span");
    name.className = "inline-flex items-center gap-1.5 text-slate-600";
    const swatch = document.createElement("span");
    swatch.className = "h-0.5 w-3 rounded-full";
    swatch.style.background = `var(--series-${i + 1})`;
    name.append(swatch, label);
    const value = document.createElement("span");
    value.className = "num font-medium text-slate-900";
    value.textContent = values[i];
    row.append(name, value);
    tip.append(row);
  });
  tip.classList.remove("hidden");
  // Keep the tooltip inside the plot: to the right of the cursor, or to its left near the end.
  const px = fraction * rect.width;
  const right = px + tip.offsetWidth + 16 > rect.width;
  tip.style.left = right ? `${Math.max(0, px - tip.offsetWidth - 10)}px` : `${px + 10}px`;
}

document.addEventListener("pointermove", (event) => {
  const plot = event.target.closest?.("[data-chart-plot]");
  document.querySelectorAll("[data-chart-plot]").forEach((other) => {
    if (other !== plot) hide(other);
  });
  if (plot) show(plot, event.clientX);
});
document.addEventListener("pointerleave", () => document.querySelectorAll("[data-chart-plot]").forEach(hide));
