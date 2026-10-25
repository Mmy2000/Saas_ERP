# Architecture standards

How new code fits the existing design. The design itself is in the
[architecture overview](../architecture/overview.md), and the apps in [modules](../architecture/modules.md).
Where existing code breaks a rule here, the [debt register](../tech-debt.md) says so. New code
doesn't copy those exceptions.

## Where code goes

| Kind of code | Goes in | Never in |
|---|---|---|
| A business rule: what may happen, what it costs, what it posts | a **service function** in the app's `services.py` (or a topical module such as `sales/returns.py`) | views, serializers, forms, templates, JS, signals, models' `save()` |
| A pure calculation with no database (karat conversion, a price formula) | `domain/` in the app (`apps/catalog/domain/metal.py`) or a plain module | anywhere it gets copied |
| A read query used by more than one screen | `selectors.py` | copied into each view |
| Parsing and shaping JSON | API serializers in `api.py` / `api/` | services |
| Rendering a page | web views in `views.py` / `web/` and templates | services |
| A permission code | the app's `permissions.py` (`register_permissions`) | strings invented in a view |
| Background work | a `@task` in the app's `jobs.py`, queued with `enqueue()` | threads, `on_commit` sends |
| A money or gold effect | `apps.ledger.services.post_entry` / `reverse_entry` | direct writes to journal or balance tables (the database refuses them anyway) |
| A document number | `apps.core.sequences.allocate_number` | `max(number) + 1` |

### Service functions

This is the shape every write follows (`apps/expenses/services.py` is a short, complete example):

```python
@dataclass(frozen=True)
class ExpenseInput:              # what the caller provides, as plain values
    branch_id: int
    amount: Decimal | str
    ...

def post_expense(data: ExpenseInput, *, actor=None) -> ExpenseVoucher:
    with transaction.atomic():                       # 1. one transaction for the whole act
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if actor is not None:
            actor.require("expenses.voucher.create", branch=branch)   # 2. permission, per branch
        ...                                          # 3. validate; raise ValidationError(fields=…)
        ensure_cash_available(box, amount, "amount") # 4. lock what you check (select_for_update)
        voucher = ExpenseVoucher.objects.create(...)
        voucher.number = allocate_number("EX", branch=branch, fiscal_year=today.year)
        voucher.journal_entry = post_entry(...)      # 5. ledger effect through the ledger
        voucher.save()
    return voucher
```

Rules:
- **Keyword-only arguments.** Pass an `XInput` dataclass when there are more than about four
  values.
- **`actor=None`** means a trusted internal caller (a test, a job, another service that has
  already checked). The API always passes `request.actor`.
- **The service opens its own `transaction.atomic()`.** Don't rely on the request transaction:
  it commits on 4xx responses.
