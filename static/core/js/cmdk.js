// Command palette: Ctrl/⌘+K (or the search buttons) opens a list of every screen the user may
// open; typing filters it, arrows move, Enter opens. The list is rendered by layouts/app.html.

const normalize = (text) =>
  text.toLowerCase()
    .normalize("NFKD")
    .replace(/[ً-ْـ]/g, "") // Arabic diacritics and tatweel
    .replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي");

export function initCommandPalette() {
  const dialog = document.querySelector("[data-cmdk]");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const input = dialog.querySelector("[data-cmdk-input]");
  const items = [...dialog.querySelectorAll("[data-cmdk-item]")];
  const empty = dialog.querySelector("[data-cmdk-empty]");
  items.forEach((item) => {
    item.dataset.search = normalize(item.dataset.text || item.textContent);
    item.dataset.name = normalize(item.dataset.label || item.textContent);
  });
  let visible = items;
  let index = 0;

  const select = (next) => {
    if (!visible.length) return;
    index = (next + visible.length) % visible.length;
    items.forEach((item) => item.setAttribute("aria-selected", "false"));
    visible[index].setAttribute("aria-selected", "true");
    visible[index].scrollIntoView({ block: "nearest" });
  };

  const filter = () => {
    const words = normalize(input.value).split(/\s+/).filter(Boolean);
    visible = items.filter((item) => {
      const match = words.every((word) => item.dataset.search.includes(word));
      item.hidden = !match;
      return match;
    });
    dialog.querySelectorAll("[data-cmdk-group]").forEach((group) => {
      group.hidden = !group.querySelector("[data-cmdk-item]:not([hidden])");
    });
    empty.classList.toggle("hidden", visible.length > 0);
    // Start on the first screen whose own name matches, not one matched by its section title.
    const named = visible.findIndex((item) => words.every((word) => item.dataset.name.includes(word)));
    select(Math.max(named, 0));
  };

  const open = () => {
    if (dialog.open) return;
    input.value = "";
    filter();
    dialog.showModal();
    input.focus();
  };

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      dialog.open ? dialog.close() : open();
    }
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-cmdk-open]")) open();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close(); // a click on the backdrop
  });
  input.addEventListener("input", filter);
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      select(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      select(index - 1);
    } else if (event.key === "Enter" && visible[index]) {
      event.preventDefault();
      window.location.assign(visible[index].href);
    }
  });
  items.forEach((item) => item.addEventListener("mousemove", () => {
    const at = visible.indexOf(item);
    if (at !== index && at >= 0) select(at);
  }));
}
