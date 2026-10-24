# Engineering audit, October 2026

Date: 2026-10-24 · Branch: `chore/engineering-standards` · Scope: the whole repository.

This report is a snapshot. Items still open live in the [technical debt register](../tech-debt.md),
which is the list to keep up to date. Each finding below links to its register entry (`TD-nn`).

## How the audit was done

- Five read-only reviews ran in parallel: security and tenant isolation; architecture and
  layering; data integrity and concurrency; API, operations and configuration; frontend and
  tests. Each finding cites `file:line`.
- Every finding acted on in this pass was checked again by reading the code. Where possible it
  was reproduced with a test that **fails before the fix and passes after** (marked *reproduced*
  below).
- The baseline was taken before any change: `pytest` gave **609 passed, 15 skipped** (the browser
  tests behind `--e2e`) in 4 min 24 s. `ruff check .` was clean. `makemigrations --check` found
  no pending changes. Translations were complete.

## Summary

**The core is sound, and several of its rules are already enforced by tests:**

- Tenant isolation has four layers: forced row-level security, a fail-closed manager, a
  same-tenant foreign-key trigger, and an isolation test suite that fails when a model misses
  any of them.
- Money and weights are `Decimal` only.
- The ledger is append-only and balanced. Postgres re-checks at commit, and corrections are
  reversals.
- Every API action declares a permission, and undeclared actions are denied.
- Money-moving API calls take idempotency keys.
- Numbering is gapless with row locks.
- Background jobs use an outbox table.

Nothing found was rated Critical (exploitable without preconditions, or corrupting data).

**The biggest risks:**

1. Identity is shared across workspaces, which opens a path to cross-tenant account takeover
   (S1).
2. There is no protection against password guessing on any sign-in (S2).
3. Operations fail silently. Failed jobs and backups were invisible (fixed), nothing alerts
   when the worker dies (O3), and a restore has never been tried (O4).
4. The architecture is drifting. App dependencies form cycles, cancel and payment code is
   duplicated, and a few functions are very large (A1–A4).
5. The design document `PROJECT_PLAN.md` and the ADRs it holds are cited 145 times in the code
   but are missing from the repository (X1).

**Update, 2026-10-25:** the [second pass](#second-pass-2026-10-25) fixed 1, 2 (except a second
factor for staff) and 3 (except running a restore drill).

## Findings

Severity:
- **Critical:** a security hole, a data-integrity risk, or broken functionality.
- **High:** a significant risk to security, data, operations or maintainability.
- **Medium:** a real problem with limited impact or likelihood.
- **Low:** minor.

Status:
- **Fixed:** changed in this pass, with tests.
- **Open:** in the debt register.
- **Doc:** addressed by documentation.

### Security and tenant isolation

| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| S1 | High | **Cross-tenant account takeover through the shared global `User`.** A workspace admin can set the password and email of a `User` who also belongs to other workspaces, or is platform staff. Provisioning reuses an existing user with the same owner email and ignores the password typed. So the owner of shop A can create a member with a future client's email, or reset a shared user, and sign in to the other shop. | `apps/iam/services.py:276-333` (`update_member`, `set_member_password`); `apps/platform/tenants/services.py:80-87` | **Fixed** (second pass, [ADR-022](../decisions/ADR-022-identity-across-workspaces.md)) |
| S2 | High | No brute-force protection on the workspace, console, `/django-admin/` or mobile sign-ins, and no second factor for platform staff. Failed attempts are only recorded. | `config/urls.py:45`; `apps/mobile/auth_api.py:25-40`; `apps/iam/signals.py:72` | **Fixed** (second pass): throttling on every sign-in. Second factor for staff open: [TD-25](../tech-debt.md#td-25) |
| S3 | Medium | The per-client request limit counts anonymous traffic. One IP sending about 10 requests a second, even to 404 pages, blocks every user of that shop with 429. | `apps/platform/tenants/middleware.py:28-41` | Open: [TD-12](../tech-debt.md#td-12) |
| S4 | Medium | **The API schema was public.** drf-spectacular's view sets `permission_classes = [AllowAny]`, which skipped the project's deny-by-default class even though `SchemaView` declared `AUTHENTICATED`. | `config/api_urls.py:7` | **Fixed** (reproduced: anonymous got `200`). Plus a guard test that no `/api/v1/` view drops `HasTenantPermission` |
| S5 | Medium | HR ignores branch scope: an HR role limited to branch A can read and edit branch B's employees (salaries, national IDs) and payroll. Every other module filters by `actor.branch_ids(...)`. This may be intended; it isn't documented. | `apps/hr/views.py:47-160`; `apps/hr/api.py:144-157`; `apps/hr/services.py:152,162` | **Fixed** (second pass): HR follows the employee's branch |
| S6 | Medium | **Path traversal in media serving on Windows hosts.** `x\..\..\..\<other id>\…` passed the checks, and a member of A downloaded B's file. Linux, the production target, was not affected. | `apps/core/media.py:134-146` | **Fixed** (reproduced over HTTP: `200` before, `404` after) |
| S7 | Low | Uploaded files keep the client's file extension (the content is checked by magic bytes). `nosniff` and the `sandbox` CSP stop execution. | `apps/core/media.py:68-70` | Open: [TD-30](../tech-debt.md#td-30) |
| S8 | Low | The PDF renderer serves any `/media/` path with no tenant check. Only matters if HTML injection ever reaches a document. | `apps/printing/pdf.py:78-82` | Open: [TD-30](../tech-debt.md#td-30) |
| S9 | Low | Report CSVs don't neutralise cells starting `= + @`. Careful: `-` starts negative numbers. | `apps/reports/views.py:32-39` | Open: [TD-30](../tech-debt.md#td-30) |
| S10 | Low | No Content-Security-Policy on application pages; inline scripts in 5 templates; DRF's browsable API is on in production. | `templates/base.html:12`; `config/settings/base.py:188-200` | Open: [TD-31](../tech-debt.md#td-31) |
| S11 | Low | Member validation reveals whether an email exists in any workspace. A phone number taken in another workspace gives a 500 (IntegrityError). | `apps/iam/services.py:215-217` | Partly fixed: a taken phone is a field error now; the email message still says the email exists |

Checked and clean:
- Raw SQL is parameterised everywhere.
- There is no `csrf_exempt`.
- `mark_safe` and `|safe` wrap only escaped or validated content.
- Every console route has `staff_required`.
- No real secret is tracked in git.
- `check --deploy` gives only W021 (HSTS preload, deliberately off).
- The WhatsApp gateway compares its key in constant time and binds to 127.0.0.1.

### Data integrity and concurrency

| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| D1 | High | **Two payouts at once could take a cash box below zero.** `ensure_cash_available` read the balance without locking the box: a box holding 1,000 could pay out 800 twice. Card terminals had the same gap, so one terminal could be settled twice. All 17 payout paths share this function. | `apps/treasury/holders.py:92`; `apps/treasury/services.py:135` | **Fixed**: `lock_holder()` locks the row before the check. The test asserts the lock comes before the balance read (it fails without the fix). |
| D2 | Medium | Selling the last grams of a bulk lot can fail with a database error: each movement's fine weight is rounded separately, so the remainder can drift to −0.0001 and break the non-negative constraint. Cost can drift the same way, unconstrained. Shown by arithmetic, not reproduced. | `apps/inventory/services.py:160-171`; `apps/inventory/models.py:168-170` | **Fixed** (second pass, reproduced) |
| D3 | Medium | Closed months are enforced only in Python, and the check races with `close_month`. | `apps/ledger/services.py:119-128,223-225`; `apps/ledger/closing.py:287` | Open: [TD-14](../tech-debt.md#td-14) |
| D4 | Medium | `delete_draft` checks `is_draft` without a lock, so it can delete an invoice posted a moment earlier, leaving its journal entry and items orphaned. Nothing in the database prevents deleting posted document headers. `hr.delete_adjustment` has the same pattern. | `apps/purchasing/services.py:265-273`; `apps/hr/services.py:345-353` | **Fixed** (second pass): row locks in both; no database guard on deleting posted headers yet |
| D5 | Medium | Creating a manual journal entry and a purchase draft are not idempotent, so a retry double-posts. Idempotency keys ignore the request body and never expire. | `apps/ledger/api/views.py:79`; `apps/core/api/idempotency.py` | Open: [TD-15](../tech-debt.md#td-15) |
| D6 | Low | Nothing regularly checks that the running balances (`BalanceProjection`, `LotBalance`) match their lines; `rebuild_*` functions exist but nothing calls them. | `apps/inventory/services.py:179` | Open: [TD-32](../tech-debt.md#td-32) |
| D7 | Low | Two old migrations import live code, so a fresh `migrate` breaks if that code changes shape. | `apps/ledger/migrations/0003*:8`; `apps/iam/migrations/0006*:10` | Open: [TD-33](../tech-debt.md#td-33) |
| D8 | Low | No `(tenant, business_date)` index on journal lines; the year view runs 24 date-range sums. | `apps/ledger/closing.py:99-110,188` | Open: [TD-33](../tech-debt.md#td-33) |
| D9 | Low | Some races the database does catch end as a 500 instead of a clear message: reversing twice, two `close_month` calls at once, opening-balance duplicates. SMS and WhatsApp for sale codes are sent inside the request transaction (15 s timeout). | `apps/sales/codes.py:130`; `apps/opening/services.py:231-236` | Open: [TD-34](../tech-debt.md#td-34) |
| D10 | Low | Floats only at output edges (mobile `unit_value`, a spreadsheet export, label layout). | `apps/mobile/shapes.py:111` | Open: [TD-34](../tech-debt.md#td-34) |

Well protected (keep these; see [database standards](../standards/database.md)):
- Decimal-only arithmetic.
- Balanced entries, checked again at commit.
- Append-only journals and stock movements.
- Gapless numbering.
- Stock moves locked in a stable order.
- Cancel paths lock the document header.
- `PROTECT` on every ledger foreign key.
- Every `RunPython` has a reverse.
- No pending model changes.
- N+1 queries: none serious.

### Operations, configuration and API

| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| O1 | High | **Job failures never reached Console → Errors or the alert e-mails.** Final failures were logged at WARNING, while the error-log handler is ERROR-only. The README said they did reach Errors. | `apps/platform/jobs/worker.py:119`; `config/settings/base.py:211-217` | **Fixed**: the last attempt logs at ERROR (test) |
| O2 | High | **Failed backups disappeared.** Platform jobs ran inside one transaction, so the failed `BackupRun` was rolled back with the job. That same transaction stayed open for the whole `pg_dump`. | `apps/platform/jobs/worker.py:58-60`; `apps/platform/monitor/backups.py:88-94` | **Fixed**: tasks can be `atomic=False`, and the backup is (test reproduced: no record before, two after) |
| O3 | High | No external alerting: `/healthz/` is a static `{"status":"ok"}` with no database, worker or backup check. A dead worker means no e-mail, WhatsApp, prices or backups, and nothing says so outside the console. | `config/platform_urls.py:16` | **Fixed** (second pass): `/readyz/` |
| O4 | High | A restore has never been run. There is no restore command or test, backups sit on the same disk, and WhatsApp sessions aren't backed up. | `README.md` (Backups and restoring) | Partly fixed: every backup is read back, `verify_backup` restores one. The drill itself is open: [TD-04](../tech-debt.md#td-04) |
| O5 | Medium | `manage.py` defaults to development settings. A production shell `migrate` or `backup` without the variable exported runs with console code channels and keys derived from `SECRET_KEY`. | `manage.py:9` | Doc: explained in `.env.example` and the runbook. Open: [TD-16](../tech-debt.md#td-16) |
| O6 | Medium | One database error stops the worker loop (no try/except around tick, claim or heartbeat). | `apps/platform/jobs/management/commands/run_worker.py:56-76` | **Fixed** (second pass) |
| O7 | Medium | With two or more workers, a job running longer than 15 minutes is re-queued and runs twice. The stale timeout is the same for every task. | `apps/platform/jobs/worker.py:31,149-154` | **Fixed** (second pass): per-task `stale_after`, 2 h for the backup |
| O8 | Medium | Log lines have no formatter (no time, level or logger) and no request id. | `config/settings/base.py:207-220` | Open: [TD-17](../tech-debt.md#td-17) |
| O9 | Medium | API clients get HTML instead of the JSON envelope for 500s, unknown hosts, suspended clients, and 429s on the legacy `/Sup*/api/` paths. | `apps/core/api/exceptions.py`; `apps/core/tenancy/middleware.py:36-40` | Open: [TD-18](../tech-debt.md#td-18) |
| O10 | Medium | `.env.example` was missing about 25 variables, including `DJANGO_ALLOWED_HOSTS`. | `config/settings/*.py` | **Fixed** |
| O11 | Medium | No CI, no dependency vulnerability scan, no single command for the checks. | — | **Fixed**: `.github/workflows/ci.yml` and `ops/check.py` |
| O12 | Low | Sessions, idempotency records and mobile devices are never pruned; `prune_traffic` needs a manual cron. | — | Doc (cron in README). Open: [TD-35](../tech-debt.md#td-35) |
| O13 | Low | A report swallowed every exception (including database errors) while pricing a line, so totals could be silently wrong. | `apps/reports/gold_reports.py:91` | **Fixed**: catches `DomainError` only |
| O14 | Low | README mistakes: `npm install` twice, the backup described as running `manage.py backup`, `prune_traffic` missing from the services. | `README.md` | **Fixed** |
| O15 | Low | Dead code: a leftover `startproject` folder (`saas/`) with a hard-coded `django-insecure-…` key, unused; `factory-boy` installed but never imported; a misplaced comment in `requirements/base.txt`. | `saas/settings.py:23` | **Fixed**: removed. The key never protected anything, so it needs no rotation; it stays in git history. |
| O16 | Low | The OpenAPI schema is incomplete: `check --deploy` gives 129 drf-spectacular warnings (87 views it can't describe, 42 untyped path parameters), e.g. `QuoteView`, `TradeQuoteView`, `ReconciliationViewSet`. | `manage.py check --deploy --settings=config.settings.prod` | Open: [TD-24](../tech-debt.md#td-24) |

### Architecture and code

| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| A1 | High | App dependencies form cycles. Counting only module-level imports there are four: {core, tenants}, {catalog, iam, ledger, org, parties, pricing}, {settlements, treasury}, {diamonds, reports, sales, repairs}. Foundation apps import higher ones: `core` imports org, parties and notifications; `ledger` imports treasury, expenses, hr, inventory and purchasing for the closing checklist. 29% of cross-app imports are inside functions to dodge cycles. | [modules](../architecture/modules.md#dependencies) | Open: [TD-08](../tech-debt.md#td-08) |
| A2 | High | **Duplicated business flows.** The cancel skeleton is repeated in 33 `void_/cancel_` functions. Taking a deposit through a box, bank or terminal is copied between repairs and reservations. Those two value outgoing foreign currency at the spot rate, while expenses and vouchers use the box's average carrying value: the same act is valued two ways. | `apps/repairs/services.py:133-157`; `apps/sales/reservations.py:88-110`; `apps/expenses/services.py:150` | Open: [TD-09](../tech-debt.md#td-09) |
| A3 | High | Very large functions carry core money rules: `plan_receipt` (complexity 32, 121 lines), `quote_sale` (29, 150 lines). 43 functions exceed complexity 10. | `apps/manufacturing/services.py:326`; `apps/sales/services.py:216` | Open: [TD-10](../tech-debt.md#td-10) |
| A4 | High | `platform/console/views.py` is a fat controller: 822 lines, 39 POST handlers, its own transactions, design versioning done inline, no service layer. | `apps/platform/console/views.py:599-624,683,738` | Open: [TD-11](../tech-debt.md#td-11) |
| A5 | Medium | The print-document registry is centralised and finds records through string paths to *private* view functions (`"apps.sales.web.views._visible"`). Other registries are filled by each app in `ready()`. | `apps/printing/documents.py:1005-1042` | Open: [TD-19](../tech-debt.md#td-19) |
| A6 | Medium | Two packaging styles: `web/` + `api/` packages in 9 apps, flat `views.py`/`api.py` in 13. All 207 web routes are in `config/urls.py`. | — | Doc: [architecture standards](../standards/architecture.md#app-layout). Open: [TD-36](../tech-debt.md#td-36) |
| A7 | Medium | Names that collide: `catalog` vs `catalogue`, three `documents` modules, two `statements`, `diamonds/jobs.py` about stone-setting work but autodiscovered as background jobs. Cancelling is called void, cancel or undo. | — | Doc: [glossary](../architecture/overview.md#glossary). Open: [TD-36](../tech-debt.md#td-36) |
| A8 | Low | Services import the presentation helper `num` from `core/templatetags/ui.py`. 8 imports reach into private names in other apps. | `apps/treasury/holders.py:20` | Open: [TD-36](../tech-debt.md#td-36) |
| A9 | Low | Dead functions with no callers: `round_fine_weight`, `round_rate`, `nav_current`, `fine_grams_in_stock`, `_previous`, `strip_tags`, `disabled_feature_for`, `no_document_type`. | — | Open: [TD-37](../tech-debt.md#td-37) |
| A10 | Low | Nothing enforces complexity or layer boundaries automatically. | `pyproject.toml` | Open: [TD-38](../tech-debt.md#td-38) |

### Tests

| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| T1 | High | **The suite would have failed from 1 January 2027:** about 30 assertions hard-coded `2026` in the numbers of documents dated today. | e.g. `apps/sales/tests/test_sales.py:91` | **Fixed**: `conftest.this_year()` in 19 files. Explicit fiscal years and fixed dates are unchanged. |
| T2 | High | Nothing tests two requests at the same moment: neither the `select_for_update` locks in about 45 service modules nor the idempotency "in progress" branch. | grep: no threads in tests | Partly: D1 now has a lock-order test. Open: [TD-20](../tech-debt.md#td-20) |
| T3 | Medium | Permission refusals are untested in treasury, hr/payroll, settlements, expenses, manufacturing and repairs. Branch-scope refusals are untested in every money flow. | — | Open: [TD-21](../tech-debt.md#td-21) |
| T4 | Medium | Shared state across tests: `apps.messaging.codes.OUTBOX` is never cleared, and one assertion sits inside an `if`. | `apps/messaging/tests/test_codes.py:110-111` | Open: [TD-21](../tech-debt.md#td-21) |
| T5 | Low | Test builders live inside test modules and are imported across apps (`Shop` in 43 files). 75 assertions check English UI text. Two hand-kept page lists have drifted apart. | `apps/sales/tests/test_sales.py:49` | Open: [TD-39](../tech-debt.md#td-39) |

### Frontend

| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| F1 | Medium | An icon name given as a variable that isn't bundled raises `KeyError`, a 500. Nothing tests the 19 variable sites. | `apps/core/templatetags/ui.py:34` | Open: [TD-22](../tech-debt.md#td-22) |
| F2 | Medium | Static files have no hashed names, so browsers can keep old JS and CSS after a deploy. | `config/settings/base.py:165-167` | Open: [TD-22](../tech-debt.md#td-22) |
| F3 | Medium | About 108 of 738 form controls have no label, only a placeholder. Error slots aren't tied to their input (`aria-describedby`). No skip link. | heuristic scan | Open: [TD-23](../tech-debt.md#td-23) |
| F4 | Medium | Duplicated JS: the same error toast 7×, HTML escaping 5×, CSRF read by hand instead of `api()` 2×. | `static/*/js/` | Open: [TD-23](../tech-debt.md#td-23) |

### Documentation

| ID | Sev | Finding | Status |
|---|---|---|---|
| X1 | High | `PROJECT_PLAN.md` is cited 145 times (`§` numbers in 91 files) and ADR-002 to ADR-018 are cited, but none of these files are in the repository. | The owner is adding `PROJECT_PLAN.md`. [docs/decisions/](../decisions/README.md) explains how the ADRs fit. |
| X2 | Medium | The README (1,060 lines) is the only document. It mixes setup, operations and a changelog of features. There is no contribution guide, architecture document, standards or runbook. | **Fixed**: [docs/](../README.md) and [CONTRIBUTING.md](../../CONTRIBUTING.md). The README is kept, with links added. |

### False positives

- *"`sign_out_device` changes state on GET."* It has `@require_POST`
  (`apps/mobile/views.py:24`), and its template posts a form.

## What changed in this pass

See the [refactoring summary](#refactoring-summary) below and the commit history of the branch.

### Refactoring summary

| Change | Files | Compatibility |
|---|---|---|
| Cash box and card terminal rows locked before their balance is checked (D1) | `apps/treasury/holders.py` (`lock_holder`), `apps/treasury/services.py` | Same results. Two payouts from the same box now queue instead of running together. |
| Platform tasks may opt out of the wrapping transaction; the backup does (O2) | `apps/platform/jobs/registry.py`, `worker.py`, `apps/platform/monitor/jobs.py` | Other tasks unchanged. A client task can't opt out (`ValueError`). |
| A job's final failed attempt logs at ERROR (O1) | `apps/platform/jobs/worker.py` | New: those failures appear in Console → Errors and are e-mailed to `ERROR_ALERT_EMAILS`. `JobFailed` (deliberate, no retry) still logs nothing. |
| The schema view keeps `HasTenantPermission` (S4) | `config/api_urls.py` | `/api/v1/schema/` now needs a signed-in member. |
| Media paths with `\` or `:` are refused, and the resolved path must stay in the same folder (S6) | `apps/core/media.py` | Stored names never contain either character. |
| A report catches only `DomainError` while pricing a line (O13) | `apps/reports/gold_reports.py` | A database error now fails the report instead of hiding a value. |
| Document-number assertions use `this_year()` (T1) | `conftest.py`, 19 test files | Tests only |
| Guard tests: every `/api/v1/` view enforces permissions; the schema needs sign-in; the holder lock comes before the balance read; a failed nightly backup is kept and reported; only the last attempt is an error; client tasks can't skip their transaction; Windows path traversal | `apps/iam/tests/test_authz.py`, `apps/treasury/tests/test_treasury.py`, `tests/test_errors_and_backups.py`, `apps/platform/jobs/tests/test_jobs.py`, `tests/test_branding.py` | Tests only |
| Removed `saas/` and `factory-boy`; fixed a comment in `requirements/base.txt` | | Nothing imported them |
| `.env.example` lists every variable the settings read; README corrections | | Documentation only |
| CI workflow, `ops/check.py`, `.editorconfig` | | New tooling |
| `docs/` and `CONTRIBUTING.md` | | New documentation |

## Second pass (2026-10-25)

After the owner's decisions (identity: the proposed fix; HR follows branches; foreign-currency
payouts at the average rate), these were fixed. Each one has a test that fails on the old code
and passes on the new.

| Finding | Change | Files | Behaviour change |
|---|---|---|---|
| S1, S11 | **Shared logins.** The database counts each login's workspaces (`User.membership_count`, kept by a trigger on `iam_membership`; a workspace can't see other workspaces' memberships). A workspace can't change the password, email or phone of a login also used elsewhere or by staff. Provisioning adds a workspace to an existing login only with "add this workspace to it" ticked (console) or `--link-existing-owner`, never to a staff login, never with a password. A taken phone is a field error. | `apps/iam/models.py`, `migrations/0009_user_membership_count.py`, `services.py`; `apps/platform/tenants/services.py`; `apps/platform/console/forms.py`, `views.py`; `templates/console/tenant_new.html`; `create_tenant` | An owner can no longer reset a member who also works for another shop: that person changes it themselves. Creating a shop with an existing owner email needs the box. |
| S2 | **Sign-in throttling.** 5 wrong passwords lock that account for up to 15 minutes; 30 from one address lock that address. Covers the workspace, console, Django admin and mobile app (`429` with `Retry-After`). The forms say why. | `apps/iam/throttle.py`, `backends.py`, `signals.py`, `web/forms.py`; `apps/platform/console/forms.py`; `apps/mobile/auth_api.py` | Yes: lockouts. With several servers, `CACHE_URL` must be shared. |
| S5 | **HR follows branches.** Employees, their advances, adjustments, payslips and commissions follow the employee's branch. Employees with no branch, and payroll runs (they pay everyone), need the permission for every branch. | `apps/hr/services.py`, `views.py`, `api.py` | Branch-limited HR roles see fewer employees and no payroll runs (their employees' payslips stay on each employee's page). |
| D2 | **The last grams of a lot** take exactly the fine weight that is left. No movement takes more than is left. | `apps/inventory/services.py` | The last movement's fine weight differs by up to 0.0001 g; the ledger follows the movement. |
| D4 | **Draft deletion locks the invoice** like posting does. Deleting an adjustment locks it, and a payroll refuses to post (`409 HR_ADJUSTMENTS_CHANGED`) if one of its adjustments was deleted meanwhile. | `apps/purchasing/services.py`, `apps/hr/services.py` | None, except under the race. |
| O3 | **`/readyz/`** on the platform host checks the database, a live worker (seen in the last minute, or busy with a job within its time limit) and a good backup in the last 26 hours. It answers 503 with the failing checks. The console uses the same thresholds. | `apps/platform/monitor/health.py`, `config/platform_urls.py`, `apps/platform/console/jobs.py`, `operations.py` | New endpoint |
| O4 | **Backups read themselves back** (`pg_restore --list`, the zip's checksums) before counting as good. **`manage.py verify_backup [--into URL]`** checks or restores the newest one; it refuses the live database. | `apps/platform/monitor/backups.py`, `management/commands/verify_backup.py`, `config/settings/base.py` (`BACKUP_PG_RESTORE`) | A dump that can't be read back is a failed backup |
| O6, O7 | **The worker survives a failed pass** (logged as an error, retried after 5 s). **Each task has its own time limit** before it counts as abandoned (`@task(stale_after=…)`); the backup's is 2 hours. | `apps/platform/jobs/management/commands/run_worker.py`, `worker.py`, `registry.py`, `apps/platform/monitor/jobs.py` | None |
| A2 (valuation) | **No change needed:** deposits are paid back only in the home currency (`cancel_reservation`, `cancel_repair`), where spot and average rates are both 1. The decision is recorded in TD-09 for when foreign-currency refunds are added. | — | — |

## Verification

See the [final verification report](verification-2026-10.md) for every command run and its
result.
