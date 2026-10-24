# Verification report, October 2026

What was run to check the [engineering audit](2026-10-engineering-audit.md) changes, with results.
All commands ran on the development machine: Windows 11, Python 3.13.13, PostgreSQL (local,
connecting as `app_owner`).

## Before any change (baseline)

| Command | Result |
|---|---|
| `python -m pytest` | **609 passed, 15 skipped** (browser tests without `--e2e`), 264 s |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 540 files would be reformatted (no formatter in use: [ADR-019](../decisions/ADR-019-lint-without-formatter.md)) |

## After the changes

| Command | Result |
|---|---|
| `python ops/check.py` | **All checks passed:** ruff clean; translations complete and compiled; `makemigrations --check`: no changes; pytest **615 passed, 15 skipped**, 283 s |
| `python -m pytest --e2e tests/e2e` | **15 passed**, 157 s |
| `manage.py check --deploy --settings=config.settings.prod` (dummy secrets) | security: only `security.W021` (HSTS preload, deliberately off). Also 129 drf-spectacular schema warnings, recorded as O16 / TD-24 |
| `npm run build`, then `git status -- static apps/core/ui_icons.json` | the build runs. Differences shown are the uncommitted editor work, expected until committed |
| Link check of every relative link and `#anchor` in `README.md`, `CONTRIBUTING.md` and `docs/` (a scratch script) | all links resolve |

The 6 new tests are:
- `test_every_api_view_enforces_the_declared_permissions`
- `test_the_api_schema_is_for_signed_in_members_only`
- `test_only_the_last_failed_attempt_is_a_server_error`
- `test_a_client_task_cannot_skip_its_transaction`
- `test_a_failed_nightly_backup_is_kept_and_reported`
- `test_the_holder_is_locked_before_its_balance_is_checked`

`test_files_never_cross_between_clients` gained the Windows path cases.

## Fixes reproduced before they were fixed

Each of these tests was run against the old code (the fix stashed), failed, and then passed
with the fix:

| Finding | Test | Failure without the fix |
|---|---|---|
| S4 public schema | `test_the_api_schema_is_for_signed_in_members_only` | anonymous `GET /api/v1/schema/` → `200` |
| S6 Windows path traversal | `test_files_never_cross_between_clients` | member of A downloaded B's file: `200` |
| D1 cash box race | `test_the_holder_is_locked_before_its_balance_is_checked` | no `FOR UPDATE` on the box before the balance read |
| O2 lost backup failures | `test_a_failed_nightly_backup_is_kept_and_reported` | the failed `BackupRun` rows were gone |

The `sign_out_device` finding was checked and is a false positive (`@require_POST` is present).

## Second pass (2026-10-25)

| Command | Result |
|---|---|
| `python ops/check.py` | **All checks passed:** ruff clean; translations complete (16 new strings translated); `makemigrations --check`: no changes (new migration `iam/0009_user_membership_count`); pytest **639 passed, 15 skipped**, 270 s |
| `python -m pytest --e2e tests/e2e` | **15 passed**, 142 s |
| Link check of the docs | all links resolve |

24 new tests. Each fix's tests were run with the fix removed (the files stashed) and failed,
then passed with it:

| Fix | Tests | Without the fix |
|---|---|---|
| Last grams of a lot (D2) | `apps/inventory/tests/test_lots.py` | `CheckViolation` on `inventory_lotbalance_non_negative_check` |
| Draft and adjustment locks, payroll race (D4) | `test_deleting_a_draft_locks_it_before_checking`, `test_deleting_an_adjustment_locks_it_first`, `test_an_adjustment_deleted_while_posting_is_not_paid` | 3 failed |
| HR branch scope (S5) | `apps/hr/tests/test_branch_scope.py` | Hana saw `{'Ali', 'Badr', 'Kamal'}` |
| Shared logins (S1) | `apps/iam/tests/test_shared_logins.py` | 4 of 5 failed (the count test needs only the trigger) |
| Sign-in throttling (S2) | `apps/iam/tests/test_throttle.py` | 3 of 4 failed (the reset test passes either way) |
| Worker resilience and per-task time limits (O6, O7) | `test_one_failed_pass_does_not_stop_the_worker`, `test_a_long_task_is_not_started_twice` | 2 failed |
| `/readyz/`, backups read back, `verify_backup` (O3, O4) | `tests/test_health.py`, `test_a_dump_that_does_not_read_back_is_a_failed_backup`, `test_verify_backup_reads_back_and_restores_elsewhere` | new behaviour |

Checked and changed nothing: deposits are refunded only in the home currency, so the
foreign-currency valuation decision (A2) has no effect yet. A change made for it was reverted.

## Not verified

- **The GitHub Actions workflow** (`.github/workflows/ci.yml`) has not run yet: it can only run
  on GitHub. Its YAML parses, and its steps follow the README setup. The first run on a pull
  request is its test; check the database bootstrap and the `assets` comparison on Linux.
- **The fixed T1 year assertions** were checked by reading them and by the suite passing in
  2026. They were not run with a clock set to 2027: no time-travel library is installed.
- **Real concurrency** for the D1 fix: the test checks lock order from the SQL, not two
  simultaneous transactions (TD-20).
- **`verify_backup --into`'s real restore and row count.** Tests mock `pg_restore` and the
  count, since a second PostgreSQL isn't available here. The first real drill (TD-04) is its
  test.
- **The `membership_count` back-fill on existing data.** It runs per tenant in migration 0009.
  It was exercised on the empty test database only. Check the counts after migrating
  production: `SELECT email, membership_count FROM iam_user ORDER BY membership_count DESC`.
- **Throttling across several servers.** The counts are per cache; production's file cache is
  shared by one server's processes only.
- **Findings marked SUSPECT** in the reviews, and the ones not acted on, were not reproduced.
  Their register entries say how to verify them.
