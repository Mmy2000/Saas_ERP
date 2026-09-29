"""The current tenant, held in two places that must agree (§5.3):

* a ContextVar that the ORM layer (TenantManager, TenantScopedModel.save) reads, and
* the PostgreSQL setting `app.tenant_id` that the RLS policies read.

`tenant_context()` sets both. The DB setting is `SET LOCAL`, so it only lives inside a
transaction; outside one, no tenant row is visible at all. That is the fail-closed default.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import DEFAULT_DB_ALIAS, connections, transaction

_current_tenant_id: ContextVar[int | None] = ContextVar("current_tenant_id", default=None)

DB_SETTING = "app.tenant_id"


class TenantContextError(RuntimeError):
    """Tenant-owned data was touched without a tenant context, or across tenants."""


def get_current_tenant_id() -> int | None:
    return _current_tenant_id.get()


def require_current_tenant_id() -> int:
    tenant_id = _current_tenant_id.get()
    if tenant_id is None:
        raise TenantContextError(
            "No tenant in context. Run this code inside `tenant_context(tenant_id)` "
            "(requests, TenantTask and TenantCommand do this for you)."
        )
    return tenant_id


def _read_db_setting(using: str) -> str:
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [DB_SETTING])
        return cursor.fetchone()[0] or ""


def _write_db_setting(value: str, using: str) -> None:
    with connections[using].cursor() as cursor:
        # is_local=true is SET LOCAL: reverted at transaction (or savepoint) end, which is what
        # makes this safe behind PgBouncer in transaction mode.
        cursor.execute("SELECT set_config(%s, %s, true)", [DB_SETTING, value])


@contextmanager
def tenant_context(tenant_id: int, *, using: str = DEFAULT_DB_ALIAS) -> Iterator[None]:
    """Run the block as `tenant_id`, inside a transaction (a savepoint if one is already open).

    Nesting the same tenant is a no-op. Switching to a different tenant inside another
    tenant's context is refused: one transaction never spans two tenants.
    """
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool):
        raise TypeError(f"tenant_id must be an int, got {type(tenant_id).__name__}")

    outer = _current_tenant_id.get()
    if outer is not None and outer != tenant_id:
        raise TenantContextError(
            f"Already in tenant {outer}; refusing to switch to tenant {tenant_id} inside it."
        )

    token = _current_tenant_id.set(tenant_id)
    try:
        with transaction.atomic(using=using):
            conn = connections[using]
            owns_setting = outer is None
            if owns_setting:
                previous = _read_db_setting(using)
                _write_db_setting(str(tenant_id), using)
            yield
            # On success, put back whatever the enclosing transaction had. On error, or when
            # the block asked for a rollback, the savepoint/transaction rollback reverts the
            # SET LOCAL for us (and no query is allowed on a transaction marked for rollback).
            if owns_setting and not conn.needs_rollback:
                _write_db_setting(previous, using)
    finally:
        _current_tenant_id.reset(token)
