# Technical debt register

The known problems we have chosen not to fix yet, most urgent first. It started from the
[October 2026 audit](audit/2026-10-engineering-audit.md); keep it current:

- **Add** an entry when you find a problem you won't fix in your change. Link the finding.
- **Close** an entry in the pull request that fixes it: delete it here and say so in the PR.
  Git history keeps the record.
- **Don't** mark an entry done without the verification it names.

Each entry gives the location, the impact, a recommended fix, what must happen first, and how to
prove the fix. "Decision" means the fix changes business behaviour and needs the product owner's
agreement first (and an ADR, see [decisions](decisions/README.md)).

## 1. Immediate: fix before the next production release

Closed on 2026-10-25 (second pass): TD-01 (shared logins, [ADR-022](decisions/ADR-022-identity-across-workspaces.md)), TD-02 (sign-in
throttling), TD-03 (`/readyz/`), TD-05 (last grams of a lot), TD-06 (draft deletion race),
TD-07 (worker resilience and per-task time limits), TD-13 (HR branch scope). The tools for TD-04
were built; the drill itself is still to do. See the
[audit's second pass](audit/2026-10-engineering-audit.md#second-pass-2026-10-25).

### TD-04
**A restore has never been rehearsed** (O4). High. An operator task now.
- **Where:** [operations → backups](operations/README.md#backups-and-restore).
- **Done:**
  - Every new backup reads its dump back (`pg_restore --list`) and checks the media
    archive's checksums before it counts as good.
  - `manage.py verify_backup [--into <scratch database>]` checks the newest backup, or restores
    it and counts the rows.
- **Left:**
  - Run the drill into a scratch server and record it in the runbook's table.
  - Copy `BACKUP_DIR` off the server (nothing in the code does it).
  - Include `ops/wa-gateway/sessions/` in what is copied, if the gateway is used.
- **Verify:** a dated, successful entry in the runbook's restore table, and an off-site copy
  newer than a day.

### TD-25
**No second factor for platform staff** (S2, the part not done). High.
- **Where:** the console login (`apps/platform/console/views.py`, `StaffLoginForm`).
- **Impact:** a stolen staff password opens every client. Wrong passwords are now throttled
  (`apps/iam/throttle.py`), but a leaked password isn't stopped.
- **Fix:** TOTP for staff accounts: enrolment page, a second step after the password, recovery
  codes. Needs a decision on a dependency (for example `pyotp`) or a small RFC 6238
  implementation.
- **Verify:** tests showing a staff sign-in without the code is refused, and that recovery
  codes work once.

## 2. High-priority structural problems

Fix these one module at a time, each in its own pull request, behind the existing tests.

### TD-08
**Dependency cycles between apps** (A1). High.
- **Where:** [modules](architecture/modules.md#dependencies).
- **Impact:** apps can't be understood, tested or changed alone. 29% of cross-app imports are
  inside functions to dodge cycles.
- **Fix:**
  - Invert the dependencies that point "down".
  - The ledger's closing checklist should ask registered providers (each app registers its
    "unfinished work" check in `ready()`, like permissions) instead of importing five apps.
  - Do the same for `core.context_processors` and the dashboard.
  - Then add an import-boundary check (TD-38).
- **Verify:** the dependency script (in the audit) shows the cycle gone, and the tests pass.

### TD-09
**Cancel flows are copied** (A2). High.
- **Where:** 33 `void_*/cancel_*` functions (e.g. `apps/expenses/services.py:175`);
  the deposit flows `apps/repairs/services.py:131-157` and `apps/sales/reservations.py:88-125`
  are near copies too.
- **Impact:** a fix to cancelling must be made 33 times.
- **Fix:**
  - A `Document.mark_voided(actor, reason)` helper, plus a `void_document()` skeleton taking the
    document-specific checks.
  - One `take_payment()` in `treasury.holders` used by both deposit flows.
- **Decided (2026-10-25):** money paid out in a foreign currency leaves a box at its average
  carrying value (`treasury.holders.outflow_value`), as expenses and vouchers do. Deposits are
  paid back only in the home currency today, where both rates are 1, so nothing changes yet.
  If foreign-currency refunds are added, credit the box at `outflow_value`, clear the deposit at
  what is owed, and book the difference to `fx_gain`/`fx_loss`.
- **Verify:** existing cancel tests unchanged and passing.

### TD-10
**`quote_sale` and `plan_receipt` are too large to review safely** (A3). High.
- **Where:** `apps/sales/services.py:216` (complexity 29); `apps/manufacturing/services.py:326`
  (32).
- **Fix:** extract steps (per-line pricing, discount limits, trade-in, totals) into named
  functions with their own tests. No behaviour change.
- **First:** characterisation tests that pin today's quotes for representative baskets.
- **Verify:** those tests pass before and after; complexity under 15.

### TD-11
**The console has no service layer** (A4). High.
- **Where:** `apps/platform/console/views.py` (822 lines, 39 POST handlers).
- **Fix:** move writes (plan deletion, tenant reassignment, document design versioning) into
  `apps/platform/tenants/services.py` and `apps/printing/services.py`; views only parse and
  render.
- **Verify:** the console tests (`tests/test_console.py`, `tests/test_plans.py`) pass unchanged.

## 3. Incremental improvements

| ID | Problem (audit) | Where | Recommended fix | Verify with |
|---|---|---|---|---|
| <a id="td-12"></a>TD-12 | Anonymous traffic counts against a shop's request limit (S3) | `apps/platform/tenants/middleware.py:28-41` | Separate per-IP limit for anonymous requests; count only signed-in traffic against the shop's quota | test: anonymous flood doesn't 429 a member |
| <a id="td-14"></a>TD-14 | Closed months enforced only in Python; races with closing (D3) | `apps/ledger/services.py:119-128`, `closing.py:287` | Advisory lock per (tenant, month): shared in `post_entry`, exclusive in `close_month`; optional INSERT trigger | concurrency test (TD-20) |
| <a id="td-15"></a>TD-15 | Idempotency gaps (D5) | `apps/ledger/api/views.py:79`, purchase draft create, `apps/core/api/idempotency.py` | `@idempotent` on both; store a body hash (409 on a different body); prune after 30 days | API tests replaying with a different body |
| <a id="td-16"></a>TD-16 | `manage.py` defaults to development settings (O5) | `manage.py:9` | Refuse to run with dev settings when `DJANGO_DEBUG` is false, or read the module from `.env` before Django starts | manual check on a server shell |
| <a id="td-17"></a>TD-17 | Logs lack time/level/logger and request id (O8) | `config/settings/base.py:207-220`, `apps/core/middleware.py` | A formatter, a request-id `logging.Filter`, the id stored on `ErrorEvent` | test that a logged error carries the id |
| <a id="td-18"></a>TD-18 | HTML instead of the JSON envelope for some API errors (O9) | `apps/core/api/exceptions.py`, `apps/core/tenancy/middleware.py:36-40`, `apps/platform/tenants/middleware.py:68` | JSON fallback for `/api/` paths on 500/404/403/429 | API tests per case |
| <a id="td-19"></a>TD-19 | Print-document registry imports private view functions by string (A5) | `apps/printing/documents.py:1005-1042` | Each app registers its document types in `ready()`, with a public queryset function | existing printing tests |
| <a id="td-20"></a>TD-20 | No concurrency tests (T2) | `select_for_update` sites, `apps/core/api/idempotency.py` | A small `transaction=True` helper running two calls in threads with an `Event`; start with selling one piece twice, paying out one box twice, the idempotency in-progress branch | the tests themselves (they must fail if the lock is removed) |
| <a id="td-21"></a>TD-21 | Missing permission/branch refusal tests; shared OUTBOX state (T3, T4) | treasury, hr, settlements, expenses, manufacturing, repairs; `apps/messaging/tests/test_codes.py:110` | One refusal test per write endpoint; clear OUTBOX in an autouse fixture | — |
| <a id="td-22"></a>TD-22 | Unknown icon name is a 500; static files not cache-busted (F1, F2) | `apps/core/templatetags/ui.py:34`; `config/settings/base.py:165` | Fall back to an empty icon and log; `ManifestStaticFilesStorage` (check the vendored files survive `collectstatic`) | test with an unknown name; deploy check |
| <a id="td-23"></a>TD-23 | Accessibility gaps; duplicated JS helpers (F3, F4) | diamonds row editors, `data-error-for`; `static/*/js` | Labels (visually hidden where needed), `aria-describedby` set by `forms.js`; move `fail()`/`escapeHtml()` into `core/js/ui.js`; use `api()` in `opening/js/import.js` and `printing/js/designer.js` | e2e checks |
| <a id="td-26"></a>TD-26 | Nothing in the database stops deleting a posted document header (audit D4: the race is fixed with row locks, the guard is not) | document tables (`apps.core.models.Document` subclasses) | A BEFORE DELETE trigger per document table refusing `status <> 'draft'`, added by a migration operation like `GuardTenantFKs`, checked by the isolation suite | test: deleting a posted invoice row raises in the database |
| <a id="td-24"></a>TD-24 | The OpenAPI schema is incomplete: 129 drf-spectacular warnings (O16) | `manage.py check --deploy --settings=config.settings.prod`; APIViews without `serializer_class`, untyped `<id>` paths | `serializer_class` or `@extend_schema` on hand-written views; `<int:pk>` in routes; then make `spectacular --validate` part of `ops/check.py` | zero `drf_spectacular` warnings |

## 4. Optional and low-risk

| ID | Problem (audit) | Recommended fix |
|---|---|---|
| <a id="td-30"></a>TD-30 | Upload extension from the client name; PDF renderer serves any media path; CSV cells starting `= + @` (S7–S9) | Extension from the detected type; limit the PDF route to the current tenant's folder; prefix `'` on text cells only (never on numbers: `-` starts negatives) |
| <a id="td-31"></a>TD-31 | No CSP; inline scripts; browsable API in production (S10) | Move the 5 inline scripts to modules; `script-src 'self'`; JSON renderer only in prod |
| <a id="td-32"></a>TD-32 | No regular check that running balances match their lines (D6) | Nightly job comparing `BalanceProjection`/`LotBalance` with sums of lines and movements, alert on a difference |
| <a id="td-33"></a>TD-33 | Migrations importing live code; missing journal-line date index (D7, D8) | Freeze the data inside those migrations; add `(tenant, business_date)` index |
| <a id="td-34"></a>TD-34 | Races caught by the database surface as 500; sale-code SMS inside the request transaction; floats at output edges (D9, D10) | Turn `IntegrityError` into `Conflict` at those sites; send codes `on_commit` or through a job; send `unit_value` as a string |
| <a id="td-35"></a>TD-35 | Sessions, idempotency records, mobile devices never pruned; `prune_traffic` needs cron (O12) | One daily worker task doing all four |
| <a id="td-36"></a>TD-36 | Two app layouts; confusing names; services importing `num` from template tags; private cross-app imports (A6–A8) | New code follows [architecture standards](standards/architecture.md); move `num` into `apps.core.numeric`/a formatting module; rename `diamonds/jobs.py` when next touched |
| <a id="td-37"></a>TD-37 | Dead functions (A9) | Delete after a final grep, one small PR |
| <a id="td-38"></a>TD-38 | Nothing enforces complexity or boundaries (A10) | After TD-08: an import-boundary test in `tests/test_repo_hygiene.py` (core imports no business app; ledger imports no document app) and ruff `C901` with `max-complexity = 15`, existing offenders listed in `per-file-ignores` until fixed |
| <a id="td-39"></a>TD-39 | Test builders inside test modules; English UI text in assertions; drifting page lists (T5) | Move `Shop` and friends into `tests/builders/`; assert on codes and data; one shared page list |

## Not yet known

- **The design document.** `PROJECT_PLAN.md`, with ADR-001 to ADR-018, is cited throughout
  the code but isn't in the repository (audit X1). Some entries above may already be decided
  there. Check before starting one.
