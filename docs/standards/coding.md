# Coding conventions

How code in this repository is written. **Automated** means a check fails, locally
(`python ops/check.py`) and in CI. Everything else is checked in review.

## Formatting and lint

- **ruff** with the rules in `pyproject.toml`: `E F W I B UP DJ S608`, line length **100**,
  target Python 3.12. Automated.
- **There is no auto-formatter.** The code has a consistent hand style, and `ruff format` would
  rewrite 540 files ([ADR-019](../decisions/ADR-019-lint-without-formatter.md)). Match the code
  around you:
  - Continuation lines align with the opening bracket.
  - Short related statements may share a line (`a, b = x, y`).
  - Blank lines separate the steps of a function, and section comments mark parts of a long
    module (`# --- vouchers ---`).
- **Imports** are sorted by ruff (`I`): standard library, third party, `apps.`, local.
  - Use `from __future__ import annotations` in modules with type hints.
  - Import cross-app names at module level unless the module is heavy, optional (a feature
    app), or the import would create a cycle. The cycle case is debt, see
    [architecture standards](architecture.md#dependency-direction).
- **`.editorconfig`:** UTF-8 **without BOM** (automated: `tests/test_repo_hygiene.py`), spaces,
  4 for Python, 2 for HTML/JS/CSS, final newline. On Windows, edit with an editor or Python,
  never PowerShell 5.1's `Set-Content -Encoding utf8`, which writes a BOM.
- **Template comments** `{# … #}` must close on the same line; use `{% comment %}` for longer
  ones (automated).

## Naming

| Thing | Convention | Example |
|---|---|---|
| Modules, functions, variables | `snake_case`, named after the business act | `post_expense`, `void_trade_sale`, `ensure_cash_available` |
| Classes, models | `PascalCase`, singular | `ExpenseVoucher`, `CashBox` |
| Input objects | `<Thing>Input`, frozen dataclass | `ExpenseInput`, `TreasuryInput` |
| Constants | `UPPER_SNAKE` | `DOC_TYPE = "expenses.ExpenseVoucher"` |
| Private helpers | leading `_`, used only in their module | `_field_error` |
| Permission codes | lower case, dotted `app.object.action` (automated: `test_catalog_codes_are_well_formed`) | `expenses.voucher.create`, `expenses.void` |
| Error codes | `APP_UPPER_SNAKE` | `DOC_NOT_POSTED`, `CATALOG_CATEGORY_IN_USE` |
| URL names | `kebab-case` | `expense-view`, `console-tenant-document` |
| Document number prefixes | 2–3 capitals, unique (`allocate_number`) | `EX`, `SI`, `PRL` |
| Database constraints and indexes | `<app>_<model>_<what>_<kind>` | `expenses_voucher_amount_check`, `expense_date_idx` |
| Tests | `test_` + what happens, in words | `test_cash_expense_posts_and_cancels` |

**Use the domain words** (see the [glossary](../architecture/overview.md#glossary)). For a new
reversal of a posted document, call it `void_<document>`: that is the name used by 21 functions,
against 7 `cancel_` and 4 `undo_`.

## Functions and modules

- **Aim for functions under about 60 lines, with cyclomatic complexity under 15.** If a
  function grows past that, extract named steps. Today 41 functions exceed complexity 10
  (`ruff check apps --select C901`; the worst are in TD-10). Don't add to the list.
- **More than about four parameters:** make them keyword-only (`*,`) or pass an `XInput`.
- **Don't nest conditions three or more deep.** Return early, or raise.
- **One module, one subject.** Split a module past about 600 lines by business act (as
  `sales/` does with `returns.py`, `trade.py`, `reservations.py`).
- **No magic numbers.**
  - Money and weight precision comes from `apps.core.numeric` (`MONEY`, `round_money`,
    `quantize`).
  - Fineness comes from `apps.catalog.domain.metal`.
  - Anything else gets a named constant with a comment saying where the value comes from.

## Numbers, text and time

- **Money, weights and rates are `Decimal`, always.**
  - Build them with `quantize(value, MONEY)` / `to_decimal`; these refuse `float`.
  - Round only through `apps.core.numeric`.
  - Never use `float()`, `round()` on money, or `FloatField` (automated:
    `tests/isolation/test_model_coverage.py::test_no_float_fields`).
- **Every user-visible string is English source text**, wrapped in `_()` / `gettext_lazy` /
  `{% translate %}`, and translated to Arabic in `locale/ar/LC_MESSAGES/django.po` (automated:
  `ops/i18n/messages.py check`). Write `%(name)s` placeholders, not f-strings, inside `_()`.
- **Stored text stays language-neutral.** Store codes and user input, and translate when
  displaying (e.g. `apps/treasury/holders.py::_box_account_name`).
- **Dates:**
  - `timezone.localdate()` is the business date; `timezone.now()` is a moment.
  - A job runs in its client's time zone (the worker sets it).
  - Never write today's year into a test: use `conftest.this_year()`.

## Comments and docstrings

- **Module docstring:** what this module is for, in the business's terms. Add the ledger lines
  it posts when it posts (see `apps/expenses/services.py`).
- **Function docstring** for any public service or helper whose name doesn't say everything:
  - One sentence on what it does.
  - Then the non-obvious rules: what it refuses, what it locks, what it posts.
- **Comments say why, not what.** Write them for a reason a reader couldn't guess. Good
  examples from the code:
  - `# Not atomic: a failed run's record must outlive the failure`
  - `# a karat missing on that board: no comparison`
- **§ numbers** (`§7.12`) refer to `PROJECT_PLAN.md`, and **ADR-nnn** to a decision. Cite them
  when the code implements a section or decision; they are how a reader finds the reasoning.
- **No commented-out code and no TODOs without a register entry.** Use git for old code. A
  known problem goes in the [debt register](../tech-debt.md), with its ID in the comment
  (`# TD-09`).

## Errors

- **Raise `apps.core.errors`** (`ValidationError`, `DomainError`, `NotFound`,
  `PermissionDenied`, `Conflict`). Never raise a bare `Exception`.
- **Catch the narrowest exception** that the code can handle. `except Exception` is allowed
  only where everything is recorded or re-raised: the worker, error logging, the backup. Mark it
  with `# noqa: BLE001 - <why>`. A broad catch that hides a value is a bug (audit O13).
- **Never `pass` on an exception**, and never return a default that looks like a real result.

More detail: [errors and logging](errors-and-logging.md).

## Types

- **Annotate the signatures of public functions**, the return type included (`-> ExpenseVoucher`).
  Locals don't need annotations.
- **There is no type checker in CI yet.** Annotations document the code; keep them true.

## Dead and deprecated code

- **Delete code with no callers in the same pull request** that removes its last caller. Prove
  it with a grep across `apps config ops tests templates static`.
- **Backward compatibility is needed for:**
  - The mobile app's legacy endpoints (`/api/auth/`, `/Sup/api/`, `/Sup1/api/`): their response
    shapes are fixed on purpose.
  - Data already stored (migrations).
  - Anything a client has saved (custom document HTML, label designs).
  
  Change these only with a migration path in an ADR.
- **Mark deprecated code** with a comment giving the replacement and the register ID, and
  remove it when the last user moves.
