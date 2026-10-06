// Copies third-party browser assets into static/ (run: npm run assets):
//  - self-hosted fonts (Inter, IBM Plex Sans Arabic)
//  - Tom Select (searchable selects), as a classic script exposing window.TomSelect
//  - the Lucide icons the UI uses, bundled into apps/core/ui_icons.json for {% icon %}
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url)); // decodes %20 (paths with spaces)
const nm = join(root, "node_modules");

function copy(pairs, outDir) {
  mkdirSync(outDir, { recursive: true });
  for (const [src, dest] of pairs) copyFileSync(join(nm, src), join(outDir, dest));
  return pairs.length;
}

const fonts = copy([
  ["@fontsource-variable/inter/files/inter-latin-wght-normal.woff2", "inter-latin-wght.woff2"],
  ...[400, 500, 600, 700].map((w) => [
    `@fontsource/ibm-plex-sans-arabic/files/ibm-plex-sans-arabic-arabic-${w}-normal.woff2`,
    `plex-arabic-${w}.woff2`,
  ]),
], join(root, "static", "core", "fonts"));

const vendor = copy([
  ["tom-select/dist/js/tom-select.complete.min.js", "tom-select.complete.min.js"],
  ["tom-select/LICENSE", "LICENSE"],
], join(root, "static", "vendor", "tom-select"));

const icons = [
  "layout-dashboard", "store", "gem", "layers", "coins", "arrow-left-right", "users", "truck",
  "settings", "log-out", "languages", "menu", "x", "chevron-down", "circle-alert",
  "circle-check", "search", "plus", "trending-up", "trending-down", "building-2", "info",
  "clock", "shield-check", "scale", "history", "user-round", "check", "user-plus",
  "key-round", "book-open", "list-tree", "pencil", "lock", "landmark", "calculator",
  "package", "receipt", "scan-barcode", "printer", "shopping-bag", "list", "credit-card",
  "repeat", "inbox", "wallet", "receipt-text", "send", "clipboard-check", "undo-2", "bookmark", "chart-column",
  "trash-2", "wrench", "sun", "moon", "monitor", "palette", "panel-left-close", "panel-left-open", "arrow-up", "arrow-down", "grip-vertical", "copy", "redo-2", "mouse-pointer-click",
  "cpu", "memory-stick", "hard-drive", "server", "database", "activity", "triangle-alert", "arrow-down-up",
  "calendar-check", "lock-open", "banknote", "flame", "hammer", "sparkles", "file-down",
];
const bundle = {};
for (const name of icons) {
  const svg = readFileSync(join(nm, "lucide-static", "icons", `${name}.svg`), "utf8");
  const inner = svg.slice(svg.indexOf(">", svg.indexOf("<svg")) + 1, svg.lastIndexOf("</svg>"));
  bundle[name] = inner.replace(/\s*\n\s*/g, "").trim();
}
writeFileSync(join(root, "apps", "core", "ui_icons.json"), JSON.stringify(bundle, null, 1) + "\n");
console.log(`fonts: ${fonts}, vendor files: ${vendor}, icons: ${icons.length}`);
