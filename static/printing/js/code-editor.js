// The console's custom-design editor: CodeMirror (static/vendor/codemirror) on the form's HTML
// textarea. It keeps the textarea in step (so the live preview follows every key), and adds:
// layouts to start from, an "Insert" panel and completions of what a design can use, the
// preview's error marked on its line, full screen beside the preview, and a guard against
// leaving with unsaved work. Without JavaScript the plain textarea still works.
import { toast } from "../../core/js/ui.js";

const CM = window.CodeMirror;
const WRAP_KEY = "console.code-editor.wrap";

function stored(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private window: not remembered */ }
}

function fill(text, values) {
  return text.replace(/\{(\w+)\}/g, (all, name) => (name in values ? values[name] : all));
}

function init(shell) {
  const form = shell.closest("form");
  const area = shell.querySelector("textarea[name=custom_html]");
  const toggle = form?.elements.use_custom;
  if (!CM || !form || !area) return;
  const msg = shell.dataset;
  const check = shell.querySelector("[data-code-check]");
  const position = shell.querySelector("[data-code-position]");
  const dirtyNote = shell.querySelector("[data-code-dirty]");
  const reference = shell.querySelector("[data-reference]");
  const referenceToggle = shell.querySelector("[data-reference-toggle]");
  const wrapButton = shell.querySelector('[data-code-action="wrap"]');
  const focusButton = shell.querySelector('[data-code-action="focus"]');
  const codes = [...shell.querySelectorAll("[data-code]")].map((item) => ({
    text: item.dataset.code, label: item.dataset.label,
  }));
  const starters = JSON.parse(document.getElementById("design-starters")?.textContent || "[]");

  const save = () => form.requestSubmit();
  const editor = CM.fromTextArea(area, {
    // theme "app": CodeMirror's default colours stay off; app.css colours the code per theme.
    mode: "django", theme: "app", lineNumbers: true, lineWrapping: stored(WRAP_KEY) === "1", indentUnit: 2,
    tabSize: 2, direction: "ltr", autoCloseTags: true, autoCloseBrackets: true,
    matchTags: { bothTags: true }, matchBrackets: true, styleActiveLine: true,
    gutters: ["CodeMirror-linenumbers", "code-error-gutter"],
    extraKeys: {
      "Ctrl-S": save, "Cmd-S": save,
      "Ctrl-Space": (cm) => suggest(cm, true),
      "Ctrl-/": "toggleComment", "Cmd-/": "toggleComment",
      Tab: (cm) => (cm.somethingSelected() ? cm.indentSelection("add") : cm.execCommand("insertSoftTab")),
      "Shift-Tab": "indentLess",
    },
  });

  // ---- completions: inside {{ }} / {% %}, the names a design can use ----
  function inTag(cm) {
    const cursor = cm.getCursor();
    const before = cm.getLine(cursor.line).slice(0, cursor.ch);
    const open = Math.max(before.lastIndexOf("{{"), before.lastIndexOf("{%"));
    return open >= 0 && open > Math.max(before.lastIndexOf("}}"), before.lastIndexOf("%}"));
  }

  function hints(cm) {
    const cursor = cm.getCursor();
    const word = /[\w.]*$/.exec(cm.getLine(cursor.line).slice(0, cursor.ch))[0];
    const needle = word.toLowerCase();
    const matches = codes.filter((c) => c.text !== word && c.text.toLowerCase().includes(needle));
    matches.sort((a, b) => Number(!a.text.startsWith(word)) - Number(!b.text.startsWith(word)));
    return {
      from: CM.Pos(cursor.line, cursor.ch - word.length), to: cursor,
      list: matches.map((c) => ({
        text: c.text,
        render(node) {
          const code = document.createElement("span");
          code.className = "code-hint-code";
          code.textContent = c.text;
          const label = document.createElement("span");
          label.className = "code-hint-label";
          label.textContent = c.label;
          node.append(code, label);
        },
      })),
    };
  }

  function suggest(cm, asked = false) {
    if (asked || inTag(cm)) cm.showHint({ hint: hints, completeSingle: false });
  }

  editor.on("inputRead", (cm, change) => {
    if (!cm.state.completionActive && /^[\w.]$/.test(change.text.join(""))) suggest(cm);
  });

  // ---- the textarea, the preview and unsaved work ----
  const snapshot = () => new URLSearchParams(new FormData(form)).toString();
  const initial = snapshot();
  let submitting = false;

  function markDirty() {
    dirtyNote.hidden = snapshot() === initial;
  }

  editor.on("change", () => {
    editor.save();
    area.dispatchEvent(new Event("input", { bubbles: true }));
  });
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);
  form.addEventListener("submit", () => { submitting = true; });
  window.addEventListener("beforeunload", (event) => {
    if (submitting || snapshot() === initial) return;
    event.preventDefault();
    event.returnValue = "";
  });

  editor.on("cursorActivity", () => {
    const cursor = editor.getCursor();
    position.textContent = fill(msg.msgPosition, { line: cursor.line + 1, column: cursor.ch + 1 });
  });
  CM.signal(editor, "cursorActivity", editor);

  // ---- the preview's verdict: off / empty / the error on its line / fine ----
  let errorLine = null;

  function goTo(line) {
    editor.focus();
    editor.setCursor({ line: line - 1, ch: 0 });
    editor.scrollIntoView(null, 120);
  }

  function showCheck({ error = "", line = 0 } = {}) {
    if (errorLine) editor.removeLineClass(errorLine, "background", "code-error-line");
    editor.clearGutter("code-error-gutter");
    errorLine = null;
    check.replaceChildren();
    check.className = "code-check";
    check.onclick = null;
    if (toggle && !toggle.checked) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "font-medium text-brand-700 hover:underline";
      button.textContent = msg.msgTurnOn;
      button.addEventListener("click", () => {
        toggle.checked = true;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      });
      check.append(msg.msgOff, " ", button);
      return;
    }
    if (!editor.getValue().trim()) {
      check.textContent = msg.msgEmpty;
      return;
    }
    if (error) {
      check.classList.add("code-check-error");
      const text = document.createElement("bdi"); // English from Django, in an Arabic line
      text.textContent = error;
      check.append(line ? `${fill(msg.msgLine, { line })}: ` : "", text);
      check.onclick = line ? () => goTo(line) : null;
      if (line && line <= editor.lineCount()) {
        errorLine = editor.addLineClass(line - 1, "background", "code-error-line");
        const dot = document.createElement("span");
        dot.className = "code-error-dot";
        dot.title = error;
        editor.setGutterMarker(line - 1, "code-error-gutter", dot);
      }
      return;
    }
    check.classList.add("code-check-ok");
    check.textContent = `✓ ${msg.msgOk}`;
  }

  form.addEventListener("design:preview", (event) => showCheck(event.detail));
  showCheck();

  // ---- start from a layout ----
  const menu = shell.querySelector("[data-menu]");
  const menuButton = menu.querySelector("[data-menu-button]");
  const menuPanel = menu.querySelector("[data-menu-panel]");

  function openMenu(open) {
    menuPanel.hidden = !open;
    menuButton.setAttribute("aria-expanded", String(open));
  }

  menuButton.addEventListener("click", () => openMenu(menuPanel.hidden));
  document.addEventListener("click", (event) => { if (!menu.contains(event.target)) openMenu(false); });
  menuPanel.addEventListener("click", (event) => {
    const starter = starters.find((s) => s.key === event.target.closest("[data-starter]")?.dataset.starter);
    if (!starter) return;
    openMenu(false);
    // One change, so Ctrl+Z brings back what was there.
    editor.replaceRange(starter.html, CM.Pos(editor.firstLine(), 0), CM.Pos(editor.lastLine()));
    editor.setCursor(0, 0);
    editor.focus();
    if (toggle && !toggle.checked) {
      toggle.checked = true;
      toggle.dispatchEvent(new Event("change", { bubbles: true }));
    }
    toast(fill(msg.msgStarted, { name: starter.label }));
  });

  // ---- the "Insert" panel ----
  function openReference(open) {
    reference.hidden = !open;
    referenceToggle.setAttribute("aria-expanded", String(open));
    if (open) reference.querySelector("[data-reference-search]").focus();
  }

  referenceToggle.addEventListener("click", () => openReference(reference.hidden));
  reference.addEventListener("click", (event) => {
    const item = event.target.closest("[data-insert]");
    if (!item) return;
    editor.replaceSelection(item.dataset.insert);
    editor.focus();
  });
  reference.querySelector("[data-reference-search]").addEventListener("input", (event) => {
    const needle = event.target.value.trim().toLowerCase();
    for (const group of reference.querySelectorAll("[data-reference-group]")) {
      let shown = 0;
      for (const item of group.querySelectorAll("[data-insert]")) {
        const hit = !needle || item.textContent.toLowerCase().includes(needle);
        item.hidden = !hit;
        shown += hit;
      }
      group.hidden = !shown;
    }
  });

  // ---- find, wrap, full screen ----
  shell.querySelector('[data-code-action="find"]').addEventListener("click", () => {
    editor.focus();
    editor.execCommand("replace");
  });

  function setWrap(on) {
    editor.setOption("lineWrapping", on);
    wrapButton.setAttribute("aria-pressed", String(on));
    store(WRAP_KEY, on ? "1" : "0");
  }
  wrapButton.setAttribute("aria-pressed", String(editor.getOption("lineWrapping")));
  wrapButton.addEventListener("click", () => setWrap(!editor.getOption("lineWrapping")));

  function setFocus(on) {
    form.classList.toggle("design-focus", on);
    document.documentElement.style.overflow = on ? "hidden" : "";
    focusButton.setAttribute("aria-pressed", String(on));
    focusButton.querySelector("[data-focus-off]").hidden = on;
    focusButton.querySelector("[data-focus-on]").hidden = !on;
    editor.refresh();
    window.dispatchEvent(new Event("resize")); // the preview scales to its new width
    editor.focus();
  }
  focusButton.addEventListener("click", () => setFocus(!form.classList.contains("design-focus")));

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (!menuPanel.hidden) openMenu(false);
    else if (!reference.hidden) openReference(false);
    else if (form.classList.contains("design-focus")) setFocus(false);
  });
}

for (const shell of document.querySelectorAll("[data-code-shell]")) init(shell);
