# Gweb Platform

Multi-tenant jewelry/gold ERP that replaces the per-client Gweb installations.
The design is in `PROJECT_PLAN.md`; section numbers (§) in code comments refer to it.

## What exists so far

| Area | Where | Plan |
|---|---|---|
| Settings per environment, env-driven | `config/settings/` | §27, §28 |
| Tenant context, fail-closed manager, write guard | `apps/core/tenancy/`, `apps/core/models.py` | §5.3 |
| PostgreSQL RLS (ENABLE + FORCE + policy) as a migration operation | `apps/core/tenancy/rls.py` | §5.3, ADR-002 |
| Host → tenant resolution, per-request tenant transaction | `apps/core/tenancy/middleware.py` | §5.4, §5.5 |
| `TenantCommand` (explicit `--tenant` / `--all-tenants`) | `apps/core/tenancy/commands.py` | §5.6 |
| Decimal-only numeric helpers, Money, Weight | `apps/core/numeric.py` | §6.4, §8.6 |
| Document sequences and plain counters (row-locked) | `apps/core/sequences.py` | §6.6 |
| Error types + API error envelope, pagination | `apps/core/errors.py`, `apps/core/api/` | §9.5, §11 |
| PII encryption (Fernet) + blind indexes | `apps/core/crypto.py` | §24 |
| Tenant registry, domains, provisioning | `apps/platform/tenants/` | §5.7 |
| Platform console (platform hosts, platform staff only): overview of every client (status, users, documents and sales in the last 30 days, a 14-day activity chart, health: busy / quiet / idle / not started), client list and search, create a client in one form (company, owner, plan), per-client page with usage, plan, trial end, branch and user limits (enforced in the workspace), contact and notes, company settings, extra domains, suspend / archive / activate, and an activity log of every console change. Figures are read inside each client's own tenant context. The raw Django admin moved to `/django-admin/` | `apps/platform/console/`, `templates/console/` | §5.4, §5.7 |
| Client traffic: every request to a workspace counted per client and minute (requests, server time, slow requests, 5xx, refusals) and per client, day and URL pattern; a requests-per-minute limit per client (platform default `TENANT_REQUESTS_PER_MINUTE`, 0 = none) answered with 429 + `Retry-After`; console Traffic page (clients ranked by share of server time, near-limit / limited / slow) and a per-client page with a chart, heaviest screens and the limit. `prune_traffic` deletes old counters | `apps/platform/tenants/traffic.py`, `apps/platform/tenants/middleware.py`, `apps/platform/console/traffic.py` | §5.7 |
| Global User + per-tenant Membership, login backends, session binding | `apps/iam/` | §7.2, §14.1, ADR-004 |
| RBAC: permission catalog, roles, branch-scoped assignments, limits, `Actor` | `apps/iam/` | §14.2–14.3 |
| TenantProfile, Branch | `apps/org/` | §7.1 |
| Karat/fineness conversions | `apps/catalog/domain/metal.py` | §8.1–8.3, ADR-009 |
| Currencies, metals, karats (seeded per tenant), item category tree + making charges | `apps/catalog/` | §7.3, §20.2 |
| Price boards (reference karat entered, others derived and stored), FX rates | `apps/pricing/` | §7.6, §8.3 |
| Customers, suppliers and trade accounts (one Party, many roles), phone normalization, encrypted national ID, balances | `apps/parties/` | §7.4, ADR-006 |
| User, role and branch administration (no privilege escalation, last-owner guard) | `apps/iam/`, `apps/org/` | §14.2 |
| Stock: one Item per piece (barcode, status, history), bulk lots with balances, append-only movements | `apps/inventory/` | §7.5, ADR-007 |
| Stock transfers between branches: send (pieces in transit, bulk weight out), receive, cancel while in transit; gold and making cost move through branch clearing in the ledger | `apps/inventory/transfers.py` | §7.5, §15 |
| Stocktake: snapshot a branch (optionally one category/karat), scan pieces, weigh bulk lots, post (missing pieces → metal loss, missing pieces found again → gain, bulk adjusted); variance report | `apps/inventory/stocktakes.py` | §7.5 |
| Barcode labels: label templates (roll or A4 sheet, jewellery tail tags with two wings, chosen fields, live preview), Code128 generated server-side as SVG, printing from stock/purchases/transfers/piece pages, ZPL download for Zebra printers | `apps/printing/` | §18 |
| Supplier invoices: draft → post (pieces, lots, number, ledger entry in one transaction) → cancel | `apps/purchasing/` | §7.8 |
| Scrap gold: buy from walk-ins (name, phone, encrypted ID) or customers at the scrap price less loss (override by permission), pay cash/bank/on account; sell to dealers from branch scrap stock with the gain or loss against its cost | `apps/purchasing/scrap.py` | §7.8 |
| Retail sales: scan-to-sell screen, server-side pricing engine (making-charge discount within a personal limit, never below cost), scrap trade-in, cash/card/transfer in any currency, credit sales, printable receipt, same-day vs older cancellation | `apps/sales/`, `apps/pricing/engine.py` | §7.7, §8.4, ADR-010 |
| Reservations: pieces held for a customer (not sellable, still counted as stock), optional price lock on the reservation-day board, deposits through any box/bank/terminal held on customer deposits, completion into a sale that uses the deposits, cancellation with refund or account credit | `apps/sales/reservations.py` | §7.7 |
| Customer returns (pieces back to stock, revenue and cost reversed, refund in cash or to the account, optional kept deduction) | `apps/sales/returns.py` | §7.7 |
| Receipts and payments, gold received / given (from scrap stock), gold balances settled in money, printable vouchers | `apps/settlements/` | §7.9, ADR-006 |
| Returns to suppliers: pieces and bulk weight sent back; the supplier's gold balance (fine g at today's value) and money balance (making cost paid) go down; cancellable | `apps/purchasing/returns.py` | §7.8 |
| Bulk gold by weight on the sales screen (chain by the gram, bullion): priced like a piece with the category's making rate and the lot's making cost as the floor; returns and cancellations put the weight back in the lot | `apps/sales/services.py`, `apps/pricing/engine.py` | §7.7, §8.4 |
| Wholesale to trade accounts by weight: pieces and bulk gold priced as gold at the board's sell price plus a making charge per gram (per category, list rate by default, live server quote); settled in gold (the trader owes the fine grams, and the making in money) or in money (the whole value); returns of whole lines at the sale's values; cancellable. Receipts and gold settlements work with trade accounts too | `apps/sales/trade.py` | §7.7 |
| Buying from trade accounts: a purchase invoice or return can be from a supplier or a trade account (`seller_role`); a trader's side posts to the trade accounts ledger, so wholesale sales, purchases and returns share one balance and one statement. The trader page links to "Sell wholesale" and "Buy from them" | `apps/purchasing/` | §7.7, §7.8 |
| Workshops and work orders: workshops as a party (statement, pay labour, give or take gold); send gold or scrap by weight to a workshop (its gold balance goes up), receive new pieces (barcoded, costed), bulk gold and scrap back; loss (هالك) or gain worked out per metal and booked explicitly (a gain must be confirmed); labour and the making cost carried in the gold sent become the cost of the new goods; cancel an order while out, or undo a receipt while its goods are untouched | `apps/manufacturing/` | §7.11, §8.5 |
| Repairs and custom orders: take in a customer's pieces (registered customer or walk-in name/phone) with descriptions, weights and prices; a bag number per branch; promised date; deposits held for the customer; send to a workshop or do it in the shop; mark ready with weights out (difference shown), final prices and the workshop's labour (a repair cost owed to the workshop); deliver with deposits applied, cash/card/transfer or on account, change in cash; cancel with the deposit paid back or credited. Only money is booked: the pieces are the customer's | `apps/repairs/` | §7.12 |
| Customer deposits: the customer page and statement show reservation deposits held (their own statement part, from the deposits account), with a link to that customer's reservations; the dashboard shows the total held | `apps/parties/web/views.py` | §7.7 |
| Customer and supplier statements per currency and metal: opening balance, running balance, links to documents (also for cash boxes, bank accounts, terminals) | `apps/ledger/statements.py` | §7.4, §19 |
| Reports: a registry of report classes (filters, permission per report, tables) with one screen, print and CSV for Excel: daily summary, gold balances, sales analysis with margin, expenses by category | `apps/reports/` | §7.12, §3.1 |
| Treasury: cash boxes per branch and currency (each with its own ledger account; the default box opens with the first cash sale), bank accounts (all or some branches), card terminals with fee rates; every payment records the box / account / terminal it went through | `apps/treasury/` | §7.9, §15 |
| Treasury movements: transfers and bank deposits/withdrawals, cash sent between branches (in transit through branch clearing until received), currency exchange with gain/loss, card settlements with bank fees; statements per box/account/terminal | `apps/treasury/services.py` | §7.9, §15 |
| Expenses: categories posting to expense accounts, vouchers paid from a box or bank account | `apps/expenses/` | §7.12 |
| Idempotency keys on money-moving API actions (double clicks and retries never post twice) | `apps/core/api/idempotency.py` | §11.4 |
| Multi-commodity double-entry ledger: chart of accounts, posting service, balance projections, reversals, trial balance, manual journal | `apps/ledger/` | §7.10, ADR-005 |
| Dashboard: today's retail sales (against yesterday), wholesale, cash on hand, gold in stock, sales for the last 7 days, balances with customers / traders / suppliers in money and gold, what needs attention (incoming transfers, open stocktakes, overdue reservations, unposted purchases) and today's postings; each block follows the user's permissions and branches | `apps/org/dashboard.py` | §17 |
| Web UI: Tailwind design system, RTL/LTR shell, dashboard and module pages | `templates/`, `apps/*/templates/`, `assets/` | §17 |
| Arabic / English UI, language cookie → tenant default | `apps/core/i18n.py`, `locale/`, `ops/i18n/` | §17 |
| Isolation test suite | `tests/isolation/` | §23.3 |

