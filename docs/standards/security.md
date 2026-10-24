# Security standards

The platform holds many shops' money, stock and customers' personal data in one database.
Security here mostly means: **one client never sees another's data, and nobody does what their
role doesn't allow.** These rules are part of normal work and of every code review.

## Tenant isolation (never weaken)

- Tenant data is reached only inside `tenant_context`: requests, `TenantCommand`
  (`--tenant`/`--all-tenants`) and worker jobs provide it. Don't open one yourself in request
  code.
- The tenant comes **only from the host**, never from a parameter, header or body field.
- Every tenant model has `TenantScopedModel`, a tenant-leading index, `EnableTenantRLS` and
  `GuardTenantFKs`. The isolation suite enforces this; never skip or weaken a test in
  `tests/isolation/`.
- The application's database role (`app_rw`) cannot bypass RLS. Never give it `BYPASSRLS`, and
  never connect the application as `app_owner` or a superuser.
- Platform code reads a client's data only inside that client's `tenant_context`. A query across
  clients belongs in `apps/platform`, on platform tables, or in the backup.
- Files: tenant uploads live under `tenants/<id>/` and are served only by `apps.core.media.serve`,
  which checks host and membership. Never let nginx serve `MEDIA_ROOT`.

## Authentication

- Workspace members sign in by username per workspace. Platform staff sign in on platform hosts
  (`is_platform_staff`).
- Passwords: Argon2, minimum 10 characters, Django's validators.
- Sessions are bound to their tenant, cookies are host-only, and production sets `Secure`,
  `HttpOnly` and `SameSite`.
- Mobile tokens (`apps/mobile/tokens.py`):
  - Access tokens last 7 minutes and are bound to tenant and device, checked against the
    database.
  - Refresh tokens are stored hashed and rotated on every use.
- **Wrong passwords are throttled** (`apps/iam/throttle.py`) on every way in: 5 for one
  account, or 30 from one address, lock it for up to 15 minutes. With several servers,
  `CACHE_URL` must be a shared cache, or each server counts on its own.
- **A login used by several workspaces, or by staff, is changed only by its owner**
  ([ADR-022](../decisions/ADR-022-identity-across-workspaces.md)):
  - A workspace can't set its password, email or phone.
  - A new workspace joins an existing login only when staff ask for it, and never a staff
    login.
- **Open gap:** no second factor for platform staff yet (TD-25). Staff passwords must be long
  and unique.

## Authorization

- Every web view has `@permission_required("code")`. Every API action declares
  `required_permissions`, and views keep `HasTenantPermission` (both automated).
- Every list is filtered with `actor.branch_ids(code)`, and every write re-checks with
  `actor.require(code, branch=...)` inside the service. An id in the URL is not proof of access:
  load the object through the branch-filtered queryset.
- Role changes can't grant more than the actor holds, and the last owner can't be removed
  (`apps/iam/services.py`). Keep these guards when touching roles.
- Feature-gated modules (Diamonds) check the feature as well as the permission.

## Input and output

- Validate input in serializers (shape) and services (rules). Never trust a price, total or
  quote sent by the browser: the server computes it again.
- SQL: ORM, or `cursor.execute(sql, params)` with `%s` placeholders. Never format values into
  SQL (ruff `S608`).
- HTML: Django autoescaping stays on. `mark_safe` / `|safe` only for markup built from escaped
  or validated parts, with a comment saying why. Data for scripts goes through `json_script`,
  and JS sets `textContent`, or escapes before `innerHTML`.
- Custom document HTML (staff only) is rendered by a sandboxed template engine with no loaders
  (`apps/printing/render.py`). Clients can't set it.
- CSV and Excel exports: see TD-30 (formula cells).
- CSRF: never `csrf_exempt`. The API uses session authentication, which enforces CSRF.
- State changes only on POST (`@require_POST`) or the API's unsafe methods.

## Files and uploads

- Check content by its bytes (magic numbers), not its name or `Content-Type`. Refuse SVG and
  HTML uploads.
- Store under a random name. TD-30: the extension should come from the detected type.
- Serve with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`, as
  `media.serve` does.
- Keep paths away from client input: no `..`, `\` or `:` (`media.serve`).

## Secrets and configuration

- Secrets come from the environment (`.env`, git-ignored; template in `.env.example`). Never
  commit one, never put one in a test, a fixture or documentation.
- Production refuses to start without `DJANGO_SECRET_KEY`, `FIELD_ENCRYPTION_KEY` and
  `BLIND_INDEX_KEY`. Keep a copy of the two encryption keys outside the server: backups don't
  contain them, and without them national IDs can't be read.
- National IDs are encrypted (Fernet) with a blind index for search (`apps/core/crypto.py`).
  Any new personal identifier goes the same way.
- Development keys are derived from `SECRET_KEY` (`config/settings/dev.py`). Never run
  production commands with development settings (`.env.example` explains
  `DJANGO_SETTINGS_MODULE`).

## Logging and personal data

- Never log passwords, tokens, codes, national IDs or full card data. Log ids, not personal
  data. Job payloads carry ids, not personal data (`enqueue` docstring).
- One-time codes appear in logs only on the development `console` channel.
- Errors shown to users carry a message and a request id, never a traceback (see
  [errors and logging](errors-and-logging.md)).

## Dependencies

- Python packages are pinned with `==` in `requirements/`. npm packages are build tools only,
  locked by `package-lock.json`.
- CI runs `pip-audit` and `npm audit` on every pull request and every Monday (advisory). Upgrade
  a flagged package in its own pull request with the full test run.
- A new dependency needs a reason in the pull request: what it does that the standard library
  or an existing dependency can't. Prefer none.

## Production settings

`config/settings/prod.py` forces `DEBUG` off and sets:

- SSL redirect, HSTS with subdomains (30 days), and a proxy SSL header.
- Secure cookies.
- `X_FRAME_OPTIONS = DENY`.

`manage.py check --deploy --settings=config.settings.prod` reports only W021 (HSTS preload,
deliberately off until stable). Run it after changing settings.

## Review checklist

- [ ] Tenant: no new path to data outside `tenant_context`; new models have RLS + guard.
- [ ] Permission declared and enforced; branch-filtered queryset; `actor.require` in the
  service.
- [ ] No string-built SQL, no unexplained `mark_safe`, no `csrf_exempt`, state changes on POST.
- [ ] No secret or personal data in code, logs, payloads or error messages.
- [ ] Uploads checked by content and served through `media.serve`.
- [ ] New dependency justified.
