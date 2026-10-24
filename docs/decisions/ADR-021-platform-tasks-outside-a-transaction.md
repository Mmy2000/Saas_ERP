# ADR-021: Platform tasks may run outside a transaction

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner (Mahmoud), during the engineering standards pass

## Context

The worker (`apps/platform/jobs/worker.py`) ran every platform task (`per_tenant=False`) inside
one `transaction.atomic()`, the way client tasks run inside `tenant_context`. For the nightly
backup this had two effects:

- `run_backup` records a failed run (`BackupRun` with the error) and then raises so the job
  retries. The raise rolled the record back with the job's transaction, so **failed backups
  never appeared** in Console → Backups. Only the "last good backup is old" warning remained.
- The transaction, and its database connection, stayed open, idle, for the whole `pg_dump`,
  which can take up to an hour.

The existing test called `run_backup()` directly, outside the worker, so it didn't see this.

## Decision

- `@task(..., atomic=False)` runs a platform task without the wrapping transaction. Each
  statement autocommits unless the task opens its own `transaction.atomic()`.
- Client tasks can't use it: `task()` raises `ValueError`, because `tenant_context` is a
  transaction (`SET LOCAL app.tenant_id`).
- The backup (`monitor.backup`) is declared `atomic=False`.
- Tests: `tests/test_errors_and_backups.py::test_a_failed_nightly_backup_is_kept_and_reported`
  runs the backup through the worker. `apps/platform/jobs/tests/test_jobs.py::test_a_client_task_cannot_skip_its_transaction`
  checks the guard.

## Alternatives considered

- **Record the failure in the task's `on_error` hook**, which runs in a fresh transaction.
  Rejected: it splits the backup's bookkeeping across two places, and doesn't fix the long
  idle transaction.
- **Write `BackupRun` through the separate `errorlog` database connection.** Rejected: that
  alias exists for error events, and tests don't have it.

## Consequences

- An `atomic=False` task must be safe to stop halfway. It must either make each step durable on
  its own, or open `transaction.atomic()` around steps that belong together.
- Use it only when the task's records must outlive its failure, or it waits a long time on
  something outside the database.
