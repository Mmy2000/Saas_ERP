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
| Reports: a registry of report classes (filters, permission per report, tables) with one screen, print and CSV for Excel: daily summary, gold balances, sales analysis with margin, expenses by category | `apps/reports/` | §7.12, §3.1 |
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
- **Sent messages** lists everything sent, its status (waiting, sent, not sent with the reason,
  link shared/opened), with Send again, Copy link and Stop the link.
- **Daily reminders e-mail**: at 07:30 local time, each person the owner ticks on Sent messages
  gets the dashboard's reminders (cheques due, months not closed, repairs ready…) and yesterday's
  sales, within what their own roles let them see; nothing is sent on a day with nothing to say.
  Links use `SITE_SCHEME` and `SITE_PORT` with the client's primary domain.

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
- The tenant-prefixed cache wrapper; WhatsApp Business API (messages sent by the platform
  itself), SMS, scheduled report e-mails.
- Deactivating a party or branch with a non-zero balance is not blocked yet.
- QR codes on labels, Arabic text in ZPL (printer fonts cannot shape it), reservations of bullion/coins not yet in stock, expiry reminders, partial receipt of a
  transfer (it is received whole or cancelled), approving stocktake differences line by line,
  RFID bulk counting, partial-weight wholesale returns; work orders: sending finished pieces
  (repairs), receiving in several batches, labour paid in gold, stones issued to setters;
  production: bills of materials, stones and findings used, a loss allowance per craftsman;
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
