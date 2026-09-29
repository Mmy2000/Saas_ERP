"""Fail-closed ORM scoping for tenant-owned models (§5.3, "ORM" layer).

A TenantQuerySet is bound to a tenant when it runs, not when it is built. So a queryset built
at import time (for example `queryset = Branch.objects.all()` in a DRF view class body, which
may first be imported in the middle of some tenant's request) serves each request's own
tenant. Running it with no tenant context raises TenantContextError instead of returning rows.

Operations that cache nothing (count, update, subqueries…) run on a bound copy, leaving the
original untouched. Iteration binds the queryset in place, because its result cache then
belongs to that tenant: reading the cache from another tenant's context raises.

PostgreSQL RLS enforces the same rule underneath; this layer exists so a mistake fails loudly
in Python rather than silently returning an empty result.
"""

from __future__ import annotations

from django.db import models

from .context import TenantContextError, require_current_tenant_id


class TenantQuerySet(models.QuerySet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tenant_id: int | None = None

    # --- binding --------------------------------------------------------------------------

    def _bind_tenant(self) -> None:
        """Filter this queryset (in place) to the current tenant, or check it already is."""
        tenant_id = require_current_tenant_id()
        if self._tenant_id is None:
            self._filter_or_exclude_inplace(False, (), {"tenant_id": tenant_id})
            self._tenant_id = tenant_id
        elif self._tenant_id != tenant_id:
            raise TenantContextError(
                f"QuerySet was evaluated for tenant {self._tenant_id} but is being used "
                f"in tenant {tenant_id}."
            )

    def _bound(self) -> TenantQuerySet:
        """A copy bound to the current tenant; self is left as it was."""
        clone = self._chain()
        clone._bind_tenant()
        return clone

    def _clone(self):
        clone = super()._clone()
        clone._tenant_id = self._tenant_id
        return clone

    # --- reads that fill the result cache: bind in place ------------------------------------

    def _fetch_all(self):
        self._bind_tenant()
        super()._fetch_all()

    def __getitem__(self, k):
        if self._result_cache is not None:
            self._bind_tenant()
        return super().__getitem__(k)

    # --- everything else that reaches the database: run on a bound copy ---------------------

    def iterator(self, *args, **kwargs):
        return super(TenantQuerySet, self._bound()).iterator(*args, **kwargs)

    def count(self):
        if self._result_cache is not None:
            self._bind_tenant()
            return len(self._result_cache)
        return super(TenantQuerySet, self._bound()).count()

    def exists(self):
        if self._result_cache is not None:
            self._bind_tenant()
            return bool(self._result_cache)
        return super(TenantQuerySet, self._bound()).exists()

    def contains(self, obj):
        return super(TenantQuerySet, self._bound()).contains(obj)

    def aggregate(self, *args, **kwargs):
        return super(TenantQuerySet, self._bound()).aggregate(*args, **kwargs)

    def in_bulk(self, *args, **kwargs):
        return super(TenantQuerySet, self._bound()).in_bulk(*args, **kwargs)

    def explain(self, *args, **kwargs):
        return super(TenantQuerySet, self._bound()).explain(*args, **kwargs)

    def update(self, **kwargs):
        rows = super(TenantQuerySet, self._bound()).update(**kwargs)
        self._result_cache = None
        return rows

    def delete(self):
        result = super(TenantQuerySet, self._bound()).delete()
        self._result_cache = None
        return result

    def resolve_expression(self, *args, **kwargs):  # used as a subquery
        return super(TenantQuerySet, self._bound()).resolve_expression(*args, **kwargs)

    def _combinator_query(self, combinator, *other_qs, **kwargs):  # union/intersection/difference
        others = [qs._bound() if isinstance(qs, TenantQuerySet) else qs for qs in other_qs]
        combined = super(TenantQuerySet, self._bound())._combinator_query(
            combinator, *others, **kwargs
        )
        combined._tenant_id = require_current_tenant_id()  # parts are filtered; not the outer
        return combined

    # --- writes -----------------------------------------------------------------------------

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj._stamp_tenant()
        return super().bulk_create(objs, *args, **kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    def unscoped(self) -> models.QuerySet:
        """A plain QuerySet with no tenant filter. Platform and migration code only.

        It is still subject to RLS unless the connection uses the platform (BYPASSRLS) role.
        """
        return models.QuerySet(self.model, using=self._db)


__all__ = ["TenantContextError", "TenantManager", "TenantQuerySet"]
