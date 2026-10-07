// Certificates: ask IGI about a report (a stone's, or any number) and show the answer in place.
import { api, ApiError } from "../../core/js/api.js";
import { strings, toast } from "../../core/js/ui.js";

const root = document.getElementById("certificates");

function badge(result) {
  const wrap = document.createElement("span");
  const tone = { verified: "badge-success", not_found: "badge-danger" }[result.status] || "badge-warning";
  const label = document.createElement("span");
  label.className = `badge ${tone}`;
  label.textContent = result.status_label;
  if (result.message && result.status === "error") label.title = result.message;
  wrap.append(label);
  if (result.pdf_url) {
    const link = document.createElement("a");
    link.className = "ms-2 text-xs text-brand-700 hover:underline";
    link.href = result.pdf_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = root.dataset.open;
    wrap.append(link);
  }
  return wrap;
}

async function ask(reportNo, refresh = false) {
  return api(root.dataset.checkUrl, { method: "POST", body: { report_no: reportNo, lab: "IGI", refresh } });
}

if (root) {
  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-check]");
    if (!button) return;
    try {
      const result = await ask(button.dataset.check, true);
      button.closest("tr").querySelector("[data-check-status]").replaceChildren(badge(result));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
    }
  });
  root.querySelector("[data-check-any]")?.addEventListener("click", async () => {
    const number = root.querySelector("[data-any-report]").value.trim();
    const out = root.querySelector("[data-any-result]");
    out.replaceChildren();
    if (!number) return;
    try {
      out.replaceChildren(badge(await ask(number, true)));
    } catch (error) {
      toast(error instanceof ApiError ? error.message : strings().network, { kind: "error" });
    }
  });
}