## Local setup (Windows / PowerShell)

1. **Database roles** (once). Pick three passwords, then as the postgres superuser:

   ```powershell
   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -d postgres -f ops/db/bootstrap.sql `
       -v dbname=SaasDB -v owner_pw=... -v rw_pw=... -v platform_pw=...
   ```

2. **Environment.** `Copy-Item .env.example .env`, then fill in the database URLs, a secret key
   and the two PII encryption keys (see the comments in `.env.example`).

3. **Dependencies and schema.**

   ```powershell
   .\venv\Scripts\python.exe -m pip install -r requirements/dev.txt
   .\venv\Scripts\python.exe manage.py migrate --database owner
   ```

4. **A tenant and a platform admin.**

   ```powershell
   .\venv\Scripts\python.exe manage.py create_tenant --slug demo --name "Demo Jewelry" --owner-username admin
   .\venv\Scripts\python.exe manage.py createsuperuser
   .\venv\Scripts\python.exe manage.py runserver
   ```

   - Tenant: <http://demo.localhost:8000/> (log in as `admin`). `--locale en` makes English the
     tenant's default language; each user can switch in the top bar.
   - Platform admin: <http://admin.localhost:8000/>.

   `*.localhost` resolves to 127.0.0.1 in modern browsers, so no hosts-file edits are needed.

## Platform console

Open `http://admin.localhost:8000/` (any host in `PLATFORM_HOSTS`) and sign in with the email
and password of a user with `is_platform_staff`. New clients get `<slug>.<TENANT_BASE_DOMAIN>`
(`localhost` by default); a custom domain added in the console must also point at the server
and be allowed by `DJANGO_ALLOWED_HOSTS`.

