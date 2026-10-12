// New account: picking a group fills in its next free code and normal balance; the currency
// list shows only for "one currency only", and what an account takes only for accounts.
const form = document.querySelector("form[data-account-form]");
const data = document.getElementById("account-groups");

if (form && data) {
  const groups = Object.fromEntries(JSON.parse(data.textContent).map((g) => [String(g.pk), g]));
  const field = (name) => form.querySelector(`[name="${name}"]`);
  const box = (name) => field(name)?.closest("div");

  const refresh = () => {
    const isGroup = field("is_group").value === "1";
    box("commodity_scope")?.classList.toggle("hidden", isGroup);
    box("currency")?.classList.toggle("hidden", isGroup || field("commodity_scope").value !== "currency");
  };

  field("parent").addEventListener("change", () => {
    const group = groups[field("parent").value];
    if (!group) return;
    field("code").value = group.code;
    field("nature").value = group.nature;
  });
  form.addEventListener("change", (event) => {
    if (event.target.matches('[name="is_group"], [name="commodity_scope"]')) refresh();
  });
  if (!field("code").value) field("parent").dispatchEvent(new Event("change"));
  refresh();
}
