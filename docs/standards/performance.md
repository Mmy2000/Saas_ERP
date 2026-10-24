# Performance

Measure before optimising. The usual problems here are queries, not Python.

## Rules

- **Queries per page stay constant as data grows.** No query inside a loop over rows: use
  `select_related` / `prefetch_related`, or aggregate in the database (`Sum`, `Count`,
  `values().annotate()`). The audit found no serious N+1 problems; keep it so.
  - Check a page or report with `CaptureQueriesContext` in a test, with two and with ten rows:
    the count must be the same.
- **Lists are paginated** (API: 25 per page, 100 maximum; web lists through
  `apps/core/listing.py`). A list that loads everything to find the latest row per group should
  use the database: `distinct("cash_box_id")` with `order_by`. `apps/treasury/views.py:344`
  still loads every posted cash count to find each box's latest.
- **Indexes for every filter used by lists and reports**, starting with `tenant`. Documents
  already have `(tenant, branch, -business_date)`.
- **Balances come from `BalanceProjection`**, maintained by `post_entry`. Don't recompute them
  by summing journal lines on a page.
- **Keep transactions short.** Never wait on HTTP, SMTP or a subprocess inside a transaction
  that holds row locks. Queue a job.
- **Cache** only platform-wide data, or per-tenant data with the tenant in the key. The
  tenant-prefixed cache wrapper isn't built yet (README "Deliberately not done yet"). The cache
  is per server unless `CACHE_URL` points at a shared one.

## Where time goes today

From the baseline run (`pytest --durations`) and the audit:

- **Each request does two upserts** for traffic counting (`apps/platform/tenants/traffic.py`).
  This is fine now; watch it under load.
- **The year view of closing** runs 24 date-range sums over journal lines, without a
  `(tenant, business_date)` index (TD-33).
- **PDFs** start headless Chromium (`apps/printing/pdf.py`), at about a second each. They are
  generated on demand, not in bulk.
- **`CONN_MAX_AGE=0`** opens a database connection per request. Set `DB_CONN_MAX_AGE` (for
  example 60) in production once you have checked the PostgreSQL connection limit against
  gunicorn's workers.

## When something is slow

1. Reproduce with realistic data. A copy of production is restored without personal data, or a
   tenant built by the opening import.
2. Count queries (`CaptureQueriesContext`, `connection.queries` with `DEBUG`) and read their
   plans (`EXPLAIN ANALYZE`, as `app_owner` inside `SET LOCAL app.tenant_id`).
3. Fix the query or the index first. Add caching last, with an invalidation story.
4. Keep the test that proves the query count.
