// Live preview of a document design: every change posts the (unsaved) form to the preview URL
// and shows the page it returns in the iframe, scaled to fit. Nothing is saved until "Save".

const DELAY = 300;

function init(form) {
  const frame = form.querySelector("[data-design-preview]");
  const box = form.querySelector("[data-preview-frame-box]");
  const status = form.querySelector("[data-preview-status]");
  if (!frame) return;
  let timer = null;
  let controller = null;

  // A roll is ~80 mm wide: a narrow frame shows it at a readable size; paper gets an A4 frame.
  function size() {
    const roll = form.elements.paper?.value === "roll80" || form.elements.layout?.value === "thermal";
    frame.style.width = roll ? "360px" : "840px";
    frame.style.height = roll ? "900px" : "1190px";
  }

  function fit() {
    size();
    const scale = Math.min(1, (box.clientWidth - 24) / frame.offsetWidth);
    frame.style.transform = `scale(${scale})`;
    frame.parentElement.style.height = `${Math.round(frame.offsetHeight * scale)}px`;
  }

  async function refresh() {
    controller?.abort();
    controller = new AbortController();
    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(String(response.status));
      frame.srcdoc = await response.text();
      fit();
      status?.classList.remove("text-red-600");
    } catch (error) {
      if (error.name !== "AbortError") status?.classList.add("text-red-600");
    }
  }

  function queue() {
    clearTimeout(timer);
    timer = setTimeout(refresh, DELAY);
  }

  form.addEventListener("input", queue);
  form.addEventListener("change", queue);
  window.addEventListener("resize", fit);
  fit();
  refresh();
}

for (const form of document.querySelectorAll("[data-design-form]")) init(form);
