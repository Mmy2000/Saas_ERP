// The sign-in form (templates/registration/_login_form.html): show/hide the password, warn
// when Caps Lock is on, shake an empty field instead of sending it, and a spinner with
// "Signing in…" in the button while the server checks the password.
import { busy } from "./loading.js";

const form = document.querySelector("[data-login-form]");

if (form) {
  const password = form.querySelector("input[name=password]");
  const reveal = form.querySelector("[data-reveal]");
  const capslock = form.querySelector("[data-capslock]");
  const button = form.querySelector("[type=submit]");
  const label = button.querySelector("[data-label]");
  const idle = label.textContent;

  reveal?.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(show));
    reveal.setAttribute("aria-label", show ? reveal.dataset.hide : reveal.dataset.show);
    const [eye, eyeOff] = reveal.querySelectorAll("svg");
    eye.classList.toggle("hidden", show);
    eyeOff.classList.toggle("hidden", !show);
    password.focus({ preventScroll: true });
  });

  const caps = (event) => {
    if (!event.getModifierState) return;
    const on = event.getModifierState("CapsLock");
    capslock.classList.toggle("hidden", !on);
    capslock.classList.toggle("flex", on);
  };
  password.addEventListener("keydown", caps);
  password.addEventListener("keyup", caps);
  password.addEventListener("blur", () => { capslock.classList.add("hidden"); capslock.classList.remove("flex"); });

  const shake = (node) => {
    node.classList.remove("login-shake");
    void node.offsetWidth; // restart the animation
    node.classList.add("login-shake");
  };

  let undo = null;
  form.addEventListener("submit", (event) => {
    const empty = [...form.querySelectorAll("input[required]")].find((input) => !input.value.trim());
    if (empty) {
      event.preventDefault();
      empty.setAttribute("aria-invalid", "true");
      shake(empty.closest(".login-field"));
      empty.focus();
      return;
    }
    undo = busy(button);
    label.textContent = form.dataset.busyLabel;
    form.classList.add("is-busy");
  });
  form.addEventListener("input", (event) => event.target.removeAttribute("aria-invalid"));

  // Back from the next page (browser cache): the form is ready again.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || !undo) return;
    undo();
    undo = null;
    label.textContent = idle;
    form.classList.remove("is-busy");
  });
}