### Branding and uploaded files

Console → **Settings**: the platform's name, logo (and one for dark backgrounds), the line shown
under each client's name, and the footer text and links shown in the footer of every workspace
page and on the client sign-in page. A client's own logo, name on screens and accent colour are set in its workspace
(**Settings → Company**, permission `org.settings.manage`) or from the console's client page.

Uploads are kept per client under `MEDIA_ROOT/tenants/<tenant id>/` (platform files under
`platform/`) and are served only by `apps.core.media.serve`, which checks the host and the
user: a client's logo is public on its own hosts, its other files only to its members, nothing
crosses to another client, and platform staff can open any. Images are checked by content
(PNG, JPEG, WEBP, GIF, ICO; no SVG), 2 MB at most, and stored under random names. Static files
(CSS, JS, fonts) are the application's own and the same for everyone. In production, back up
`MEDIA_ROOT` with the database, and never let the web server serve it directly.

### Traffic and request limits

Console → **Traffic** shows every client's requests, server time and refusals for the last hour,
24 hours or 7 days, live (refreshed every 5 s while the tab is visible, `static/core/js/console-live.js`),
with a switch per client to disable (suspend) or enable it; open a client for its heaviest screens and to change its limit (requests per
minute; empty = `TENANT_REQUESTS_PER_MINUTE`, default 600; 0 = no limit). Over the limit the
workspace answers 429 until the next minute. Run `manage.py prune_traffic` daily to keep 30 days
of per-minute counters and 90 days of per-route ones.

