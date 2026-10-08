// WhatsApp campaigns: the new-campaign form (who it reaches, live; how the message looks;
// start), the opt-out list, and a campaign's progress (asked every 5 s while it sends).
import { api, ApiError, uuid } from "../../core/js/api.js";
import { busy } from "../../core/js/loading.js";
import { strings, toast } from "../../core/js/ui.js";

const fail = (error) => toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });

// ---- the new-campaign form -------------------------------------------------------------------
const form = document.getElementById("campaign-form");
if (form) {
  let count = 0;
  let timer = null;
  const kind = () => form.querySelector("input[name=kind]:checked").value;
  const audience = () => ({
    kind: kind(), branch: Number(form.elements.branch.value) || null,
    since: form.elements.since.value || null, numbers: form.elements.numbers.value,
  });

  const showFor = () => {
    form.querySelectorAll("[data-show-for]").forEach((node) => {
      node.hidden = !node.dataset.showFor.split(" ").includes(kind());
    });
  };

  const reach = async () => {
    const box = form.querySelector("[data-reach]");
    box.classList.add("is-refreshing");
    try {
      const found = await api(form.dataset.preview, { method: "POST", body: audience(), quiet: true });
      count = found.count;
      box.querySelector("[data-reach-count]").textContent = found.count;
      const comma = document.documentElement.dir === "rtl" ? String.fromCharCode(0x060c, 32) : ", ";
      box.querySelector("[data-reach-sample]").textContent = found.sample.join(comma);
      const skipped = box.querySelector("[data-reach-skipped]");
      skipped.replaceChildren(...found.skipped.map((row) => {
        const item = document.createElement("li");
        item.textContent = `${row.reason}: ${row.count}`;
        return item;
      }));
    } catch {
      count = 0;
      box.querySelector("[data-reach-count]").textContent = "—";
    } finally {
      box.classList.remove("is-refreshing");
    }
  };
  const later = () => {
    clearTimeout(timer);
    timer = setTimeout(reach, 400);
  };

  const sample = () => {
    const out = form.querySelector("[data-sample-message]");
    const text = form.elements.message.value.trim();
    out.textContent = text
      ? text.replaceAll("{name}", out.dataset.name).replaceAll("{company}", out.dataset.company)
      : out.dataset.empty;
  };

  form.addEventListener("change", (event) => {
    if (["kind", "branch", "since"].includes(event.target.name)) {
      showFor();
      later();
    }
  });
  form.elements.numbers.addEventListener("input", later);
  form.elements.message.addEventListener("input", sample);
  form.querySelectorAll("[data-insert]").forEach((button) => button.addEventListener("click", () => {
    const box = form.elements.message;
    const at = box.selectionStart ?? box.value.length;
    box.value = box.value.slice(0, at) + button.dataset.insert + box.value.slice(box.selectionEnd ?? at);
    box.focus();
    box.selectionStart = box.selectionEnd = at + button.dataset.insert.length;
    sample();
  }));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    form.querySelectorAll("[data-error-for]").forEach((node) => (node.textContent = ""));
    const banner = form.querySelector("[data-form-error]");
    banner.classList.add("hidden");
    if (count && !window.confirm(form.dataset.confirm.replace("{count}", count))) return;
    const done = busy(form.querySelector("[type=submit]"));
    try {
      const made = await api(form.dataset.endpoint, { method: "POST", idempotencyKey: uuid(), body: {
        name: form.elements.name.value, message: form.elements.message.value, audience: audience(),
        daily_cap: Number(form.elements.daily_cap.value) || 150,
        min_gap: Number(form.elements.min_gap.value) || 25, max_gap: Number(form.elements.max_gap.value) || 70,
      } });
      window.location.assign(form.dataset.redirect.replace("/0/", `/${made.id}/`));
    } catch (error) {
      done();
      if (!(error instanceof ApiError)) return fail(error);
      const fields = { ...error.fields, ...(error.fields.audience || {}) };
      let placed = false;
      for (const [name, messages] of Object.entries(fields)) {
        const slot = form.querySelector(`[data-error-for="${name}"]`);
        if (slot && Array.isArray(messages)) {
          slot.textContent = messages.join(" ");
          placed = true;
        }
      }
      if (!placed) {
        banner.textContent = error.message;
        banner.classList.remove("hidden");
      }
    }
  });

  showFor();
  sample();
  reach();
}

// ---- the opt-out list ------------------------------------------------------------------------
const optOuts = document.querySelector("[data-opt-outs]");
if (optOuts) {
  optOuts.querySelector("[data-opt-out-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const slot = event.currentTarget.querySelector("[data-error-for=phone]");
    slot.textContent = "";
    try {
      await api(optOuts.dataset.optOuts, { method: "POST", body: { phone: event.currentTarget.elements.phone.value } });
      window.location.reload();
    } catch (error) {
      if (error instanceof ApiError && error.fields.phone) slot.textContent = [].concat(error.fields.phone).join(" ");
      else fail(error);
    }
  });
  optOuts.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-opt-in]");
    if (!button || !window.confirm(optOuts.dataset.confirm)) return;
    try {
      await api(`${optOuts.dataset.optOuts}?id=${button.dataset.optIn}`, { method: "DELETE" });
      button.closest("li").remove();
    } catch (error) {
      fail(error);
    }
  });
}

// ---- a campaign's progress -------------------------------------------------------------------
const board = document.querySelector("[data-campaign]");
if (board) {
  const EVERY = 5000;
  const set = (name, value) => {
    const node = board.querySelector(`[data-campaign-${name}]`);
    if (node) node.textContent = value;
  };
  const show = (state) => {
    board.dataset.status = state.status;
    for (const name of ["sent", "left", "failed", "skipped", "percent", "note"]) set(name, state[name]);
    board.querySelector("[data-campaign-bar]").style.width = `${state.percent}%`;
    const badge = board.querySelector("[data-campaign-badge]");
    badge.textContent = state.label;
    badge.className = `badge ${{ running: "badge-brand", paused: "badge-warning", done: "badge-success" }[state.status] || "badge-neutral"}`;
    const actions = document.querySelector("[data-campaign-actions]");
    actions.querySelector("[data-campaign-do=pause]").hidden = state.status !== "running";
    actions.querySelector("[data-campaign-do=resume]").hidden = state.status !== "paused";
    actions.querySelector("[data-campaign-do=cancel]").hidden = !["running", "paused"].includes(state.status);
  };
  const poll = async () => {
    if (board.dataset.status !== "running") return;
    if (!document.hidden) {
      try {
        show(await api(board.dataset.campaign, { quiet: true }));
      } catch {
        // Try again on the next round.
      }
    }
    setTimeout(poll, EVERY);
  };
  setTimeout(poll, EVERY);

  document.querySelector("[data-campaign-actions]").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-campaign-do]");
    if (!button || (button.dataset.confirm && !window.confirm(button.dataset.confirm))) return;
    const done = busy(button);
    try {
      const wasRunning = board.dataset.status === "running";
      show(await api(`${board.dataset.campaign}${button.dataset.campaignDo}/`, { method: "POST", idempotencyKey: uuid() }));
      if (!wasRunning && board.dataset.status === "running") setTimeout(poll, EVERY);
    } catch (error) {
      fail(error);
    } finally {
      done();
    }
  });
}
