"""Authorization checks (§14.2–14.3).

`Actor` is what a request, job or import acts as: a member plus the effective permission map
of all their role assignments. `actor.can(code, branch)` is the one question everything asks:
DRF permission classes, web views, services (`actor.require`) and templates (`{% can %}`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.utils.translation import gettext as _

from apps.core.errors import PermissionDenied

from .catalog import AUTHENTICATED, WILDCARD, limits

ALL_BRANCHES = None  # scope value meaning "every branch"


def _merge(current, new):
    if current is ALL_BRANCHES or new is ALL_BRANCHES:
        return ALL_BRANCHES
    return current | new


@dataclass
class Actor:
    user: object
    membership: object
    grants: dict[str, frozenset[int] | None] = field(default_factory=dict)
    limit_values: dict[str, Decimal] = field(default_factory=dict)

    def _scopes(self, code: str):
        for key in (code, WILDCARD):
            if key in self.grants:
                yield self.grants[key]

    def can(self, code: str, branch=None) -> bool:
        if code == AUTHENTICATED:
            return True
        branch_id = getattr(branch, "pk", branch)
        for scope in self._scopes(code):
            if scope is ALL_BRANCHES or branch_id is None or branch_id in scope:
                return True
        return False

    def require(self, code: str, branch=None) -> None:
        if not self.can(code, branch):
            raise PermissionDenied(_("You do not have permission to do this."),
                                   code="PERMISSION_DENIED")

    def branch_ids(self, code: str) -> frozenset[int] | None:
        """Branches where `code` is granted (None = all). Used to scope list queries."""
        result: frozenset[int] | None = frozenset()
        for scope in self._scopes(code):
            result = _merge(result, scope)
        return result

    def limit(self, key: str) -> Decimal:
        """A member's numeric limit (e.g. max discount rate). Explicit values win; Owners
        (wildcard) are otherwise unlimited (1 = 100 %); everyone else gets the default."""
        if key in self.limit_values:
            return self.limit_values[key]
        if WILDCARD in self.grants:
            return Decimal(1)
        return Decimal(limits()[key].default)


def build_actor(membership) -> Actor:
    from .models import MembershipRole

    grants: dict[str, frozenset[int] | None] = {}
    assignments = (
        MembershipRole.objects.filter(membership=membership)
        .select_related("role")
        .prefetch_related("role__grants", "branches")
    )
    for assignment in assignments:
        scope = (ALL_BRANCHES if assignment.all_branches
                 else frozenset(b.branch_id for b in assignment.branches.all()))
        for grant in assignment.role.grants.all():
            grants[grant.permission] = (_merge(grants[grant.permission], scope)
                                        if grant.permission in grants else scope)

    limit_values = {lim.key: lim.value for lim in membership.limits.all()}
    return Actor(user=membership.user, membership=membership, grants=grants,
                 limit_values=limit_values)


def permission_required(code: str):
    """Web-view decorator: login, active membership, and `code` in any branch."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            actor = getattr(request, "actor", None)
            if actor is None or not actor.can(code):
                raise DjangoPermissionDenied
            return view(request, *args, **kwargs)

        wrapped.required_permission = code
        return wrapped

    return decorator
