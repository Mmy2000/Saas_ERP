# Modules

There are 26 Django apps under `apps/`. Each is one area of the business. README "What exists so
far" describes their features in detail. This page is the map: what each app owns, and which
apps may depend on which.

## The apps

Grouped by layer: an app may depend on its own layer and the layers above it in this table,
never on the layers below.

| Layer | App | Owns |
|---|---|---|
| **Foundation** | `core` | Tenancy (context, middleware, RLS operation, `TenantCommand`), `TenantScopedModel` and `Document`, Decimal helpers (`numeric`), numbering (`sequences`), errors and API plumbing (`errors`, `api/`: envelope, pagination, idempotency, renderer), media serving, i18n, shared template tags (`ui`) |
| | `iam` | Users, memberships, roles, the permission catalog, `Actor`, sign-in history |
| | `org` | Tenant profile, branches, the dashboard |
| | `catalog` | Currencies, metals, karats and fineness (`domain/metal.py`), item categories and making charges |
| | `pricing` | Price boards, FX rates, the pricing engine (`engine.py`) |
| | `parties` | Customers, suppliers, trade accounts, workshops (one `Party`, many roles), encrypted national ID |
| | `ledger` | Chart of accounts, `post_entry`/`reverse_entry`, balances, statements, closing periods and years |
| | `audit` | The audit trail (database triggers) and same-tenant foreign-key guards |
| **Holdings** | `inventory` | Pieces (one `Item` per piece), bulk lots, stock movements, transfers, stocktakes, blind counts, the photo catalogue |
| | `treasury` | Cash boxes, bank accounts, card terminals ("holders"), treasury movements, cheques, bank reconciliation, cash counts |
| **Business documents** | `purchasing` | Supplier invoices, scrap bought and sold, returns to suppliers |
| | `sales` | Retail sales (POS), reservations, customer returns, wholesale (`trade.py`), sale codes |
| | `settlements` | Receipts and payments, gold received and given, balance transfers |
| | `expenses` | Expense categories, vouchers, refunds |
| | `hr` | Employees, advances, adjustments, payroll, commissions |
| | `manufacturing` | Work orders at workshops, in-house production |
| | `repairs` | Repairs and custom orders |
| | `diamonds` | Diamond pieces and loose stones, parcels, stone setting, buy-back (a feature, off by default) |
| | `merchandising` | Selling prices of pieces changed in bulk, offers |
| | `opening` | Opening balances on the day a shop starts on the system, Excel import |
| **Outputs** | `reports` | The report registry and every report, including P&L and the balance sheet |
| | `printing` | Labels and barcodes, printable documents and their designs, PDFs, fonts |
| | `messaging` | E-mail, WhatsApp and SMS to customers, campaigns, the daily digest |
| | `notifications` | In-app notifications (the bell) |
| | `mobile` | The mobile app's API (stock, stocktake) and its tokens |
| **Platform** | `platform.tenants` | Tenant registry, domains, plans, features, provisioning, traffic limits |
| | `platform.console` | The staff console (platform hosts) |
| | `platform.jobs` | Job queue, worker, scheduler |
| | `platform.market` | Market gold price sources |
| | `platform.monitor` | Server samples, error log, backups |

`platform.*` may read any app, but only inside a client's `tenant_context`. Tenant apps never
import `platform.*`, except `platform.jobs` (to enqueue) and `platform.tenants.features` (to
check a feature).

## Dependencies

Measured in October 2026 by parsing every import (the audit's `deps.py`).

**Most imported:**

| App | Imported by | | App | Imported by |
|---|---|---|---|---|
| core | 27 apps | | pricing | 18 |
| iam | 26 | | ledger | 17 |
| org | 25 | | inventory | 14 |
| catalog | 19 | | treasury | 14 |
| | | | parties | 14 |

**Widest fan-out:** reports 19 apps, diamonds 14, sales 14, console 13, org 12.

**Where the code breaks the direction above (TD-08).** 29% of cross-app imports are written
inside functions, mostly to dodge these cycles.

- **Foundation importing documents.**
  - `ledger/closing.py:221-249` imports treasury, expenses, hr, inventory and purchasing for
    the closing checklist.
  - `ledger/accounts.py:58,147` does the same.
- **`core` importing higher apps:** `core/listing.py:66,81` and
  `core/context_processors.py:200-227` import org, parties and notifications.
- **Holdings importing an optional feature:** `inventory/web/views.py:73-79` and
  `inventory/blind_counts.py:48` import diamonds.
- **Cycles in module-level imports alone:**
  - {core, platform.tenants}
  - {catalog, iam, ledger, org, parties, pricing}
  - {settlements, treasury}
  - {diamonds, reports, sales, repairs}
- **Private names imported across apps (8):** e.g. `printing/documents.py:1023` imports
  `sales.web.trade_views._scope`, and `mobile/auth_api.py:42` imports `iam.signals._record`.

**How to fix a "down" dependency:** invert it with a registry the higher app fills in its
`AppConfig.ready()`, the way permissions, jobs and document links already work (see
[architecture standards](../standards/architecture.md#registries)). Example: each document app
registers its "unfinished work" check, and the ledger's closing checklist iterates the
registry.

## Registries

These are the extension points. Add to them; don't import the consumer.

| Registry | Declared in | Filled by |
|---|---|---|
| Permissions | `apps/iam/catalog.py` (`register_permissions`) | each app's `permissions.py`, autodiscovered |
| Background tasks and schedules | `apps/platform/jobs/registry.py` (`@task`, `daily`, `monthly`, `every`) | each app's `jobs.py`, autodiscovered |
| Document links (open any document from a ledger line) | `apps/core/documents.py` (`register_document`) | each app's `AppConfig.ready()` |
| Reports | `apps/reports/registry.py` (`@register`) | report modules imported from `apps/reports/permissions.py` (TD-08: should move to each app's `ready()`) |
| Printable document types | `apps/printing/documents.py` (`TYPES`) | the module itself (TD-19: should be filled by each app) |
| Market price sources | `apps/platform/market/sources/` | one module per source |
| Sidebar | `apps/core/context_processors.py` (`NAVIGATION`) | the module itself |
