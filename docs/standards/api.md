# API standards

The JSON API under `/api/v1/` on tenant hosts serves the web pages' forms and scripts, and is the
contract for any other client. This page describes the contract as implemented (in
`apps/core/api/`) and the rules new endpoints follow.

## Versioning and routes

- **Version 1 is in the path:** `config/urls.py` mounts `api/v1/` → `config/api_urls.py`. Each
  app has a DRF router (`apps/<app>/api.py` or `api/urls.py`).
- **Route names:** plural nouns, with actions as sub-paths, e.g.
  `POST /api/v1/expenses/vouchers/{id}/void/`.
- **The mobile app has legacy, unversioned routes** (`/api/auth/`, `/Sup/api/`, `/Sup1/api/`)
  whose response shapes are fixed for the installed app (`apps/mobile/legacy.py`). They are the
  only exception to this page.
- **Breaking a v1 response** means removing or renaming a field, or changing a type or status
  code. That needs either a new field alongside the old one, or `/api/v2/` for that resource,
  plus an ADR. Adding fields is not breaking.

## Authentication and permissions

- **Session authentication** (the signed-in browser). Unsafe methods need the CSRF token in
  `X-CSRFToken`; `static/core/js/api.js` sends it. The mobile app uses its own Bearer tokens
  (`apps/mobile/tokens.py`).
- **Every view keeps the default permission class `HasTenantPermission`, and declares
  `required_permissions` for every action:**

  ```python
  required_permissions = {
      "list": "expenses.view", "retrieve": "expenses.view",
      "create": "expenses.voucher.create", "void": "expenses.void",
  }
  ```

  - An undeclared action is denied.
  - `AUTHENTICATED` (from `apps.iam.catalog`) means any signed-in member.
  - Two tests enforce this (automated, `apps/iam/tests/test_authz.py`): every action declares a
    permission, and no `/api/v1/` view replaces `HasTenantPermission` (a library view did, and
    published the schema).
- **Filter querysets by branch:**

  ```python
  def get_queryset(self):
      branches = self.request.actor.branch_ids("expenses.view")
      qs = super().get_queryset()
      return qs if branches is None else qs.filter(branch_id__in=branches)
  ```

  The service checks the write again with `actor.require(code, branch=...)`.

## Requests

- **Parse with a write serializer** (`serializers.Serializer`, not the model serializer). Then
  call one service with an `XInput`, and return the result through the read serializer:

  ```python
  @idempotent
  def create(self, request):
      payload = VoucherWriteSerializer(data=request.data)
      payload.is_valid(raise_exception=True)
      d = payload.validated_data
      voucher = services.post_expense(services.ExpenseInput(branch_id=d["branch"], ...),
                                      actor=request.actor)
      return Response(VoucherSerializer(voucher).data, status=201)
  ```

- **No business logic in the view or serializer.** Field-level shape checks belong in the
  serializer (type, required, max length). Business rules belong in the service.
- **Decimals:** clients send them as strings (`"18392.85"`). The renderer writes every Decimal
  as a string (`apps/core/api/renderers.py`), and JSON never contains floats.

## Idempotency

Every action that moves money, gold or stock is decorated with `@idempotent`
(`apps/core/api/idempotency.py`):

| Situation | Result |
|---|---|
| No `Idempotency-Key` header, or longer than 64 characters | `400 IDEMPOTENCY_KEY_REQUIRED` |
| First request with a key | runs; a 2xx response is stored |
| Same key again, same path | the stored response, with `Idempotent-Replay: true` |
| Same key, still running | `409 IDEMPOTENCY_IN_PROGRESS` |
| Same key, another path | `409 IDEMPOTENCY_KEY_REUSED` |
| The action failed (non-2xx or exception) | the key is released; retry with the same key |

Keys are per tenant and user.
- **Clients:** `api()` creates a UUID per call unless given `idempotencyKey`. Screens that
  retry the same document keep one key for it (`static/sales/js/pos.js`).
- **Known gaps (TD-15):** keys don't compare the request body and never expire, and manual
  journal entries and purchase drafts aren't covered.

## Responses

**Success:** the resource as JSON (fields from the read serializer), `200`, or `201` on create.

```json
{"id": 41, "number": "01-EX-2026-000003", "status": "posted", "amount": "1500.00",
 "currency": "EGP", "business_date": "2026-10-24", "category": {"id": 2, "name": "Rent"}}
```

**Lists** are paginated (`apps/core/api/pagination.py`): `?page=`, `?page_size=` (default 25,
maximum 100).

```json
{"count": 132, "next": "https://shop.example.com/api/v1/expenses/vouchers/?page=2",
 "previous": null, "results": [ ... ]}
```

**Filtering** uses `django-filter` through `filterset_fields`, e.g.
`?status=posted&business_date__gte=2026-10-01`. Declare the fields a list may be filtered on.
Don't hand-parse query strings.

## Errors

Every error from an API view has one shape (`apps/core/api/exceptions.py`):

```json
{"error": {"code": "VALIDATION_ERROR",
           "message": "Please correct the highlighted fields.",
           "fields": {"amount": ["Must be greater than zero."]},
           "request_id": "8f0c2b6e3a1d4c59"}}
```

| Raised in the service | HTTP | `code` |
|---|---|---|
| `ValidationError(msg, fields={...})` | 400 | `VALIDATION_ERROR` (or the code given) |
| DRF serializer validation | 400 | `VALIDATION_ERROR`; nested `fields` keep their shape (`{"lines": [{}, {"karat": ["…"]}]}`) |
| not signed in | 401/403 | `NOT_AUTHENTICATED` |
| `PermissionDenied` / missing permission | 403 | `PERMISSION_DENIED` |
| `NotFound` | 404 | `NOT_FOUND` |
| `Conflict` (stale state, idempotency) | 409 | `CONFLICT` or specific |
| `DomainError(msg, code="SALES_DISCOUNT_LIMIT")` | 422 | the specific code |
| over the client's request limit | 429 | `THROTTLED`, with `Retry-After` |
| anything unexpected | 500 | **HTML page today** (TD-18) |

Rules:
- **`message` is safe for the user, translated, and never contains a stack trace, SQL or
  internal ids.** Unexpected errors are logged with their traceback in Console → Errors; the
  client gets only the `request_id` to quote.
- **Give a `DomainError` a specific, stable `code`**: clients and tests branch on the code,
  never on the message text.
- **Field names in `fields` are the request's field names**, so `[data-error-for="amount"]`
  finds them.

## The schema

`GET /api/v1/schema/` returns the OpenAPI schema (drf-spectacular) to signed-in members. Hand-
written `APIView`s and `@action`s may be incomplete there. Add `@extend_schema` when an
endpoint is meant for other clients.

## Checklist for a new endpoint

- [ ] Route in the app's router, plural noun, action as a sub-path.
- [ ] `required_permissions` for every action; branch-filtered `get_queryset`.
- [ ] Write serializer → one service call → read serializer.
- [ ] `@idempotent` if it moves money, gold or stock.
- [ ] Errors raised as `apps.core.errors`, with a specific code for business rules.
- [ ] Tests: success, validation error, permission refused (and branch refused), replay with
  the same key.
