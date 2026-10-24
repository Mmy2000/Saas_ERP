# Testing strategy

Tests are how this codebase keeps its promises: isolation between clients, balanced books,
nothing posted twice. Every change ships with tests that would fail without it.

## Running them

```powershell
.\venv\Scripts\python.exe ops\check.py                  # everything CI runs: lint, translations, migrations, tests
.\venv\Scripts\python.exe ops\check.py --quick          # without the tests
.\venv\Scripts\python.exe -m pytest apps/expenses       # one app
.\venv\Scripts\python.exe -m pytest -k void -x          # by name, stop at the first failure
.\venv\Scripts\python.exe -m pytest --e2e tests/e2e     # browser tests (python -m playwright install chromium)
```

- **The database:** tests need PostgreSQL and connect as `app_owner` (`DATABASE_OWNER_URL`, or
  `TEST_DATABASE_URL`). They create `test_gweb`. Never use a superuser: superusers bypass
  row-level security and the isolation tests would pass for the wrong reason (automated:
  `test_test_connection_is_not_a_superuser`).
- **Baseline (October 2026):** about 620 tests, 15 of them browser tests skipped without
  `--e2e`. The full run takes about 4½ minutes on a developer machine. The first database test
  waits about a minute while the test database is built.
- **PDF tests** need Chromium (`python -m playwright install chromium`) and are skipped without
  it. CI installs it.

## What kind of test, where

| Kind | Where | Database | Use for |
|---|---|---|---|
| Unit | `apps/<app>/tests/` | no (plain functions) or yes | pure rules: `apps/core/tests/test_numeric.py` (with Hypothesis), `apps/catalog/tests/test_metal.py` |
| Service | `apps/<app>/tests/test_<area>.py` | yes | business acts through the service functions: post, cancel, refuse; ledger effects (`trial_balance()` balances); stock effects |
| API | `apps/<app>/tests/test_api.py` | yes | the contract: status codes, the error envelope and `code`, `required_permissions`, idempotent replay |
| Pages | the app's tests, `tests/test_i18n_and_pages.py` | yes | every page renders in Arabic and English; permission-gated pages answer 403 |
| Isolation | `tests/isolation/` | yes | nothing crosses between clients: RLS, ORM scoping, HTTP by host, model coverage |
| Platform | `tests/test_console.py`, `test_plans.py`, `test_monitor.py`, `apps/platform/*/tests/` | yes | console, jobs, monitoring, backups |
| Repository rules | `tests/test_repo_hygiene.py`, `apps/iam/tests/test_authz.py` | partly | conventions that must never regress (BOMs, template comments, every API action declares and enforces a permission) |
| Browser (e2e) | `tests/e2e/` | yes (`live_server`, real transactions) | JavaScript behaviour, layout in RTL, the designer; opt-in with `--e2e` |

## What every change must test

- **A new business act:**
  - The success path, with its ledger and stock effects asserted. At least assert that the
    trial balance still balances.
  - Each refusal: invalid input (`ValidationError` with the field), business rule
    (`DomainError` with its code), permission (`PermissionDenied`), and **branch scope** (a
    member limited to branch A acting on branch B).
  - The reversal path: cancel or void, and what may no longer be cancelled.
- **A new API action:** status codes, the envelope `code`, the permission refusal, and, for
  `@idempotent` actions, that a replay with the same key returns the same document.
- **A bug fix:** a regression test that **fails before the fix**. Run it once without the fix
  to see it fail. The audit's fixes show the pattern (`test_the_holder_is_locked_before_its_balance_is_checked`).
- **A new tenant model:** nothing extra. The isolation suite already checks the RLS, the guard
  and the tenant index. Run it.
- **A new page:** add it to the page list in `tests/test_i18n_and_pages.py`, so it renders in
  both languages.

## Conventions

- **Name tests for what happens**, in words: `test_a_failed_nightly_backup_is_kept_and_reported`.
- **Build data the way users make it:**
  - `make_tenant` / `tenant_a` / `tenant_b` (root `conftest.py`) provision a full client.
  - `make_member(tenant, "sara", "viewer", branches=[...])` creates a member with a role.
  - `login(tenant, "sara")` returns a signed-in client.
  - The `Shop` builder (`apps/sales/tests/test_sales.py`) gives a shop with stock, a price
    board, a bank and a terminal.
  - Post documents through services, not by inserting rows.
- **Tenant code runs inside `with tenant_context(tenant.id):`.** Most app tests do this in a
  `shop` fixture.
- **Assert on data and codes, not on English text.** Check `exc.code == "DOC_NOT_POSTED"`, or
  field names. Pages render in Arabic by default; use `language="en"` in `login()` only when
  the assertion really needs text.
- **Dates:**
  - Expected document numbers use `this_year()` from `conftest.py`, never a literal year.
  - Prefer fixed dates (`date(2026, 9, 24)`) passed to services over "today" when the year or
    month matters.
  - Don't compute `TODAY` at import time: it changes at midnight during a run.
- **No sleeps.** Wait for conditions (threads use `threading.Event`, browser tests use
  Playwright's waits).
- **Isolate global state.** Outboxes (`apps.messaging.codes.OUTBOX`, `gateways.OUTBOX`) and
  module-level lists must be cleared by a fixture. The cache is cleared around every test
  (autouse in `conftest.py`).
- **Mock only the edges:** HTTP to gateways and market sources, `subprocess` for `pg_dump`, and
  the clock where unavoidable. Never mock the ORM, services or the ledger: test them for real
  against PostgreSQL.

## Concurrency

Locks are the riskiest code and the least tested (TD-20). Two levels:

1. **Cheap and always:** assert the lock is taken before the check, from the SQL. This is
   deterministic and needs no threads:

   ```python
   with CaptureQueriesContext(connection) as queries:
       move(kind="transfer", source_box_id=box.pk, dest_bank_id=bank.pk, amount="100")
   sql = [q["sql"] for q in queries.captured_queries]
   lock = next(i for i, q in enumerate(sql) if "FOR UPDATE" in q and "treasury_cashbox" in q)
   check = next(i for i, q in enumerate(sql) if "ledger_balanceprojection" in q)
   assert lock < check
   ```

2. **For the money paths in TD-20:** a `@pytest.mark.django_db(transaction=True)` test runs two
   calls in threads, each in its own `tenant_context`. An `Event` holds the first transaction
   open while the second starts. The test must fail when the lock is removed.

## Coverage

There is no coverage tool in the toolchain. The baseline is the set of tests and the gaps listed
in the audit (T2–T5, TD-20, TD-21). Don't chase a percentage. Use the "what every change must
test" list above, and close the listed gaps first: permission refusals in treasury, HR,
settlements, expenses, manufacturing and repairs, and branch-scope refusals everywhere.
