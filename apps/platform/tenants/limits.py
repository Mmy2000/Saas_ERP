"""Plan limits set in the platform console, checked where tenants add branches and users."""

from __future__ import annotations

from django.utils.translation import gettext as _

from apps.core.errors import DomainError
from apps.core.tenancy import get_current_tenant_id

from .models import Tenant


def _tenant() -> Tenant | None:
    tenant_id = get_current_tenant_id()
    return (Tenant.objects.select_related("plan").filter(pk=tenant_id).first()
            if tenant_id else None)


def check_branch_limit() -> None:
    from apps.org.models import Branch

    tenant = _tenant()
    limit = tenant.branch_limit if tenant else None
    if limit is not None and Branch.objects.filter(is_active=True).count() >= limit:
        raise DomainError(
            _("Your plan allows %(limit)s branches. Contact us to add more.")
            % {"limit": limit}, code="PLAN_BRANCH_LIMIT")


def check_user_limit() -> None:
    from apps.iam.models import Membership, MembershipStatus

    tenant = _tenant()
    limit = tenant.user_limit if tenant else None
    if limit is not None and (
            Membership.objects.filter(status=MembershipStatus.ACTIVE).count() >= limit):
        raise DomainError(
            _("Your plan allows %(limit)s users. Contact us to add more.")
            % {"limit": limit}, code="PLAN_USER_LIMIT")