- **Lock before you check** anything another request could change between your check and your
  write (a piece's status, a box's balance, a document's state). Use
  `select_for_update(of=("self",))`, so only the row you mean is locked, not the rows it joins.
  Lock several rows in a stable order (by id).
- **Raise the errors from `apps.core.errors`:**
  - `ValidationError(message, fields={"field": [...]})` for input.
  - `DomainError(message, code="APP_SPECIFIC_CODE")` for a broken rule.
  - `NotFound` and `PermissionDenied`.
  - `Conflict` for a stale state.
- **Return the model.** The caller serialises it.

### App layout

New apps, and new code in apps that already use it, follow the package layout:

```
apps/<app>/
  apps.py            AppConfig; ready() registers documents, report types, checks
  models.py          fields, constraints, indexes, display helpers
  services.py        writes (or topical modules: returns.py, transfers.py…)
  selectors.py       shared reads (optional)
  domain/            pure calculations (optional)
  permissions.py     register_permissions(...)
  jobs.py            @task / daily(...) (optional)
  api/ (or api.py)   serializers, ViewSets with required_permissions, urls (router)
  web/ (or views.py) page views with @permission_required
  templates/<app>/   templates
  migrations/        including EnableTenantRLS and GuardTenantFKs operations
  tests/             test_*.py
```

Nine apps use `web/` and `api/` packages; thirteen use flat `views.py` and `api.py`. Both are
accepted; don't reorganise an app just for this (TD-36). Web routes are in `config/urls.py` and
API routes in each app's router, included from `config/api_urls.py`.

### Adding an app

1. Is this a new business area, or a document inside an existing one? Most new features belong
   in an existing app.
2. Pick its layer in [modules](../architecture/modules.md#the-apps) and depend only on its own
   layer and the layers above.
3. Add it to `INSTALLED_APPS` in `config/settings/base.py`. Every model inherits
   `TenantScopedModel`. Its first migrations add `EnableTenantRLS` and `GuardTenantFKs`, which
   the isolation suite checks.
4. Add a row to the table in [modules](../architecture/modules.md), and to README "What exists
   so far".

## Dependency direction

- An app imports its own layer and the layers above it, never below
  ([modules](../architecture/modules.md#the-apps)). `ledger` must not import `sales`; `core`
  imports no business app.
- **Need a lower app to act?** Give the higher app a **registry** it fills from `ready()`, or a
  plain callback parameter. Don't import the lower app.
- **Import public names only.** Never `_private` functions from another app. If you need one,
  make it public in its own app, with a docstring.
- Imports inside functions are allowed for heavy or optional modules. They are not a way around
  a cycle: that is debt (TD-08).

### Registries

The project's main extension mechanism. An app adds itself and the consumer iterates:

```python
# apps/expenses/apps.py
def ready(self):
    from apps.core.documents import register_document
    register_document("expenses.ExpenseVoucher", "expense-view", _("Expense"))
```

Use an existing registry ([modules → registries](../architecture/modules.md#registries)) before
creating one. A new registry needs a reason two apps can't otherwise meet without a cycle;
record it in an ADR.

## Patterns in use

These patterns already exist and solve real problems here. Use them as they are. A new pattern
needs an [ADR](../decisions/README.md).

| Pattern | Problem it solves | Where | How to use it | Trade-off |
|---|---|---|---|---|
| **Service layer** (functions) | Business rules in one place, callable from API, jobs, imports and tests | `services.py` in every app | See [service functions](#service-functions) | Plain functions, not classes: no inheritance to share a skeleton, so cancel flows are copied (TD-09) |
| **Command / input objects** | Many arguments, validated once, easy to build in tests | frozen `XInput` dataclasses (58); older `*Command` (`catalog`, `pricing`, `platform.tenants`) | Name new ones `XInput`; plain values (ids, strings, Decimals), never request objects | A second layer of fields next to the serializer |
| **Registry + autodiscovery** | Apps contribute to a shared list without the owner importing them | permissions, jobs, document links, reports, printable documents | [Registries](#registries) | Import side effects: a module that isn't imported doesn't register |
| **Strategy** | Interchangeable implementations behind one interface | market price sources (`platform/market/sources/base.py`); messaging gateways (`messaging/gateways.py`) | Subclass the base, register the new source or gateway | Only worth it with two or more real implementations |
| **Outbox** | Side effects (e-mail, WhatsApp) only when the work commits | `apps/platform/jobs` (`enqueue` inside the transaction) | `enqueue("app.task", id=…)` with ids, not personal data | At-least-once delivery: tasks must be safe to run twice |
| **Quote then post** | The browser shows exact prices without doing money arithmetic | `quote_sale`/`post_sale`, `plan_receipt` | Post re-computes from scratch; never trust a quote sent back | Two code paths must agree (both call the same helpers) |
| **Ledger as the single source of balances** | One truth for money and gold | `ledger.post_entry` / `reverse_entry`, `BalanceProjection` | Post lines; read balances from selectors | Every feature must express its effect as journal lines |
| **Idempotency key** | Double clicks and retries never post twice | `apps/core/api/idempotency.py` (`@idempotent`) | Decorate every API action that moves money or stock | Keys are stored; see TD-15 for missing expiry |
| **Template-method-like layouts** | Printable documents share data, differ in look | `apps/printing/documents.py` (`Doc`), `layouts/*.html` | Builders produce a `Doc`; layouts only render | The registry is central (TD-19) |

We do **not** use repositories over the ORM, dependency-injection containers, domain events or
signals for business rules. The ORM with `TenantScopedModel` is already the persistence
abstraction, and these would add layers without solving a problem we have.

## SOLID, applied here

- **Single responsibility.**
  - A service function does one business act: post an expense, cancel it.
  - A view parses and renders.
  - Split a function when it has more than one reason to change. `quote_sale` was 150 lines
    with six reasons; it is now 29 lines calling one named step for each
    (`apps/sales/services.py`).
- **Open/closed.**
  - Extend through registries and strategies, not by editing a central list.
  - A new report registers itself; a new market source is a new module.
- **Liskov.** A subclass of `Document`, `TenantScopedModel`, a market `Source` or a gateway must
  keep the base's contract. That includes `TenantScopedModel`'s rules on tenant indexes, and a
  document's draft → posted → voided states.
- **Interface segregation.**
  - Services take the values they need (an `XInput`), not a request.
  - Selectors return querysets or small dataclasses.
- **Dependency inversion.** Lower layers define the registry or callback, and higher layers plug
  in. Don't invert with abstract base classes unless there are two real implementations.

**When is a new abstraction justified?**
- When the same logic exists or will exist in three places.
- When two apps must cooperate without a cycle.
- When a test can't be written without it.

Otherwise, write the plain function.

## Frontend

The full rules are in README "Frontend assets". In short:

- **Templates** use the component classes in `assets/css/app.css` and logical utilities
  (`ms-`, `text-start`), so one template serves RTL and LTR. They never use `dark:` classes:
  the theme tokens handle dark mode.
- **One ES module per page** in `static/<app>/js/`, loaded in `{% block head %}`. It finds its
  root by id or a `data-` attribute. It reads URLs and translated text from `data-*` attributes
  or `json_script`, and row markup from `<template>`.
- **Shared helpers** live in `static/core/js/` and are imported relatively:
  - `api()` for every request: CSRF, idempotency key, error envelope.
  - `toast()` and `strings()` from `ui.js`.
  - `busy()` and `refreshing()` from `loading.js`.
  - `formatNumber()` from `money.js`.

  Don't write another `fetch` or HTML-escape helper (TD-23).
- **Plain forms** use `<form data-api-form data-endpoint=…>`. Errors appear in
  `[data-error-for="field"]`.
- **No money arithmetic in JS.** Ask the API for a quote.
- **Icons** are `{% icon "name" %}`. A new name must be added to `assets/copy-assets.mjs`,
  followed by `npm run build`.
- **Build output is committed:** `static/core/css/app.css`, `apps/core/ui_icons.json` and
  `static/vendor/`. CI fails if it is stale.

## Recording decisions

A decision that is hard to reverse or that others must follow gets an ADR in
[docs/decisions/](../decisions/README.md). Examples: a new registry, a new dependency, a change
to tenancy, money or identity rules, a new process or service. Propose it in the pull request
that needs it; the ADR is reviewed with the code.
