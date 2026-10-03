// Floating scroll buttons: "back to top" once the page is scrolled down (its ring shows how
// far), "go to the bottom" while a long page has more below. Hidden buttons leave the tab order.

const SHOW_AFTER = 320; // px scrolled before "back to top" appears
const NEAR_END = 160; // px from the bottom where "go to the bottom" hides

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function setVisible(button, visible) {
  if (!button || button.dataset.visible === String(visible)) return;
  button.dataset.visible = String(visible);
  button.tabIndex = visible ? 0 : -1;
  button.setAttribute("aria-hidden", String(!visible));
}

export function initScrollButtons() {
  const up = document.querySelector("[data-scroll-to=top]");
  const down = document.querySelector("[data-scroll-to=bottom]");
  if (!up && !down) return;
  const ring = up?.querySelector("[data-scroll-progress]");
  let queued = false;

  function update() {
    queued = false;
    const root = document.documentElement;
    const y = window.scrollY;
    const room = root.scrollHeight - window.innerHeight;
    setVisible(up, y > SHOW_AFTER);
    setVisible(down, room > SHOW_AFTER && room - y > NEAR_END);
    if (ring) ring.style.strokeDashoffset = String(room > 0 ? 100 - Math.min(100, (y / room) * 100) : 100);
  }

  function queue() {
    if (!queued) {
      queued = true;
      window.requestAnimationFrame(update);
    }
  }

  window.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue);
  // Pages grow after load (live parts, added lines): re-check when the content changes size.
  if ("ResizeObserver" in window) new ResizeObserver(queue).observe(document.body);

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scroll-to]");
    if (!button) return;
    const top = button.dataset.scrollTo === "top" ? 0 : document.documentElement.scrollHeight;
    window.scrollTo({ top, behavior: reduceMotion.matches ? "auto" : "smooth" });
  });
  update();
}
