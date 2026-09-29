// Member and role forms: request shapers plus small interactions (role scope, group toggles).
import { registerShaper } from "../../core/js/forms.js";

registerShaper("member", (values, form) => {
  const roles = [];
  form.querySelectorAll("[data-role-row]").forEach((row) => {
    if (!row.querySelector("[data-role-toggle]").checked) return;
    const allBranches = row.querySelector("[data-scope]").value === "all";
    const branches = Array.from(row.querySelector("[data-branches]").selectedOptions, (o) => Number(o.value));
    roles.push({ role: Number(row.dataset.roleRow), all_branches: allBranches, branches });
  });
  const body = {
    display_name: values.display_name ?? "",
    username: values.username ?? "",
    email: values.email ?? "",
    phone: values.phone ?? "",
    default_branch: values.default_branch ? Number(values.default_branch) : null,
    roles,
  };
  if (values.password) body.password = values.password;
  return body;
});

registerShaper("role", (values, form) => ({
  name: values.name ?? "",
  description: values.description ?? "",
  permissions: Array.from(form.querySelectorAll("[data-permission]:checked"), (box) => box.dataset.permission),
}));

document.addEventListener("change", (event) => {
  const target = event.target;
  const row = target.closest("[data-role-row]");
  if (row && target.matches("[data-role-toggle]")) {
    row.querySelector("[data-role-scope]").classList.toggle("hidden", !target.checked);
  }
  if (row && target.matches("[data-scope]")) {
    row.querySelector("[data-branch-picker]").classList.toggle("hidden", target.value === "all");
  }
  const group = target.closest("[data-permission-group]");
  if (group && target.matches("[data-group-toggle]")) {
    group.querySelectorAll("[data-permission]").forEach((box) => (box.checked = target.checked));
  }
});

// Group toggles start checked when every permission in the group is.
document.querySelectorAll("[data-permission-group]").forEach((group) => {
  const boxes = group.querySelectorAll("[data-permission]");
  group.querySelector("[data-group-toggle]").checked = boxes.length > 0 && Array.from(boxes).every((b) => b.checked);
});
