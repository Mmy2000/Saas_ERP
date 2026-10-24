# Errors and logging

## Raising errors

Services raise the classes in `apps/core/errors.py`. Each one carries a `message` that is safe
to show, a stable `code` and optional `fields`:

| Class | HTTP | When |
|---|---|---|
| `ValidationError(msg, fields={"amount": ["…"]})` | 400 | input is wrong; name the field |
| `PermissionDenied` | 403 | `actor.require` raises it; rarely raised by hand |
| `NotFound` | 404 | the object doesn't exist **or isn't visible to this actor** (don't reveal which) |
| `Conflict` | 409 | the state changed under you: already cancelled, stale version, idempotency |
| `DomainError(msg, code="SALES_DISCOUNT_LIMIT")` | 422 | a business rule refuses the act; always give a specific code |

- **Messages** are translated (`_()`), say what to do when possible ("Cancel the refunds
  first."), and contain no internals.
- **Codes** are stable `APP_UPPER_SNAKE` strings that clients and tests rely on. Don't change a
  code; add a new one.

## How errors reach the user

- **API:** `apps/core/api/exceptions.py` turns them into the error envelope
  ([API standards](api.md#errors)). The browser's `api()` raises `ApiError`, and `forms.js`
  puts `fields` next to the inputs.
- **Web views** that call a service directly catch `ValidationError` / `DomainError` and show a
  form error or a `messages.error`, as about 24 views already do.
- **Unexpected exceptions** become a 500 (`templates/500.html`). The request transaction is
  rolled back. API clients get the HTML page today (TD-18).

## Catching errors

- **Catch only what you can handle**, at the narrowest type (`except DomainError:`), and say why
  in a comment.
- **`except Exception` is allowed only where the failure is recorded or re-raised:**
  - The job worker records it on the job and retries.
  - The error log and the backup record it, then re-raise.
  - A broken custom template is logged, and the standard layout prints instead.
  
  Mark it `# noqa: BLE001 - <why>`.
- **Never swallow an error to return a default** that looks like a real result. A report that
  skipped a value on any exception gave wrong totals (audit O13).

## Logging

- **Get a logger per module:** `logger = logging.getLogger(__name__)`. Never use `print`
  outside management commands.
- **Levels:**
  - `ERROR` for something an operator must look at. A server error, or a job's last failed
    attempt, is stored in **Console → Errors** and e-mailed to `ERROR_ALERT_EMAILS`.
  - `WARNING` for a handled problem that may need attention: a retried job attempt, a broken
    custom template, a market source down.
  - `INFO` for rare, useful events. `DEBUG` for development.
- **Pass the exception** (`exc_info=exc`, or `logger.exception(...)` inside `except`), so the
  traceback is kept.
- **Use `%s` arguments**, not f-strings: `logger.warning("job %s failed: %s", name, error)`.
- **Never log** passwords, tokens, codes, national IDs, full phone numbers with names, or
  request bodies. Log ids.

How it is wired (`config/settings/base.py` `LOGGING`):
- The root logger writes to stderr at `LOG_LEVEL` (default INFO). Under systemd that goes to the
  journal.
- `django.request` and `apps.platform.jobs` also go to `ErrorLogHandler` at ERROR. It groups
  errors by type and place into `ErrorEvent` rows, written on their own database connection so a
  rollback doesn't erase them (`apps/platform/monitor/errors.py`).
- To make another area's errors reach Console → Errors, add its logger to `LOGGING["loggers"]`
  with the `errorlog` handler.

**Known gaps:**
- Log lines have no time, level or logger name, and no request id (TD-17).
- The request id (`X-Request-ID`, `apps/core/middleware.py`) appears only in the error
  envelope and the response header so far. It isn't stored with errors or written to log lines.

## Background jobs

- **To fail without retrying** (nothing will change on a retry: the record was deleted), raise
  `JobFailed("why")`. It is recorded on the job, and not logged as an error.
- **Any other exception** is retried with backoff up to `max_attempts`. The earlier attempts are
  warnings; the last is an error.
- **To show a failure to the shop** (mark the message "failed"), give the task an `on_error`
  hook. It runs in a fresh transaction.
- **A platform task that must keep what it records even when it fails** (the backup) is declared
  with `atomic=False` ([ADR-021](../decisions/ADR-021-platform-tasks-outside-a-transaction.md)).