## Frontend assets

Tailwind CSS v4 through its CLI (Node is needed only to rebuild; the built CSS is committed):

```powershell
npm install
npm install       # copy fonts, icons and Tom Select, then build static/core/css/app.css
npm run watch:css    # while editing templates
```

- Design tokens and component classes are in `assets/css/app.css`: `.card` (+ `-header`,
  `-title`, `-body`), `.btn` + `btn-primary` / `btn-secondary` / `btn-ghost` / `btn-brand`
  (`text-xs` makes it small), `.form-input`, `.badge` + `badge-neutral|brand|success|warning|danger`
  (`<span class="badge-dot">` for a status dot), `.table`, `.segmented` + `.segment` (radio
  toggles), `.tab` + `.tab-active` (list filters), `.kbd`, `.num`. The look is neutral zinc greys
  (the `slate-*` scale is redefined to zinc there) with gold (`brand-*`) as the accent; primary
  buttons use the accent's solid colour. Use logical utilities (`ms-`, `pe-`, `start-`, `text-start`) so one
  template serves RTL and LTR.
- Themes: light, dark or system, and an accent colour (gold, emerald, teal, blue, indigo, violet,
  rose, graphite), picked per user in the top bar's appearance menu (cookies `gweb_theme`,
  `gweb_accent`); a workspace's default accent is set in the console (Company settings). It all
  works through tokens: the `slate-*`, `brand-*` and red/amber/emerald scales are redefined for
  `[data-theme=dark]`, so templates need no `dark:` classes. Use `bg-surface` (not `bg-white`) for
  cards and panels, `text-on-brand` on `bg-brand-600`, and `zinc-*` for panels that stay dark
  in both themes (login side panel, console sidebar). Palettes live in `assets/css/app.css` and
  `apps/core/appearance.py`.
- The sidebar collapses to an icon rail on desktop (top-bar button, cookie `gweb_sidebar`). Mark
  text that hides in the rail with `data-sidebar-label`; the current screen's link uses the accent.
- Ctrl/⌘+K opens the command palette (`static/core/js/cmdk.js`): every screen in the sidebar the
  user may open, searchable in Arabic or English.
- Icons: `{% load ui %}{% icon "coins" "size-5" %}` (Lucide, bundled into `apps/core/ui_icons.json`
  by `npm run assets`; add names to `assets/copy-assets.mjs`).
- Numbers: `{{ value|num:2 }}` inside `<span class="num">` (grouped, Western digits, LTR).
- Every `<select>` is searchable (Tom Select, `static/core/js/selects.js`). `data-native` opts
  out; `data-remote="/api/v1/…"` searches the API instead of listing options.
- Forms post JSON to the API: `<form data-api-form data-endpoint=… data-method=POST|PATCH>`,
  errors appear in `[data-error-for="field"]`. The browser never does pricing arithmetic.

## Translations

English strings in code/templates, Arabic in `locale/ar/LC_MESSAGES/django.po`. GNU gettext is
not required:

