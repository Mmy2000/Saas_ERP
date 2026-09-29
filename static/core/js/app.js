// Entry point loaded on every page (type="module", so it runs after parsing).
import { initCommandPalette } from "./cmdk.js";
import { initForms } from "./forms.js";
import { enhanceSelects } from "./selects.js";
import { initShell } from "./ui.js";

initShell();
initCommandPalette();
enhanceSelects();
initForms();
