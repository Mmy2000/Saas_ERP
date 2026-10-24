# Common tasks

Short recipes. Each links to the standard that explains the rules. For a whole feature, follow
the [feature playbook](feature-playbook.md).

## Add or change a model

1. Edit `models.py`, following the [database standards](../standards/database.md#models):
   - Inherit `TenantScopedModel` or `Document`.
   - Use `PROTECT` and the standard Decimal precisions.
   - Add constraints and a tenant-leading index.
2. Run `.\venv\Scripts\python.exe manage.py makemigrations <app> --name <what>`.
3. A new model, or a new foreign key to a tenant table, needs `EnableTenantRLS("<Model>")`
   and/or `GuardTenantFKs("<Model>")` in a migration. Add `AuditModel` for master data.
4. Run `manage.py migrate --database owner` and `pytest tests/isolation`.
5. A change to a table that already has data: read the [migration rules](../standards/database.md#migrations).
   Add nullable first, backfill, then make the column required; check `sqlmigrate`.

## Add a business rule

Put it in the service that performs the act, before anything is written:

```python
if amount > left:
    raise _field_error("amount", _("Only %(left)s is left to refund.") % {"left": num(left, 2)})
if voucher.status != DocStatus.POSTED:
    raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
```

- If the rule depends on data another request can change, lock that row first.
- If the rule can be a database constraint, add one too.
- Test the refusal (`pytest.raises(DomainError)` and check `.code`).

## Add an API endpoint

See [API standards](../standards/api.md#checklist-for-a-new-endpoint).

- An action on an existing ViewSet is an `@action(detail=True, methods=["post"])`, with its
  entry in `required_permissions` and `@idempotent` if money or stock moves.
- A new ViewSet goes in the app's router. A new app router is included from
  `config/api_urls.py`.

## Add a permission

```python
# apps/<app>/permissions.py
register_permissions(_("Expenses"), [
    ("expenses.refund.approve", _("Approve refunds")),
])
```

- Codes are lower-case `app.object.action`.
- Translate the label.
- The permission appears in Settings → Roles. Owners hold every permission through the
  wildcard. Grant it to other built-in roles only if the requirement says so; they are seeded
  by `seed_system_roles`.

## Add a page

1. Write a view in `views.py` / `web/` with `@permission_required("code")`. Filter what it shows
   by `request.actor.branch_ids(code)`.
2. Route it in `config/urls.py` with a `kebab-case` name.
3. Add a template in `apps/<app>/templates/<app>/` extending the layout. Use the component
   classes and logical utilities from README "Frontend assets".
4. Writes go through the API: `<form data-api-form data-endpoint="{% url … %}">`.
5. Add the page's JS, if any, as `static/<app>/js/<page>.js`, loaded in `{% block head %}`.
   Use `api()`, `toast()` and `busy()` from `static/core/js/`.
6. Add a sidebar entry in `NAVIGATION` (`apps/core/context_processors.py`) if users navigate to
   it.
7. Add the URL to the page list in `tests/test_i18n_and_pages.py`.
8. After changing templates, rebuild the CSS (`npm run build:css`, or `npm run watch:css` while
   editing) and commit `static/core/css/app.css`.

## Add an icon

1. Add the Lucide name to the `icons` list in `assets/copy-assets.mjs`.
2. Run `npm run build`, then commit `apps/core/ui_icons.json`.
3. Use it as `{% icon "name" "size-4" %}`.

An unknown name raises an error when the page renders (TD-22).

## Add a background job

```python
# apps/<app>/jobs.py
from apps.platform.jobs.registry import JobFailed, daily, task

@task("expenses.remind_unpaid", label=_("Remind about unpaid bills"), max_attempts=3)
def remind_unpaid(voucher_id):
    voucher = ExpenseVoucher.objects.filter(pk=voucher_id).first()
    if voucher is None:
        raise JobFailed("The voucher was deleted.")   # no retry
    ...

daily("expenses.daily_check", at=time(7, 0))          # every client, 07:00 their time
```

- Queue it inside the transaction of the work: `enqueue("expenses.remind_unpaid",
  voucher_id=v.pk)`.
- Payloads carry ids, not personal data.
- Tasks must be safe to run twice: delivery is at least once.
- A client task runs in its tenant's context, language and time zone. A platform task
  (`per_tenant=False`) runs without a tenant.
- `atomic=False` is only for platform tasks that must keep their own records when they fail
  ([ADR-021](../decisions/ADR-021-platform-tasks-outside-a-transaction.md)).
- Test it with `enqueue(...)` + `run_pending()` (`apps/platform/jobs/tests/test_jobs.py`).

## Integrate an external service

1. **Settings.** Read URL, key and timeout from the environment in `config/settings/base.py`,
   with safe defaults. Add them to `.env.example` with a comment, and to the
   [operations](../operations/README.md#configuration) list if production needs them.
2. **One module talks to the service.** Examples: a gateway in `apps/messaging/gateways.py`, a
   source in `apps/platform/market/sources/`. If there are or will be several providers,
   follow the existing Strategy: a base class with one method, and one subclass per provider.
3. **Always set an HTTP timeout.** Catch the provider's errors, and raise a `DomainError` (for a
   user action) or let the job retry.
4. **Never call it inside a transaction that holds locks.** Queue a job.
5. **Development and tests use a console or fake implementation**, as
   `WHATSAPP_CONSOLE` / `CODE_CHANNELS=console` do. Tests mock only the HTTP call.
6. **Never log the key** or the personal data sent.

## Add a report

```python
# in a report module imported by apps/reports/permissions.py
@register
class RefundsByCategory(Report):
    code = "refunds_by_category"            # permission: reports.refunds_by_category.view
    section = "money"
    title = gettext_lazy("Refunds by category")
    filters = (Filter("date_from", "date"), Filter("date_to", "date"), BRANCH_FILTER)

    def run(self, params, scope):
        table = Table(_("Refunds"), [Column("category", _("Category")),
                                     Column("amount", _("Amount"), "money")])
        ...                                 # aggregate in the database; respect `scope` (branches)
        return [table]
```

- The report gets its screen, print, PDF and CSV for free.
- Its permission is registered automatically. Translate the title.
- The report registry is filled by imports in `apps/reports/permissions.py` (TD-08: this should
  move to each app's `ready()`).

## Add a management command that works on tenant data

```python
from apps.core.tenancy.commands import TenantCommand

class Command(TenantCommand):
    help = "…"

    def handle_tenant(self, tenant, **options):   # runs inside tenant_context(tenant.id)
        self.log(f"{Item.objects.count()} pieces")
```

Run it with `--tenant <slug>` or `--all-tenants`; there is no implicit tenant.

## Change configuration

1. Add the setting in `config/settings/base.py` (or `prod.py` if it's production only), read
   from `env(...)` with a default, and a comment saying what it does.
2. Add it to `.env.example` (commented with its default if optional).
3. If operators must set it, add it to [operations → configuration](../operations/README.md#configuration).
4. Tests use `config/settings/test.py`. Override per test with `override_settings`.

## Translations

```powershell
.\venv\Scripts\python.exe ops\i18n\messages.py extract   # new strings into the .po
# translate each new msgstr in locale/ar/LC_MESSAGES/django.po
.\venv\Scripts\python.exe ops\i18n\messages.py compile   # write the .mo (committed)
```

`ops/check.py` (and CI) fails on an untranslated string or a stale `.mo`. Strings in JS come
from the template (`data-*` attributes, `json_script`), so they are extracted from there.

## Debugging common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `TenantContextError` | tenant data touched outside `tenant_context` (a shell, a script, a thread) | wrap in `with tenant_context(tenant.id):`, or write a `TenantCommand` |
| Rows "disappear" in a shell or query | RLS: no `app.tenant_id`, or connected as the wrong role | use `tenant_context`; connect as `app_owner`/`app_rw`, never as a superuser (which would show everything and hide bugs) |
| API answers 403 for everyone | the action is missing from `required_permissions` | declare it; `test_every_api_action_declares_a_permission` names it |
| API answers 400 `IDEMPOTENCY_KEY_REQUIRED` | an `@idempotent` action called without the header | call through `api()` in JS, send `Idempotency-Key` from other clients |
| `test_every_tenant_foreign_key_is_guarded` fails | new foreign key without `GuardTenantFKs` in the migration | add `GuardTenantFKs("<Model>")` to a migration |
| `test_no_byte_order_marks` fails | a file saved with a BOM (PowerShell 5.1 `Set-Content -Encoding utf8`) | re-save as UTF-8 without BOM |
| Translation check fails | new string not translated, or `.mo` not compiled | `messages.py extract`, translate, `compile` |
| Styles missing after editing a template | CSS not rebuilt | `npm run build:css` and commit `static/core/css/app.css` |
| PDF tests skipped | Chromium not installed | `python -m playwright install chromium` |
| Tests can't create the database | role lacks `CREATEDB`, or wrong `DATABASE_OWNER_URL` | run `ops/db/bootstrap.sql`; check `.env` |
| A page 500s with `KeyError` in `icon` | icon name not bundled | add it to `assets/copy-assets.mjs`, `npm run build` |
| "Too many wrong passwords" while testing locally | 5 wrong passwords for one account (or 30 from one address) in 15 minutes (`apps/iam/throttle.py`) | wait, or restart `runserver` (development keeps the counts in memory) |
| A production `manage.py` command uses dev settings | `DJANGO_SETTINGS_MODULE` not exported in that shell | export it (see `.env.example`) |
| A job keeps failing | see Console → Jobs (last error) and Console → Errors (traceback) | fix the cause; `JobFailed` for hopeless cases |
| An error reported by a user | — | Console → Errors lists each error with its client, user, page and last time; request ids aren't stored there yet (TD-17), so match by time and page |