```powershell
.\venv\Scripts\python.exe ops\i18n\messages.py extract   # add new strings to the .po
# translate the new entries (msgstr) in the .po file
.\venv\Scripts\python.exe ops\i18n\messages.py compile   # write the .mo
```

`tests/test_i18n_and_pages.py` fails when a string is untranslated or the `.mo` is stale.

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest                       # everything (needs the database)
.\venv\Scripts\python.exe -m pytest --e2e                 # plus browser tests (python -m playwright install chromium)
.\venv\Scripts\python.exe -m pytest apps/core/tests/test_numeric.py apps/catalog/tests/test_metal.py   # no database
.\venv\Scripts\python.exe -m ruff check .
```

Tests connect as `app_owner`, never as a superuser (superusers bypass RLS, which
`test_test_connection_is_not_a_superuser` guards against).

## Rules that the code and tests enforce

- Every tenant-owned model inherits `TenantScopedModel`, declares an index or unique constraint
  starting with `tenant`, and gets `EnableTenantRLS("<Model>")` in a migration. The suite in
  `tests/isolation/test_model_coverage.py` fails otherwise, including for auto-created M2M
  tables.
- Tenant data is only reachable inside `tenant_context(tenant_id)`. Requests, `TenantCommand`
  and (later) `TenantTask` provide it; anything else raises `TenantContextError`.
- Every API action declares `required_permissions`; undeclared actions are denied, and
  `apps/iam/tests/test_authz.py` fails. Web views use `@permission_required("code")`; the
  sidebar only shows what the user may open. Services re-check with `actor.require(...)`.
- No `FloatField`. Money, weights and rates are `Decimal`, rounded only through `apps.core.numeric`.
- Karat conversion factors live only in `apps.catalog.domain.metal`.
- Every balance comes from the ledger. `apps.ledger.services.post_entry` enforces balance in
  company currency and per metal; a deferred trigger re-checks at COMMIT, and journal tables are
  append-only in the database (corrections are reversals).
- Documents (`apps.core.models.Document`) are edited only as drafts; posting assigns the number and
  applies stock + ledger effects atomically; cancelling reverses them (and is refused once goods
  have left stock). Stock movements are append-only in the database, like journal lines.
- API JSON never contains floats: `apps.core.api.renderers.JSONRenderer` writes Decimals as strings.
- The Django admin is served only on platform hosts.
- Source files are UTF-8 without BOM (`tests/test_repo_hygiene.py`). PowerShell 5.1's
  `Set-Content -Encoding utf8` adds one; edit files with an editor or Python instead.

## Deliberately not done yet

These are Phase 2 spikes or later phases in the plan:

- RLS on the global `iam_user` table, and wiring the `app_platform` role into a DB alias.
- The same-tenant FK trigger for cross-tenant FK injection (§5.3). For now FK inputs are
  resolved through tenant-scoped querysets, which RLS already restricts.
- Celery `TenantTask`, the outbox, audit events, the tenant-prefixed cache wrapper.
- Deactivating a party or branch with a non-zero balance is not blocked yet.
- Server-side PDF of documents (WeasyPrint, a Phase 2 spike; receipts and vouchers print from the
  browser), QR codes on labels, Arabic text in ZPL (printer fonts cannot shape it),
  stones/diamond detail on pieces,
  reservations of bullion/coins not yet in stock, expiry reminders, partial receipt of a
  transfer (it is received whole or cancelled), approving stocktake differences line by line,
  RFID bulk counting, partial-weight wholesale returns; work orders: sending finished pieces
  (repairs), receiving in several batches, labour paid in gold, stones issued to setters;
  repairs: photos of the pieces, paying in scrap gold, gold added by the workshop.
- Treasury: bank reconciliation, cheques, cash counts / drawer close, FX revaluation of foreign
  cash (exchanges and transfers use average carrying value; receipts and payments in a foreign
  currency still use the rate in force), choosing among several cash boxes on the sales screen.
- Invitations for people who already have an account in another workspace (OQ-6).
- Fiscal period closing screens (the ledger already refuses postings into closed periods).
