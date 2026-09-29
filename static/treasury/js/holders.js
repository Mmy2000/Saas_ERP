// Narrow cash box / bank account / card terminal selects to those usable at a branch in a
// currency. Options carry data-currency, and data-branch (boxes; terminals: empty = any
// branch) or data-branches ("*" or space-separated ids, bank accounts).
import { filterOptions } from "../../core/js/selects.js";

const usable = (option, currency) => !currency || option.dataset.currency === currency;

export function narrowHolders({ branch, currency, box, bank, terminal }) {
  const at = String(branch ?? "");
  const counts = {};
  if (box) {
    counts.box = filterOptions(box, (o) => usable(o, currency) && o.dataset.branch === at);
  }
  if (bank) {
    counts.bank = filterOptions(bank, (o) => usable(o, currency)
      && (o.dataset.branches === "*" || (o.dataset.branches || "").split(" ").includes(at)));
  }
  if (terminal) {
    counts.terminal = filterOptions(terminal, (o) => usable(o, currency)
      && (!o.dataset.branch || o.dataset.branch === at));
  }
  return counts;
}
