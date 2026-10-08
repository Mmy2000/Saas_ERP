// Entry point loaded on every page (type="module", so it runs after parsing).
import { initAppearance } from "./appearance.js";
import { initBell } from "./bell.js";
import { initCommandPalette } from "./cmdk.js";
import { initForms } from "./forms.js";
import { initLoading } from "./loading.js";
import { initPrintTables } from "./print_table.js";
import { initScrollButtons } from "./scroll.js";
import { enhanceSelects } from "./selects.js";
import { initShortcuts } from "./shortcuts.js";
import { initShell } from "./ui.js";

initShell();
initLoading();
initAppearance();
initBell();
initShortcuts();
initCommandPalette();
enhanceSelects();
initForms();
initScrollButtons();
initPrintTables();
