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
| In-house production: production orders take gold, bullion or scrap out of stock into "production in progress" (stock card: used in production), optionally naming the craftsman (an employee); recording what was made creates barcoded pieces, bulk gold or scrap (any karat: scrap can be melted into new pieces), books the fine-gold loss or gain, and adds the craftsman's labour per gram to the cost of the goods against "production labour absorbed" (salaries are already an expense). What comes out is valued at what went in, so production in progress clears exactly. Cancel while in production, or undo the record while the goods are untouched; list filtered by craftsman | `apps/manufacturing/` | §7.11, §8.5 |
| Repairs and custom orders: take in a customer's pieces (registered customer or walk-in name/phone) with descriptions, weights and prices; a bag number per branch; promised date; deposits held for the customer; send to a workshop or do it in the shop; mark ready with weights out (difference shown), final prices and the workshop's labour (a repair cost owed to the workshop); deliver with deposits applied, cash/card/transfer or on account, change in cash; cancel with the deposit paid back or credited. Only money is booked: the pieces are the customer's | `apps/repairs/` | §7.12 |
| Employees and payroll: employees with a branch, monthly salary and a commission rule (a share of the making charge sold, or an amount per gram), linked to the login they sell with; advances paid from a cash box or bank account (Dr employee advances) and taken back from the next payroll (the amount can be changed per payslip); bonuses and salary deductions per month; a monthly payroll prepared and checked before paying (salary + commission + bonuses − deductions − advances), posted to salaries, sales commissions and employee advances, one per month, printable with a signature column, cancellable (advances due again, adjustments freed); a commissions page per month with sales, making charge and weight sold per seller, returns in the month taken off | `apps/hr/` | §7.12 |
| Diamonds and gemstones (a platform feature, off unless switched on for the client or its plan in the console): diamond pieces and loose stones as barcoded pieces with stone details (kind, shape, count, carats, colour, clarity, cut, lab and certificate number), a stones' cost and a label price; receiving from suppliers (gold owed in fine grams, making on the metal weight, the stones' cost on "diamonds and stones" inventory); selling at the label price with a diamond discount limit and a floor at cost, the gold part to gold sales and the rest to diamond sales and cost; returns, transfers, stocktakes and supplier returns carry the stones' cost; stone setting (loose stones into a mounting: details and cost move onto the piece, setter's labour owed or absorbed, cancellable); diamond stock and diamond sales reports. Wholesale by weight refuses diamond pieces | `apps/diamonds/` | §7.6, §7.7 |
| Customer deposits: the customer page and statement show reservation deposits held (their own statement part, from the deposits account), with a link to that customer's reservations; the dashboard shows the total held | `apps/parties/web/views.py` | §7.7 |
| Customer and supplier statements per currency and metal: opening balance, running balance, links to documents (also for cash boxes, bank accounts, terminals) | `apps/ledger/statements.py` | §7.4, §19 |
| Reports: a registry of report classes (filters, permission per report, tables) with one screen, print, PDF and CSV for Excel, grouped on the reports page (sales and customers, gold and stock, money): daily summary, gold balances, sales analysis with margin, expenses by category, and the pack below | `apps/reports/` | §7.12, §3.1 |
| Financial statements: profit and loss (sales, cost of sales, gross profit, operating expenses, operating profit, other income and losses, net profit; % of sales and the fine gold booked; against the period before, the same period last year, or month by month) and the balance sheet on any day (assets, liabilities and equity grouped as in the chart, cash boxes and bank accounts rolled up, amounts and fine grams per metal, profit not yet closed as its own equity line). Both per branch, printable and exported to Excel; year-end closing entries are left out of the profit and loss | `apps/reports/statements.py` | §7.10, §7.12 |
| Treasury: cash boxes per branch and currency (each with its own ledger account; the default box opens with the first cash sale), bank accounts (all or some branches), card terminals with fee rates; every payment records the box / account / terminal it went through | `apps/treasury/` | §7.9, §15 |
| Treasury movements: transfers and bank deposits/withdrawals, cash sent between branches (in transit through branch clearing until received), currency exchange with gain/loss, card settlements with bank fees; statements per box/account/terminal | `apps/treasury/services.py` | §7.9, §15 |
| Cheques: received from customers, suppliers, traders or workshops (on "cheques received" until cleared) and issued from our bank accounts (on "cheques payable" until the bank pays them), with due dates; deposit, clear, bounce (also after clearing), hand back, endorse to another party (e.g. to pay a supplier), cancel if recorded by mistake; lists by state with what is due this week, and dashboard reminders | `apps/treasury/cheques.py` | §7.9 |
| Bank reconciliation: per bank account, the statement date and closing balance, then tick the booked movements on the statement (cleared balance against statement balance, difference live); book bank charges or interest found on the statement from the same screen; complete when the difference is zero; history of reconciled statements, the latest can be reopened | `apps/treasury/reconciliation.py` | §7.9 |
| Cash counts: count a cash box (by notes and coins for the main currencies, or as a total) against its balance in the books; a shortage or surplus is booked to cash over and short (a reason is required), so the box holds what was counted; boxes not counted today are flagged; printable with signatures; the latest count of a box can be cancelled; the daily summary report lists the day's counts | `apps/treasury/counts.py` | §7.9 |
| Expenses: categories posting to expense accounts, vouchers paid from a box or bank account | `apps/expenses/` | §7.12 |
| Idempotency keys on money-moving API actions (double clicks and retries never post twice) | `apps/core/api/idempotency.py` | §11.4 |
| Multi-commodity double-entry ledger: chart of accounts, posting service, balance projections, reversals, trial balance, manual journal | `apps/ledger/` | §7.10, ADR-005 |
| Closing periods: months close in order once they are over, after a checklist of unfinished work (unposted purchases, stocktakes in progress, goods and cash between branches, unpaid payroll; closing anyway needs a confirmation); a closed month takes no postings and none of its entries can be reversed, so no document dated in it can be cancelled; months reopen newest first with a reason. Closing a year (all its months closed) posts one entry on 31 December moving every income and expense balance, in money and in gold, to retained earnings; reopening the year reverses it. Full history of who closed and reopened what; a dashboard reminder for finished months still open. Needs the permission for all branches | `apps/ledger/closing.py` | §7.10 |
| Dashboard: today's retail sales (against yesterday), wholesale, cash on hand, gold in stock, sales for the last 7 days, balances with customers / traders / suppliers in money and gold, what needs attention (incoming transfers, open stocktakes, overdue reservations, unposted purchases) and today's postings; each block follows the user's permissions and branches | `apps/org/dashboard.py` | §17 |
| Web UI: Tailwind design system, RTL/LTR shell, dashboard and module pages | `templates/`, `apps/*/templates/`, `assets/` | §17 |
| Arabic / English UI, language cookie → tenant default | `apps/core/i18n.py`, `locale/`, `ops/i18n/` | §17 |
| Audit trail: a database trigger records every insert, change and delete of the master data (prices, currencies, karats, categories, customers and suppliers, pieces' edits, users, roles and permissions, branches, cash boxes and bank accounts, ledger accounts, employees, label and document designs): who (the signed-in user, passed to PostgreSQL per request), when, and the old and new values; secrets masked; append-only. Owners see it under Settings → Activity, and "History" on each record | `apps/audit/` | §21 |
| Same-tenant foreign keys: a trigger on every tenant table refuses a row that points at another tenant's row (PostgreSQL checks foreign keys without RLS); the isolation suite fails when a table's guard is missing or stale | `apps/audit/triggers.py` | §5.3 |
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
   .\venv\Scripts\python.exe -m playwright install chromium   # PDFs and browser tests
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

## Production (Linux)

The same steps as above, plus the services and the web server. Everything below runs as an
unprivileged user (here `gweb`) from the project folder (here `/srv/gweb`).

1. **Database roles**: `ops/db/bootstrap.sql` once, as the PostgreSQL superuser (it creates
   `app_owner`, `app_rw` and `app_platform`). Install the PostgreSQL client tools on the app
   server too (`pg_dump` for the backups).
2. **Environment** (`.env`, readable by `gweb` only): `DJANGO_SETTINGS_MODULE=config.settings.prod`,
   `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` (`.your-domain.com`), `TENANT_BASE_DOMAIN`,
   `PLATFORM_HOSTS` (`admin.your-domain.com`), `DATABASE_URL` (app_rw), `DATABASE_OWNER_URL`
   (app_owner), `BACKUP_DATABASE_URL` (app_platform), `FIELD_ENCRYPTION_KEY`, `BLIND_INDEX_KEY`,
   `EMAIL_URL`, `DEFAULT_FROM_EMAIL`, `SITE_SCHEME=https`, `ERROR_ALERT_EMAILS`. Production refuses
   to start without the secret key and the two encryption keys. **Keep a copy of the two
   encryption keys outside the server**: they are not in the backups, and without them national
   IDs cannot be read.
3. **Install and update** (each release):

   ```bash
   python3 -m venv venv && venv/bin/pip install -r requirements/base.txt
   venv/bin/python -m playwright install --with-deps chromium   # PDFs
   venv/bin/python manage.py migrate --database owner
   venv/bin/python manage.py collectstatic --noinput             # into var/static
   ```

   Translations (`locale/*.mo`) and the CSS are committed; nothing else is built on the server.
4. **Services** (systemd units, `Restart=always`, `User=gweb`, `WorkingDirectory=/srv/gweb`,
   `EnvironmentFile=/srv/gweb/.env`):

   ```bash
   venv/bin/gunicorn config.wsgi --bind 127.0.0.1:8000 --workers 4 --timeout 120   # gweb-web
   venv/bin/python manage.py run_worker                                            # gweb-worker
   venv/bin/python manage.py monitor_server                                        # gweb-monitor
   ```

   The worker sends e-mail and WhatsApp, runs the daily reminders, the market prices and the
   nightly backup; Console → Jobs warns when none is running. `ops/wa-gateway` (Node) is a fourth
   service only for clients using their own WhatsApp number.
5. **Web server** (nginx): TLS for `*.your-domain.com` (a wildcard certificate: every client is a
   subdomain) and the platform host; `client_max_body_size 10m;`; serve `/static/` from
   `var/static/` with long caching; send everything else, **including `/media/`**, to
   `127.0.0.1:8000` with `proxy_set_header Host $host;` and
   `proxy_set_header X-Forwarded-Proto $scheme; proxy_set_header X-Forwarded-For
   $proxy_add_x_forwarded_for;`. Uploads go through the application, which checks who may see
   them: never let nginx serve `MEDIA_ROOT` directly. `/healthz/` on the platform host answers
   `{"status": "ok"}` for uptime checks.
6. **Cache**: the production settings use a file cache under `var/cache`, shared by the web
   processes and the worker on one server. With several servers set `CACHE_URL` to a shared one.

### Backups and restoring

Every night at 02:30 the worker runs `manage.py backup` (also on demand: Console → **Backups** →
Back up now, or the command itself). It writes two dated files to `BACKUP_DIR` (`var/backups`):
`gweb-db-….dump` (pg_dump custom format, every client, as `app_platform`, the only role that
sees all rows) and `gweb-media-….zip` (`MEDIA_ROOT`), and keeps the newest `BACKUP_KEEP` (14) of
each. Console → Backups lists every run and warns when the last good one is more than a day old.
**Copy `var/backups` off the server** every day (rsync, cloud storage): a backup on the same disk
is lost with it.

To restore on a new server: run `ops/db/bootstrap.sql`, then as the PostgreSQL superuser
(`pg_restore` must bypass row-level security to load the rows):

```bash
pg_restore --no-owner --role=app_owner --dbname=gweb gweb-db-20261013-023000.dump
unzip gweb-media-20261013-023000.zip -d var/media
```

then the same `.env` (with the **same encryption keys**), `migrate --database owner` (nothing
to do if the code is the same version) and start the services.

### Server errors

Every server error (a 500 for a client, or a job that crashed) is kept in Console → **Errors**,
grouped by kind and place in the code, with how many times, the first and last time, the client,
user and page of the last one and its traceback (`apps/platform/monitor/errors.py`). Mark one
resolved once fixed: it reopens if it happens again. A new or returning error is e-mailed to
`ERROR_ALERT_EMAILS`. Errors are written on their own database connection ("errorlog" alias), so
the rollback of the failed request does not erase them.

## Platform console

Open `http://admin.localhost:8000/` (any host in `PLATFORM_HOSTS`) and sign in with the email
and password of a user with `is_platform_staff`. New clients get `<slug>.<TENANT_BASE_DOMAIN>`
(`localhost` by default); a custom domain added in the console must also point at the server
and be allowed by `DJANGO_ALLOWED_HOSTS`.

### Plans

Plans live in the database (`tenants.Plan`) and are managed in Console → **Plans**: name in
English and Arabic, price and billing period, default limits (branches, users, requests per
minute), trial days for new clients, whether it is offered and which one is the default, and
its features. A client's own limits (client page) win over its plan's. Deleting a plan moves
its clients to the plan you pick in the same step; the last plan cannot be deleted. The four
original plans (trial, standard, professional, enterprise) were created by migration
`tenants.0006_plans`, so no client changed.

### Features (modules per client)

Console → **Features** (or a plan's own page) sets what each plan includes; a client's page (**Features** card) switches
one on or off for that client only, or back to its plan. A feature with no saved choice is on.
A feature owns permission prefixes (`apps/platform/tenants/features.py`); when it is off, those
permissions are not granted to anyone in that workspace, Owners included, so its menu entries,
buttons, dashboard blocks, pages ("not in your plan") and API (403) all go away together.

To add a module as a feature: give it its own permission codes (`register_permissions`), check
them in its views/API/templates as usual, and add a `Feature(...)` listing their prefix. It then
appears in the console. `tests/test_features.py` fails if a prefix matches no permission or two
features claim the same one.

### Printed documents (invoice designs)

Each client prints its documents in its own design, from one codebase (the old system kept a git
branch per client with hand-placed ReportLab coordinates). A document type turns its record into
one plain shape (`apps/printing/documents.py`: company, title, meta, party, columns + rows,
sections, totals); a design renders it:

- **Built-in layouts** (`apps/printing/templates/printing/documents/layouts/`): Classic, Modern
  and an 80 mm thermal slip. The client chooses layout, paper (A4/A5/80 mm), language (Arabic,
  English or both), colour, logo, notes, terms, columns, details and copies in
  **Settings → Documents**, with a live preview.
- **The designer** (drag and drop, clients and staff): the document is a list of blocks (company,
  logo, title, details, customer, items table, extra details, totals, text, space, line,
  signatures, 2–3 columns holding blocks). Drag blocks from the palette onto the page or into
  Layers, click a block in the page to select it, and set its properties and style: spacing and
  padding on four sides, background, border, rounded corners, alignment, text size, weight and
  colour; the table has header colours, striped rows, lines, cell padding and a list shape for
  80 mm paper. Page margins, base text size and colours; undo/redo; Classic, Modern and Thermal
  starting points. Saved as JSON in `DocumentDesign.blocks`/`page`; everything posted is cleaned
  by `apps/printing/builder.py` (known types and keys only, clamped numbers, #rrggbb colours)
  and the server writes the HTML, so it is safe for clients.
- **Custom HTML** per client and document, written by platform staff only (console → client →
  Documents), with a live preview, the classic layout as a starting point, and every earlier
  version kept. It is rendered by a separate template engine with no loaders and plain data
  only (`apps/printing/render.py`), so it cannot include server files or reach other data; a
  broken one prints with the built-in layout instead.

Document types (14, grouped as sales, purchases, money and goods): sales invoice and return,
reservation, wholesale sale and return, repair/custom order, purchase invoice, supplier return,
scrap purchase and sale, receipt/payment, expense voucher, cash and bank movement, stock
transfer. Each type has its own columns and details; its look either is its own or **follows**
another type (`DocumentDesign.follows`; every type follows the sales invoice until given its own
design, loops are refused). Following copies the look only (layout, paper, language, colour,
texts, designer blocks, custom HTML), resolved by `render.design_for()`.

**Printing report tables.** A list or report table marked `<table data-report>` gets a Print
button in the page header (`static/core/js/print_table.js`, dialog in
`templates/components/print_table.html`): pick the tables and columns (matched by header text,
remembered per page), this page or every page (`data-pages` = page count; the other pages are
fetched with `?page=n`), and A4 portrait/landscape. It prints a clean copy with the company,
title, filters and row count from a hidden frame; "Save as PDF" in the print dialog gives the PDF.

Every document page prints through one URL, `/print/<type>/<pk>/` (`?print=1` opens the print
dialog), which checks the type's permission and finds the record through the same visibility
rules as its page. Adding a type: a `DocumentType` in `documents.py` with columns, options, a
`builder`, a `sampler` and a `finder`. Pre-printed paper is not done yet.

**PDF files (server side).** `/print/<type>/<pk>/pdf/` gives the same document as a PDF in the
client's design (A4/A5, or an 80 mm roll as long as the slip; `?inline=1` opens it instead of
downloading). Reports and statements take `?format=pdf` (`/reports/<code>/`, the profit and loss
and balance sheet, customer/supplier/trader/workshop statements and cash box/bank statements):
an A4 sheet (`printing/sheet.html`) with the logo, the period, page numbers, and landscape when a
table has more than six columns. Every one of those pages has a PDF button next to Print.
`apps/printing/pdf.py` renders with headless Chromium (Playwright), which shapes Arabic exactly
like the screen: the page is loaded from a made-up origin, `/static/` and `/media/` are answered
from local files, every other request is refused and JavaScript is off, so a custom design can
neither run code nor fetch anything. Two renders at a time per process; each worker thread keeps
its browser (about 0.3 s per document once warm). Without Chromium the link shows a "use Print →
Save as PDF" page (503). Install it once per server: `python -m playwright install chromium`.

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

### Server monitoring

Console → **Server** shows the machine live (refreshes every 5 s): CPU (and each thread), memory
and swap, the app's disk and every other drive, network and disk activity, the busiest processes,
PostgreSQL size, connections and version, and history charts for 15 minutes to 7 days. Readings
over `MONITOR_WARN_PCT` (75) are marked high, over `MONITOR_CRITICAL_PCT` (90) critical.

History comes from samples. While the page is open it records one every
`MONITOR_SAMPLE_SECONDS` (10); to keep history around the clock run the recorder as a service:

```
python manage.py monitor_server            # until stopped (systemd / supervisor / NSSM)
python manage.py monitor_server --once     # or one sample a minute from cron / Task Scheduler
```

Samples older than `MONITOR_RETENTION_DAYS` (7) are deleted automatically. Needs `psutil`
(in `requirements/base.txt`).

### Opening balances and moving over from another system

**Accounting → Opening balances** (`apps/opening`, permissions `ledger.opening.*`) takes what a
business brings with it on its first day: one document per branch and cut-over date, posted as
one ledger entry of kind "opening" against the Opening balances equity account (3301).
- Customers, suppliers, traders, workshops and partners: owes us / we owe them, in any currency
  or in gold as grams of a karat (kept in fine grams, valued at the gold board in force).
- Cash boxes and bank accounts (a bank may be negative), in their own currency.
- Other branches (legacy GHBR34): what another branch owes this one or this one owes it, in
  money or gold by karat. Booked on both branches in the same entry (branch clearing against
  opening balances at each), so it is entered once, at either branch; a second entry between the
  same two branches is refused while the first stands. The branch statement report shows it.
- Stock by weight (scrap, bullion, bulk categories) per karat, with its making cost.
- Barcoded pieces from **Excel or CSV** (Import pieces from Excel): columns found by their
  English or Arabic header in any order; category by code or name, karat as 21 or 21K, supplier
  by code or name. Pieces keep their old barcodes (labels already printed still scan). "Check the
  file" runs the whole posting and rolls it back, listing every problem by row and column;
  nothing is saved until the file is clean. A template with the shop's own categories, karats
  and suppliers is downloadable. Up to 5,000 rows per file.
- Cancelling reverses the entry and takes the pieces and weights back out; it is refused once a
  piece has been sold or moved.

### Chart of accounts, money in and out, currencies

**Accounting → Chart of accounts** (permission `ledger.accounts.manage`; legacy Acc GHG11-GHG15,
a fixed five-level tree there): add an account or a group under any group, at any depth. The code
starts with the group's code and the next free one is suggested (5 → 5402, 5402 → 540201); the
type comes from the group, the normal balance can be set, and an account takes money in any
currency, one currency only (a dollar loan), money and gold, or gold only. Any account can be
renamed (a standard one goes back to its translated name when the name is cleared). An account
can be switched off once nothing is left on it, unless the postings use it (a role), it is a cash
box, bank or card terminal (managed in Treasury), an active expense category points at it, or
active accounts sit under it; only accounts added here and never posted to can be deleted.
Migrations that add a standard account later must find it by role and take a free code.

**Treasury → Money in and out** (`apps/treasury/vouchers.py`, permission
`treasury.voucher.create`, cancelling `treasury.void`; legacy Cash GHV11/GHV12 "ايداع / سحب" and
Bnk GHB13/GHB14, single-sided there): money put into or taken out of a cash box or bank account
against a chosen account: the owner adding cash, a loan, interest or other income, a payment that
is not an expense. Customers, suppliers and partners keep Receipts & payments, running costs
Expenses; the accounts the stock, documents and closing keep (inventory, branch clearing, cheques,
cost of sales, metal gains and losses, employee advances…) are not offered. A balance sheet account
keeps the currency; income and expense accounts are booked in the company currency. Numbered
`MV`, printable, listed with search and totals, shown on the daily summary.

**Catalog → Currencies** (permission `catalog.currency.manage`; legacy Cod FC1): add a currency
by its ISO code (common Arab and world currencies are suggested, with their names translated
and symbols), rename it or switch it off. The company currency always stays on, and a currency
with active cash boxes or bank accounts cannot be switched off. Amounts keep two decimals in
every currency. Rates are published under Pricing → Exchange rates as before.

### Advances repaid, expense refunds, gold losses, parcels returned, moved and counted

- **Advance repaid or settled** (employee page; legacy GHE20 مرتد سلف, GHE23 التسوية): the
  employee pays back part of their advances in cash or by transfer (`ER`), or a settlement lowers
  or raises what they owe with no money moving, against balance adjustments, with a reason
  (`ES`). Never more than they owe; the next payroll takes back what is left. Cancelling a
  repayment makes it owed again; an advance already paid back cannot be cancelled.
- **Expense refunds** (expense page → Money back; legacy GHM13 مرتد المصروفات, which was not
  tied to the expense): all or part of an expense back into a cash box or bank account (`XR`),
  valued in proportion to the voucher and never more than is left of it. The expense list shows
  refunded and net, the expenses report has refunded and net columns, the daily summary lists
  refunds; an expense with refunds is cancelled only after them.
- **Gold received with a loss** (Receipts & payments → Gold received; legacy GHSH15a خسية): grams
  (or a per cent that fills them) taken off for impurities; the party is credited, and the scrap
  stock takes, the net weight. **Scrap bought or sold on a trade account**: the scrap screens take
  a trade account as well as a customer or supplier, and "on their account" uses its balance.
- **Stone parcels** (none of which Gweb did for parcels; it returned, moved and counted single
  stones only): *Return to the supplier* (`PX`) at the average cost, or an agreed price with the
  difference to cost of diamonds sold, off the supplier's balance or refunded in cash;
  *Move to another branch* (`PT`) into a new parcel there (same description, code + branch
  code) or an existing one of the same kind, the cost through branch clearing; *Count parcels*
  (`PC`): weigh a branch's parcels and count their stones, missing stones leave and stones found
  come in at the average cost against stone losses. Each can be cancelled.

### Stone grades, diamond labels, labels by range, customer follow-up, supplier totals

- **Diamonds → Grades** (`apps/diamonds/grades.py`; legacy GHZ12 درجات النقاء, GHZ13 الوان
  الحجار, GHZ14 Cut): each business's colour, clarity and cut lists, starting from the GIA
  scales: add, rename (unused codes only), reorder, switch off, delete unused ones. Entering
  stones offers them, and a value typed in another case is stored as the listed code ("vs1" →
  "VS1"); other values are kept as typed so Excel and old records still load. The diamond stock
  filters by colour, clarity, cut, shape and total carats (Gweb's diamond stock had none of these).
- **Diamond label fields** (Settings → Labels): main stone (count × ct, colour, clarity), colour /
  clarity, cut, certificate (lab and number) and all stones, from the piece's own stones, on HTML
  and ZPL labels; Gweb's diamond labels printed carats only.
- **Labels by barcode range or by filters** (legacy GHD29 من والي, GHD50 لفرع): the stock list takes
  a branch and a barcode range (codes of the same length), and "Labels for all N pieces shown"
  prints everything it shows, at most 500 at a time, only from the branches the user may see.
- **Customer follow-up** (Customers → Find customers to follow up; legacy GHC11, GHC24, GHC26):
  each customer gets a class (VIP, A, B, C) and a city; the list filters by class, city, home branch,
  gender, a birthday or anniversary in the next 7 or 30 days, bought in the last / not bought for
  N days, bought at least, and they owe us / we owe them, shows the last purchase, total bought and
  balance, and exports what it shows to CSV for Excel (`apps/parties/crm.py`).
- **Totals on their invoice** (purchase form; Gweb had none): pieces, weight, fine gold and making
  charges printed on the supplier's invoice, typed in and compared with the lines as they are
  entered and on the invoice page ("Matches" / "Differs"); differences are shown, not blocked.

### Advances and orders reports, parcel deals, diamond models, walk-ins made customers

- **Reports → Employees' advances** (`apps/reports/pack3.py`; legacy GHE22): per employee, the
  balance before the period, advances given, paid back, settlements and payroll take-backs, and
  what they owe now; choosing an employee gives their statement with a running balance.
- **Reports → Orders by weight** (legacy GHC30 lists): bullion, coins and other gold reserved by
  weight: what open orders still need in stock per category and karat (and 21K), and every order
  with its customer, due date (overdue flagged), quoted value, deposit and state (open, delivered,
  cancelled).
- **Reports → Parcel stones bought and sold** (Diamonds feature, `diamonds.report_parcels`):
  per parcel carats and amounts bought, sold (with the cost and margin) and returned to
  suppliers, and every deal.
- **Diamond model / style** (`Item.model_code`; legacy الموديل): typed when receiving (earlier
  models suggested) or on the piece, shown and searched in the diamond stock, printable on labels.
- **Make a customer** (a sale's page; walk-in buyer with a name or phone): finds the customer
  that already has the phone or opens one, and puts every walk-in sale with the same phone (any
  spacing or +20 form) on them. Walk-in sales were settled at the till, so no balance moves.

### Report filters and reports from the third pass

- **Goods filter** (All goods / Gold / Diamonds and stones) on Net sales, Profit per piece and
  Salespeople; for diamonds Net sales adds net carats and Salespeople ranks by net sales (no
  21K grams). **Diamond sales** now deducts returns (net figures, with what came back in its own
  column). **Diamond stock** has a by-branch table (diamond pieces and loose stones side by side).
  Legacy GHD24/25/27/33/36/43, GHZ25/31/32/33.
- **Payroll** (`apps/reports/pack4.py`, GHE21): any months, per employee and per month: salary,
  commission, bonuses, deductions, advances taken back, net pay.
- **Cash and bank balances** (GHV23/24, GHB23/24): every cash box and bank account at the end of
  any day, per branch, in its currency and in the company currency.
- **Purchases against their invoices** (GHS47): purchases with the supplier's own totals typed
  in, entered − theirs per pieces, weight, fine gold and making; only those that differ, or all.
- **Label price and discount changes** (GHD45): from the audit trail, every change to a piece's
  label price or discount class: before, after, who and when.
- **Stones set into pieces** (Diamonds feature, GHPR22): settings in a period with the piece,
  setter, gold before and after, carats, stones' cost and labour.
- **Daily summary** over several days (Until) and a shift's hours (From / To hour; GHED11,
  GHS64): with hours, documents and cash movements count by the time they were posted.

### Stones on invoices, printed work orders, cross-currency settling, agreed statements

- **Stones on the sales invoice** (GHD15 / GHZ17): a Stones column prints each stone of a
  diamond piece or loose stone ("1 × 0.5 ct D VS1 Excellent · GIA 2141438171 + 12 × 0.24 ct").
  It is left out on invoices with no stones, and the client can switch it off in
  Settings → Documents.
- **Work order document** (GHSH14): Print and PDF on a work order: gold sent and pieces, bulk
  gold and scrap back, fine gold each way, loss or gain, labour, and handover signature lines.
  It has its own design under Settings → Documents.
- **Paying against a balance in another currency** (GHSS33 / GHSS34): on a receipt or payment,
  "Against their balance in" plus an agreed rate (units paid for one of theirs). For example,
  5,000 EGP at 50 takes 100 USD off a trader's dollar balance. The cash or bank side stays in
  the currency paid. The party line moves their currency by the agreed amount, valued at what
  was paid. Shown on the settlement page and the printed receipt.
- **Agreed balances** (Suu2_Match, GHSS40 / GHSS41): on every statement (customers, suppliers,
  traders, workshops and partners), people who may edit that party save the balance the party
  confirmed as of a date, with who agreed and a note. Each agreement is checked against the
  ledger every time the statement opens. If a back-dated document or a void changes the balance
  as of that date, it shows "Changed since agreed" and the new figure. Agreements are in the
  audit trail.

### Diamond details: natural or lab-grown, setting types, model classes, stone codes

- **Natural or lab-grown** (legacy Fd1 type): set on each stone. It is shown in stock lists
  ("Lab-grown"), on the sales invoice's Stones column and as a label field. The diamond stock
  can be filtered by it.
- **Setting types** (GHD13a: prong, bezel, pavé…) and **model classes** (GHD9: several per
  piece, e.g. "Rings", "Solitaire") are managed lists on Diamonds → Grades and lists. Setting
  types start on the usual list; model classes start empty. The diamond stock can be filtered
  by both. A value already used can only be switched off.
- **Stone codes** (GHD11, Diamonds → Stone codes): a coded description (kind, origin, shape,
  colour, clarity) with a reference price per carat in a currency. Picking a code on a stone
  row fills the stone. The stone keeps the code's price per carat at that moment, so later
  price changes don't rewrite old stones. The piece page shows the stones' reference value.
  When receiving, the stones' cost follows the codes' prices until it is typed in, as long as
  the codes are in the invoice currency.

### Blind counts (Goods → Blind counts)

Legacy GHS45a / GHS45b (مطابقة جرد اصناف الفرع), `apps/inventory/blind_counts.py`:
- **What is counted:** the counter picks a branch and counts by category or by main group
  (the top of the category tree). They type the pieces per karat, the bulk gold by weight
  (scrap, bullion, bulk lots) and the cash in the company currency, without seeing the books.
  Any of the three parts can be left out.
- **Matching:** pieces must match exactly, weights to 0.01 g and cash to the whole unit, as in
  Gweb. "Check and save" keeps every check under its own number (BC), matched or not, with
  what was typed and what the books held at that moment.
- **Who sees what:** figures that differ are marked in red. Only people with "See the real
  figures in blind counts" (`admin.blind_count.reveal`, owners by default) see the books next
  to them.
- **Recounting and stock:** "Count again" starts from the typed figures. Nothing is posted: to
  correct stock, use a stocktake.

### Casting bars (work orders)

Legacy GHSHH15 / GHSHH24 (سبك):
- **Receiving bars:** when a work order (usually of kind Casting) receives into a by-the-piece
  category of the Bullion and coins family, each bar is entered with its weight, its fineness
  (‰, e.g. 995.0 or 999.9) and the caster's serial number.
- **The bar pieces:** each bar becomes its own barcoded piece. Its fine gold comes from its own
  fineness, so the fine gold received and the loss are exact. The fineness is kept on the piece,
  and the serial is its supplier code (searchable, shown on the piece page).
- **Labels and fees:** labels can print both (fields "Fineness (bars)" and "Serial no.
  (bars)"). A receipt line can carry a fixed charge (the casting fee) instead of labour per
  gram. It is owed to the workshop like any labour and paid from its statement.

### Photos and the catalogue (Goods → Catalogue)

Legacy GHMRK2 / GHMRK4 (كتالوج الذهب / الماس), `apps/inventory/catalogue.py`:
- **Photos:** a piece's page takes a photo, and a category's page takes one for its pieces
  without their own. Both need the permission "Add and change photos of pieces and
  categories". Photos are PNG, JPEG, WEBP or GIF up to 2 MB, kept per client and served only
  to its members.
- **The catalogue:** pieces in stock with their photo and today's price, filtered by branch,
  category, karat, weight and price, and with or without a photo (to find pieces still to
  photograph). Gold is priced like the sales screen before discounts or offers; diamond pieces
  show their label price. "A5 catalogue (PDF)" prints the same list (up to 300 pieces), two to
  a row, with the shop's name and logo.

### Bank money to another branch

Legacy GHBR45-48 (تحويل نقدية بنك لفرع، استلام، الخزينة الوسيطة), on Treasury → Movements →
New, as a Transfer:
- **From a bank account:** the movement has a "From branch". Money sent to a cash box at
  another branch waits in branch clearing until that branch receives it.
- **Into a bank account:** the movement has a "To branch". Choosing the same bank account with
  another branch moves that account's balance from one branch to the other, in two steps as
  well.
- **Receiving and printing:** it shows under In transit until the receiving branch confirms on
  the movement's page. The printed movement shows both branches and serves as the receipt.
- **Unchanged:** a bank withdrawal into a box without a "From branch" is still booked at once
  where the box is.

### WhatsApp usage

Legacy WhatsappDashboard, `apps/messaging/usage.py`:
- **Console → WhatsApp:** the hosted service's own account, read live with the platform's key
  (`GET /accounts/profile/` and `/accounts/messages/`): messages sent, the quota and what is
  left, with a bar that turns amber at 75% and red at 90%. Below it, every client with WhatsApp
  switched on (or with messages), showing this month's and last month's messages by route
  (WhatsApp service, the shop's number including campaigns, WhatsApp links), those not sent,
  and the last message.
- **Settings → Sent messages:** each shop sees its own WhatsApp count for this month by route,
  and last month's total.

### Diamond category card (Reports → Diamond category card)

Legacy GHD51 (كرت الصنف ماس), Diamonds feature, report `diamond_card`:
- **The tables:** for diamond pieces and loose stones, the opening, what came in and went out
  by kind of movement, and the closing per category, in pieces, carats and cost (making plus
  stones). Then every movement of the period, with links to the document and the piece and a
  running balance in carats. Filters: dates, branch and category.
- **A limit:** carats and cost are the piece's current ones. A piece whose stones changed later
  (set or taken out) shows today's figures on its earlier movements.

### Automatic gold prices (market feed)

The platform reads the Egyptian market price every `MARKET_POLL_MINUTES` (3) from iSagha, Gold
Bullion and eDahab (`apps/platform/market/sources/`, stdlib HTTP, parsers ported from Gweb's
`Cod/gold_sync`) in one background job for every client, instead of each shop scraping on its
own. Each source keeps its health (last success, failures in a row, last error, time taken); a
quote is stored only when its prices change, and implausible pages are refused (no 21K, buy
above sell, karats out of order, a 21K price outside 500–100,000). The price to follow is
cross-checked: a source silent for 20 minutes is not used, and with three or more answering,
one more than 1.5% from the median is set aside. Console → **Market prices** shows each source,
the price in use, the agreement, switches sources off and changes their order.

Each client chooses on **Gold prices → Market prices** (feature "Automatic gold prices",
permission `pricing.feed.manage`): follow automatically or not, the source (or the best
available), amounts added per gram to the market's 21K sell, buy and scrap-buy prices, rounding
(0.01 / 1 / 5 / 10 / 25), the minimum move worth publishing, shop hours, silver following the
999 price, and the jump (default 3%) above which a new price waits for approval (dashboard
reminder, Approve and publish / Keep my prices). "Update now" publishes at once; "Pause until
tomorrow" stops it for the day. Boards published this way are marked "Automatic" with the market
price and source in their note; other karats are derived by fineness as before.

### Partners (owners and investors)

**Customers and suppliers → Partners** (permissions `parties.partner.*`, legacy GHP11–GHP24): each
partner has a running account on Partners' capital (3101) in any currency and in gold (fine
grams). Money put in or taken out, gold brought in or taken out, and a gold balance paid in money
("settle in gold value", legacy GHP15) all go through **Receipts & payments** with "Partner";
opening balances through Opening balances; the statement (with PDF, e-mail and WhatsApp) and the
balances list work as for the other parties. Negative = what the business owes the partner.

### Selling prices, offers and cashback, bullion

**Pricing → Price changes** (permission `inventory.price.change`): change the making charge per
gram of many gold pieces at once (or the label price of diamond pieces): choose branches,
item categories, karats, suppliers, a weight range or a list of barcodes, then set a value, add
an amount or change by a percentage. "Show the pieces" previews the count and a sample; nothing
is saved until "Change the prices" (up to 20,000 pieces, idempotent). Every change keeps each
piece's old and new value; "Undo this change" puts them back, leaving alone pieces changed again
since. A single piece's making charge is edited on its stock page, which shows its offer and its
earlier changes.

**Pricing → Offers** (feature "Offers and cashback", permissions `sales.offer.*`): between two
dates, on chosen pieces (same filters), either a discount on the making charge (a percentage or a
rate per gram) or cashback (an amount per piece or a percentage of the price). The sales screen
applies them on its own (the lowest making wins, never under the making cost); cashback goes on
the customer's account (Dr Promotions and cashback 5208, Cr customers) and is taken back by a
return; a sale without a customer gets none. Each offer shows what it gave (lines, weight,
saving, cashback).

**Bullion and coins**: "Buy scrap" also buys bullion and sealed coins (bulk categories of the
bullion/coin family) at today's buying price, without loss, into their own lot and gold
inventory. A reservation can order bullion or coins by weight even if they are not in stock
("Order by weight", priced like the reserved pieces, with offers, and locked when the price is locked); the
dashboard says when ordered gold is in stock, and completing the reservation sells it from the
lot.

### Balance transfers, adjustments, unpaid invoices, visits and sale codes

**Receipts & payments** has two more kinds (permissions `settlements.transfer.post`,
`settlements.adjust.post`):

* **Balance moved to another account** (legacy GHC29, GHSS17): money in any currency, or gold
  by karat and weight, taken from one account (customer, supplier, trader, workshop or partner)
  and given to another (Dr the first, Cr the second). No cash moves; e.g. a customer's credit
  pays another customer's debt, or a customer's balance moves to their supplier account.
* **Balance adjustment** (legacy GHC25, GHSS36, GHP17, GHSHH17): one screen for every kind of
  account, in money or gold, raising or lowering what they owe against "Balance adjustments"
  (5303, other income and losses), with a required reason.

**Reports → Unpaid invoices** (legacy GHC27, done properly): what each customer or trader owes
is their balance in the books; payments, returns and credits clear the oldest charges first, so
the open items are the newest invoices (or repairs, opening balances, adjustments) adding up to
the balance, each with its age and a link, then the balance of every account split into up to
30, 31–60, 61–90 and over 90 days, on any date. Cancelled documents are left out.

**Reports → Customer visits** (legacy GHC34): built from sales, returns, payments, gold received
or given, reservations, repairs left and collected, and scrap bought; a visit is one person on
one day; walk-ins are matched to an account by phone, else kept by phone or name. Shows the
visits and value per person, new (first visit ever in the period) or returning, every visit
with a link, and "Not back lately": people who came in the year before but not in the last 90
days.

**Sale codes** (feature "Sale confirmation codes", off unless switched on for the client;
legacy GHC16/GHC30 OTP): before a sale left on the customer's account, a reservation pick-up
or any sale above an amount (Settings → Sale codes, `sales.code.manage`), a 6-digit code goes
to the customer's phone and the seller types what the customer reads out. The code is for that
customer, branch and exact total, valid 5 minutes, 5 tries, a new one at most every minute and 3
sends; only a keyed hash is stored; the sale uses it once. `sales.code.skip` sells without it and
the invoice says "Code skipped". Codes go out through `CODE_CHANNELS` in order: `whatsapp` (the
WhatsApp gateway Gweb uses: `WHATSAPP_GATEWAY_URL`, `WHATSAPP_GATEWAY_KEY`,
`WHATSAPP_GATEWAY_ENDPOINT`), `sms` (`SMS_API_URL`, `SMS_API_KEY`, `SMS_SENDER`) or `console`
(development and tests: logged, not sent).

### Reports pack

Ported from Gweb's report screens, computed from stock movements and the ledger instead of
daily snapshots. Weights are also shown as 21K equivalent (fine gold ÷ 0.875); returns count on
the day they came back at the figures of the original sale.

* **Profit per piece** (GHS29, GHS33, GHS87): every piece sold with price per gram, gold at
  the day's selling price, making charged, making and stones cost and profit; "sold at a loss"
  only; returns taken off; net by karat with sales per 21K gram.
* **Net sales** (GHS38, GHS62): sold, returned and net by karat, category, branch,
  salesperson or day. **Salespeople** (GHS26, GHS27, GHS43, GHS81): ranked by net 21K grams,
  with returns, net profit, profit % and profit per gram.
* **Customer analysis** (GHC18, GHC26): what each customer bought and returned, last purchase,
  and what they owe now in money and gold.
* **Scrap gold** (GHS48, GHS82): scrap and bullion bought and taken in on sales per karat,
  paid per 21K gram against the day's selling price; scrap sold with its gain; scrap on hand.
* **Buy and sell coverage** (GHS83): gold sold for money (retail less returns, wholesale for
  money, scrap sold, gold balances settled in money) against gold bought back (scrap, bullion,
  trade-ins, fixings): average prices, spread, covered grams and their profit, open position,
  per source and day by day.
* **Gold position** (GHS86): the shop's own gold on any day from the books: stock, scrap, at
  workshops, in production, between branches, plus what others owe, less what it owes.
* **Category card** (GHS49, GHED12): opening, in and out and closing per category and karat,
  totals per kind of movement, and every movement with its document and a running balance.
* **Branch statement** (GHBR35, GHBR36): money and gold each branch is owed by the others,
  and a branch's statement with running balances.
* **Purchases** (GHS21, GHS35, GHD21, GHD46): what was bought in a period by supplier and by
  category and karat (pieces, weight, 21K, making cost and making per gram), with goods
  returned to the supplier taken off; filters for supplier and gold or diamonds.
* **Wholesale sales** (GHSH23, GHSH29, GHSH25): sales to traders per trade account: weight,
  fine gold, making, money charged, what they returned and the net.
* **Customer returns** (GHS32, GHD28, GHD38, GHZ26): pieces brought back by category and
  karat, what was paid, kept and refunded, and the refunds per refund method.
* **Balances** (GHSS22, GHSS23, GHP23, GHP24, GHSHH22, GHSH22, GHC19): what each supplier,
  customer, trader, workshop or partner owes or is owed today: money per currency, gold in fine
  grams valued at today's buying price, and the net in the company currency. Each name opens
  its statement; "they owe us" and "we owe them" filters.
* **Transfers between branches** (GHBR31-33): goods sent per route and karat (pieces,
  weight, fine, 21K) and how much is still on the way.
* **Workshop and production loss** (GHSHH25, GHPR21): work orders and in-house production
  received in a period: fine gold sent and returned, loss and gain, loss per cent and labour,
  per workshop or craftsman and per order (each order links to its page).

Document numbers in report tables stay on one line (column kind `code`).

### Setters (diamonds)

**Diamonds → Setters** (legacy Install GHN1–GHN4, permission `diamonds.setting`): hand
mountings (gold or diamond pieces) and loose stones to a setter (a workshop). They leave stock
("at workshop") and their value moves to "Goods at workshops" at the gold value of that day,
so they come back at the same value. Back from the setter: **set** (a mounting and stones of
the job become one piece: the stones' details and cost move onto it, it is re-weighed, the
labour is owed to the setter and added to its cost; a gold mounting becomes a diamond piece of
the chosen category, with a label price) or **unused** (back to stock unchanged). A job closes
when nothing is left with the setter. A setting from a job can be cancelled while the piece is
untouched (everything goes back to the setter). On any stone setting, **Take stones out**
(GHN4 "فك") puts chosen stones back in stock as the same loose stones (barcode, details and
cost), re-weighs the piece and books an optional fee to the setter; Gweb lost the stones' cost
and deleted the piece.

**Diamonds → Discount classes** (legacy Offd1, GHD10 / GHD42; `diamonds.manage` to edit):
each class has a code, a name, a usual discount and a largest discount. A piece gets its class
when received, on its page, or in bulk from the diamond stock (every piece shown with the
current filters, including a class filter). On the sales screen and in reservations a piece
with no discount entered starts at its label price less the class's usual discount; a seller
may give up to max(usual, min(their own diamond limit, the class's largest)), never below what
the piece cost. Pieces without a class work as before.

**Diamonds → Certificates** (legacy GHD52, certificate cards GHD15/GHD19/GHD39): stones with a
lab and a report number, searchable by number or barcode. **Check with IGI** asks the lab for
the report (the public PDF at `IGI_PDF_URL`, numbers longer than ten characters also cut to
ten; the data at `IGI_API_URL` with `IGI_API_KEY` when it answers) and keeps the answer and the
PDF (`CertificateCheck`), so the lab is asked again only on request; any number can be checked
before buying a stone. **Certificate cards**: credit-card certificates (85.6 × 54 mm, one per
page, printed or as PDF) with the shop, the piece, its gold weight and each stone with its
report; from a piece's page, from a sale (with the customer's name) or for the diamond pieces
shown in stock.

**Diamonds → Buy old diamonds** (legacy GHD40 "شراء ماس كسر", permission `diamonds.buy_back`):
old diamond jewellery and loose stones bought from a customer (paid in cash, by bank or on
their account) or a walk-in (name, phone, encrypted national ID; cash or bank). Gweb only noted
the weights and the price; here each thing becomes a barcoded piece in stock with its stones
(described now, completed on the piece later), to sell again or to take its stones out. The
form shows the gold in each piece at today's value as a guide; the price is split into the gold
(at most that value, on gold inventory through the metal position, like scrap) and the stones
(the rest, on "diamonds and stones"). Cancellable while every piece is untouched in stock.

**Diamonds → Stone parcels** (legacy برتيتات: Ft1/GHZ11, GHZ16, GHZ71): small stones kept by
weight without a barcode each. A parcel (code, branch, kind, shape, size, colour, clarity, sale
price per carat) holds carats, a count and their cost; the average cost per carat is what leaves
it (Gweb had none). Bought into from a supplier (on account, cash or bank: Dr diamonds and
stones), sold by the carat to a customer or a walk-in (diamond sales; cost of diamonds sold at
the average), and taken into a piece in a stone setting or when a piece comes back from the
setter (the cost moves onto the piece, the same account). Every change is a movement; a
cancelled sale or setting puts back exactly what left.

**Diamonds → Stones at workshops** (legacy Prod GHPR14 سحب احجار, GHPR15 رد حجارة, GHPR16
تنزيل الاحجار المستخدمة, report GHSHH23): stones from a parcel handed to a workshop or setter by the
carat. Each workshop holds a balance per parcel, at cost. Handing out moves the parcel's average
cost to "stock at workshops". The stones can come back into the parcel, be set into a piece by that
setter (pick "Stones the setter holds" on the setter job, and the piece takes them at the cost they
were handed out at), be charged to the workshop's account, or be written off to Stone losses (5304,
new). Every step is a document (SH) and can be cancelled while the stones are still where it put
them. Gweb kept only weights and counts with no posting, and its balance checks ignored the
workshop and the parcel. The parcel page lists who holds its stones.

**Goods → In transit** (legacy الخزن الوسيطة, one screen per kind in Gweb): every stock transfer
and cash transfer between branches not received yet, by branch and direction (coming / sent),
with days waiting, totals by karat (scrap shown apart) and cash per currency. The dashboard's
"waiting to be received" items open it on the user's own branch.

### Searching the lists

Every document list shares one filter bar (`apps/core/listing.py` ListQuery, templates
`components/list_filters.html`, `list_totals.html`, `pager.html`): a search box (number, names,
phones, barcodes, references as fits the list), a date range, the branch (only branches the member
may see), the list's own choices (status, type, payment, kind…), and a totals strip for what is
shown (cancelled documents not added up). Paging and "Print all pages" keep the filters. A link
with `?party=<id>` (or `?customer=` on repairs and reservations) narrows a list to one customer,
supplier, trade account, workshop or partner and shows it as a chip to remove; each party's page
links to "See their" sales, returns, reservations, repairs, purchases, wholesale and receipts &
payments. Lists covered: Sales, **Returns** (new: every customer return, by goods family too),
Reservations (also pieces / gold ordered by weight / diamonds), Wholesale, Repairs, Receipts &
payments (receipts and payments totals, gold in and out), Purchases (weight, making cost), Returns
to suppliers, Expenses, Transfers, Treasury movements and Buy old diamonds. This replaces Gweb's
look-up screens (استعلام) and the list-style reports such as GHC28, GHC31-33, GHSS39-40,
GHSS26/27/30, GHFX21/22, GHM21, GHD35/41 and GHS71-74.

### Notifications, sign-ins and shortcuts

**The bell** (legacy Notifications app) in the top bar shows the unread count, refreshed every
minute while the tab is open, and opens on what needs attention (the dashboard's list, live)
above the latest notifications. A notification is written in the same transaction as the work it
reports, to each member holding its permission in that branch, never to the person who did it:
goods or cash sent to your branch (marked read for everyone once received or cancelled), new gold
prices (only the latest stays unread, so a market feed does not flood it), a price jump waiting
for approval, and at 08:00 each morning (job `notifications.morning`) repairs promised, reservations
ending and cheques due that day, one per branch. Text is stored as facts and written in the
reader's language. Each member chooses what to receive on the **Notifications** page (Gweb's
per-user flags were set by the admin); a lost permission hides its kinds. Read ones go after 90
days, unread after a year. Unlike Gweb, it does not need a separate "notifications enabled" switch.

**Settings → Sign-ins** (owners: `admin.signins.history`; legacy AuthLog) lists every sign-in,
wrong password (with the name typed, which Gweb did not keep) and sign-out, with the address and
browser, filters, and a warning when there were failed attempts in the last 24 hours. Users →
Last sign-in now means this workspace (it was the global account's). In production the address is
the last entry of `X-Forwarded-For` (set `SIGNIN_IP_HEADER` for another proxy header).

**Shortcuts** (legacy ShortCuts): the star next to the branch name pins the current screen. Pinned
screens, up to 12, appear under Dashboard in the sidebar and as tiles on the dashboard (× to
remove), and only while the member may still open them.

### Mobile app (stock and stocktake)

The existing phone app (`D:\work\Gold-inventory`, Expo / React Native, made for Gweb) works
against this platform **unchanged**: `apps/mobile` answers at Gweb's own addresses and in Gweb's
field names, on each client's own domain (the app asks for the domain). Feature "Mobile app"
(off unless switched on), permission `mobile.signin` (Managers have it; Viewers do not).

| The app calls | Here |
|---|---|
| `POST /api/auth/login/` `refresh/` `logout/`, `GET me/`, `POST change-password/` | Bearer tokens: a 7-minute JWT access token (`exp` read by the app; carries the client and the device) and a 30-day refresh token that rotates; one row per phone (`MobileDevice`) |
| `GET /Sup/api/inventory/` (`search`, `karat`, `branch`, `page`, `page_size`), `summary/`, `branches/`, `<barcode>/` | Pieces on hand in the branches the member may see; cost fields only with "See purchase costs" (left out, not null, as in Gweb) |
| `GET /Sup1/api/ghu11/lookups/`, `GET /Sup1/api/ghu11/`, `POST scan/`, `POST start/`, `GET report/` (`fmt=pdf\|xlsx`, `stage=start\|end`) | This platform's stocktake (`xo1` = its id, `brna` = branch code); started and counted from the phone or the web, posted on the web |

Names: `mco` = barcode, `com_mco` = supplier's code, `co` = karat (silver = 1), `gco`/`gdis` = category,
`ssco`/`ssdis` = its top group, `br`/`brna` = branch code and name. Errors keep Gweb's shape
(`{"detail": …}` or `{"field": [message]}`), in the shop's language. A barcode the count does not
expect is turned down with Gweb's reasons (`not_found`, `sold`, `other_branch`, `not_received`,
`returned_to_factory`, `not_in_stocktake`, `already_counted`) and nothing is recorded. Starting a
count for a top group now includes its sub-categories (web too). **Settings → Mobile devices** lists
the signed-in phones and signs one out at once. The contract was checked by running the app's own
parsers (`src/api/mappers.ts`) against this server's answers. Not carried over: CORS (the app is
native) and Gweb's `/Sup/api/fn6/by-mco/`, `/Dim/api/…`, `/Stone/api/…` look-ups used by its
e-commerce site.

### Background jobs, e-mail and WhatsApp

`apps/platform/jobs` is a job queue in PostgreSQL (no Redis or Celery, works on Windows). A job is
a row written in the same transaction as the work that asks for it (`enqueue("messaging.send_email",
message_id=…)`), so a rolled-back sale never sends its e-mail (the outbox pattern). Payloads hold
ids only. Tasks and daily schedules are declared in each app's `jobs.py` (`@task`, `daily(at=…)`).
Run the worker as a service next to the app; more than one may run (rows are claimed with
`FOR UPDATE SKIP LOCKED`):

```
python manage.py run_worker          # until stopped (systemd / supervisor / NSSM)
python manage.py run_worker --once   # or: queue the schedules, run what is due, stop (cron)
```

A client's job runs in that client's `tenant_context` (RLS applies), language and time zone. A
failure is retried after 30 s, 2 min, 8 min… up to the task's `max_attempts`; a job left running
by a dead worker goes back in the queue after 15 minutes; finished jobs are deleted after 14 days
(failed ones after 60). Daily schedules run at the client's local time (`TenantProfile.timezone`),
once per local day even with several workers. Console → **Jobs** shows whether a worker is alive,
what is waiting, what failed and why, with Run again / Cancel.

**Sending to customers** (`apps/messaging`, feature "E-mail and WhatsApp", permission
`messaging.send`): every document page and party statement has a **Send** button.
- *E-mail*: the PDF in the client's design is attached by the worker. Mail goes out from
  `DEFAULT_FROM_EMAIL` with the shop's name, reply-to the person who sent it. Configure
  `EMAIL_URL` (e.g. `smtp+tls://user:password@smtp.example.com:587`); in development mail is
  written to `var/mail/` instead.
- *WhatsApp*: no API account needed. The shop's own WhatsApp opens (`wa.me`) with the message and
  a private link to the PDF (`/shared/<signed token>/`): no login, works for 30 days, counts how
  often it was opened, and can be stopped from **Settings → Sent messages**.
- *WhatsApp through a gateway* (`apps/messaging/gateways.py`), each a console feature, off unless
  switched on for the client; the Send dialog then offers "Send from":
  - **WhatsApp service** (feature `whatsapp_api`): the official hosted API sale codes use
    (`WHATSAPP_GATEWAY_URL`/`KEY`, `/whatsapp/send-message/` or `/send-report/`, the PDF as
    `invoice_file`). It sends its own approved wording: the shop's text is not passed on.
  - **The shop's number** (feature `whatsapp_own`): a whatsapp-web.js gateway (`WA_GATEWAY_URL`,
    `WA_GATEWAY_KEY`; `/sessions/<id>/start|status|qr`, `/send`, `/send-document`) with one session
    per client. **Settings → WhatsApp** links the number by scanning a QR (the page follows the
    scan), sends a test, and unlinks. Documents go out with the shop's text and the PDF.
  The gateway is in this repository (`ops/wa-gateway`, Node 20+, Baileys: WhatsApp Web's own
  protocol, no browser per shop; sessions kept in `ops/wa-gateway/sessions/`, never committed):

  ```powershell
  cd ops\wa-gateway; npm install
  $env:WA_GATEWAY_KEY = "<a long random key>"; node server.mjs   # http://127.0.0.1:3002
  ```

  and give Django `WA_GATEWAY_URL=http://127.0.0.1:3002` and the same `WA_GATEWAY_KEY`. Without a
  gateway URL, development uses a stand-in that "links" a made-up number at once (the page says
  so). Unlinking also logs the number out on the gateway.
  Both are sent by the worker (`messaging.send_whatsapp`, three tries) and logged in Sent messages
  with the route; failures say why ("not linked", "not on WhatsApp", "service not reachable") and
  can be sent again. `WHATSAPP_CONSOLE` (on in development and tests) replaces both with a log.
- **Customers → WhatsApp campaigns** (feature `whatsapp_own`, `messaging.campaign.manage`; legacy
  bulk sending, only from two reports there): one message to all customers with a phone, one home
  branch, those who owe the shop, those who bought since / have not bought since a date, or a
  pasted list, with `{name}` and `{company}` (replaced as plain text: Gweb's `str.format` stopped a
  campaign on a stray brace). The form shows live how many it reaches and who is skipped (no valid
  number, a number twice, opted out). Sent one by one by the worker at a random 25–70 s gap (never
  under 20), only 09:00–21:00 the shop's time, up to 150 a day across campaigns; it pauses after 8
  failures in a row or when the number is unlinked, and can be paused, carried on or cancelled. One
  campaign at a time; each queued send carries a step number so a resumed campaign never sends
  from two chains. **Numbers that do not want campaigns** can be added and removed on the page
  (Gweb had the table but no screen). Nothing is sent automatically on business events (as in
  Gweb), and replies such as "STOP" are not read (the gateway has no inbound hook).
- **Sent messages** lists everything sent, its status (waiting, sent, not sent with the reason,
  link shared/opened), with Send again, Copy link and Stop the link.
- **Daily reminders e-mail**: at 07:30 local time, each person the owner ticks on Sent messages
  gets the dashboard's reminders (cheques due, months not closed, repairs ready…) and yesterday's
  sales, within what their own roles let them see; nothing is sent on a day with nothing to say.
  Links use `SITE_SCHEME` and `SITE_PORT` with the client's primary domain.
- **Monthly report e-mail** (`apps/messaging/monthly.py`): on the 1st at 08:00 local time, each
  person ticked "Monthly" gets last month's sales, gross profit and net profit against the month
  before (with the change in %), the number of sales invoices, whether the month is closed (with
  a link to close it), and the profit and loss, balance sheet and sales analysis attached as PDFs.
  Figures, PDFs and branches follow that person's own roles; the PDFs are made when the e-mail is
  sent. "E-mail me last month's report" on Sent messages sends it at once. Monthly schedules are
  declared with `monthly(task, day=1, at=…)` (days 1-28).
- The profit and loss "against the period before" compares whole calendar months with the same
  months before (September with August 1-31, a quarter with the quarter before); other periods
  with the same number of days.

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
  Its groups (`NAVIGATION` in `apps/core/context_processors.py`: `NavSection(key, title, icon,
  items)`) are menus that open and close from their header, with a count of their screens while
  closed; the open ones are remembered in the cookie `gweb_nav` so the server renders them open,
  and the current screen's group is always open. A group of one screen (or the Overview) is a
  plain link. In the icon rail the groups become plain icons with dividers.
- Ctrl/⌘+K opens the command palette (`static/core/js/cmdk.js`): every screen in the sidebar the
  user may open, searchable in Arabic or English.
- Icons: `{% load ui %}{% icon "coins" "size-5" %}` (Lucide, bundled into `apps/core/ui_icons.json`
  by `npm run assets`; add names to `assets/copy-assets.mjs`).
- Numbers: `{{ value|num:2 }}` inside `<span class="num">` (grouped, Western digits, LTR).
- Every `<select>` is searchable (Tom Select, `static/core/js/selects.js`). `data-native` opts
  out; `data-remote="/api/v1/…"` searches the API instead of listing options.
- Forms post JSON to the API: `<form data-api-form data-endpoint=… data-method=POST|PATCH>`,
  errors appear in `[data-error-for="field"]`. The browser never does pricing arithmetic.
- Loading (`static/core/js/loading.js`), nothing to add per page: a thin accent bar at the top
  while another page or an `api()` request takes more than a moment; on the same page with other
  filters, a tab or a page number, tables keep their headings and their rows turn into a
  skeleton (empty states too); going to another page, the content becomes a page skeleton; the
  button that started a save shows a spinner (forms with `data-api-form`, and any button whose
  click sends a request). Back from the browser cache undoes it all; downloads, print/PDF links,
  new tabs and anything inside `[data-no-loading]` are left alone. For a page's own figures:
  `api(url, { quiet: true })` with `later(() => refreshing(nodes))` dims them while they are
  worked out again, `skeletonRows(table, n)` gives placeholder rows, `busy(button)` a spinner,
  `startNavigation(url)` before a `window.location.assign`. Classes: `.skeleton`,
  `.busy-spinner`, `.is-refreshing`.

## Translations

English strings in code/templates, Arabic in `locale/ar/LC_MESSAGES/django.po`. GNU gettext is
not required:

```powershell
.\venv\Scripts\python.exe ops\i18n\messages.py extract   # add new strings to the .po (_, _l, gettext, ngettext, templates)
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
- The tenant-prefixed cache wrapper; SMS beyond sale codes; automatic WhatsApp messages on business
  events (invoice after a sale, repair ready, birthdays); a client's own sender number on the hosted
  WhatsApp service (Gweb's Meta sender registration); images in campaigns; scheduled report
  e-mails.
- Deactivating a party or branch with a non-zero balance is not blocked yet.
- QR codes on labels, Arabic text in ZPL (printer fonts cannot shape it), expiry reminders, partial receipt of a
  transfer (it is received whole or cancelled), approving stocktake differences line by line,
  RFID bulk counting, partial-weight wholesale returns; work orders: sending finished pieces
  (repairs), receiving in several batches, labour paid in gold;
  production: bills of materials, findings used, stones used in work-order goods (stones
  at workshops are set through setter jobs), a loss allowance per craftsman;
  diamonds: certificate scans, selling part of a parcel of stones, a diamond
  price list (Rapaport-style), buying diamond pieces for money instead of gold;
  repairs: photos of the pieces, paying in scrap gold, gold added by the workshop.
- Payroll: attendance and leave, overtime, social insurance and income tax, payslips per
  employee by email, paying part of the team from a cash box and part by bank, commission tiers
  or targets, commission on wholesale sales.
- Treasury: blind counts (hiding the book balance from the cashier), cheques in a foreign currency, importing bank statement
  files to match automatically, FX revaluation of foreign
  cash (exchanges and transfers use average carrying value; receipts and payments in a foreign
  currency still use the rate in force), choosing among several cash boxes on the sales screen.
- Invitations for people who already have an account in another workspace (OQ-6).
- Closing: per-branch closing, approval by a second person, a closing report pack (P&L and
  balance sheet per month), revaluing gold at the year-end price.
