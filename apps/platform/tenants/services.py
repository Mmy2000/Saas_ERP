"""Tenant provisioning (§5.7)."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context

from .models import RESERVED_SLUGS, Tenant, TenantDomain, TenantStatus, ensure_plans


@dataclass(frozen=True)
class ProvisionTenantCommand:
    slug: str
    name: str
    domain: str
    owner_username: str
    owner_password: str | None
    owner_email: str | None = None
    owner_display_name: str = ""
    head_office_name: str | None = None  # defaults to "Head office" in the tenant's language
    functional_currency: str = "EGP"
    timezone: str = "Africa/Cairo"
    locale: str = "ar"
    country: str = "EG"  # ISO 3166 alpha-2; default region for phone numbers
    fineness_24k: str | None = None  # default 999.9; some clients use 1000 (OQ-8)
    activate: bool = True


def provision_tenant(cmd: ProvisionTenantCommand) -> Tenant:
    """Create a tenant with its primary domain, profile, head-office branch, reference data,
    system roles and owner.

    The owner is a global User (reused if one with that email exists; the password is then
    left untouched) plus a Membership carrying the tenant-local username and the Owner role.
    Seeded names (karats, roles, head office) are written in the tenant's language.
    """
    from apps.catalog.services import seed_reference_data
    from apps.iam.models import Membership, User
    from apps.iam.services import OWNER, assign_role, seed_system_roles
    from apps.ledger.services import seed_ledger
    from apps.org.models import Branch, TenantProfile

    slug = cmd.slug.strip().lower()
    if slug in RESERVED_SLUGS:
        raise ValidationError(_("'%(slug)s' is reserved.") % {"slug": slug},
                              fields={"slug": [_("Reserved.")]})

    with transaction.atomic(), translation.override(cmd.locale):
        ensure_plans()
        tenant = Tenant(slug=slug, name=cmd.name, status=TenantStatus.PROVISIONING)
        tenant.full_clean()
        tenant.save()
        TenantDomain.objects.create(
            tenant=tenant, domain=cmd.domain, is_primary=True, verified_at=timezone.now()
        )

        with tenant_context(tenant.id):
            TenantProfile.objects.create(
                display_name=cmd.name,
                legal_name=cmd.name,
                functional_currency=cmd.functional_currency,
                timezone=cmd.timezone,
                locale=cmd.locale,
                country=cmd.country.upper(),
            )
            head_office = Branch.objects.create(
                code=1, name=cmd.head_office_name or _("Head office"), is_head_office=True
            )
            seed_reference_data(functional_currency=cmd.functional_currency,
                                fineness_24k=cmd.fineness_24k)
            seed_ledger(cmd.functional_currency)
            roles = seed_system_roles()

            user = None
            if cmd.owner_email:
                user = User.objects.filter(email__iexact=cmd.owner_email).first()
            if user is None:
                user = User.objects.create_user(
                    email=cmd.owner_email,
                    password=cmd.owner_password,
                    display_name=cmd.owner_display_name or cmd.owner_username,
                )
            owner = Membership.objects.create(
                user=user, username=cmd.owner_username, default_branch=head_office
            )
            assign_role(owner, roles[OWNER])

        if cmd.activate:
            tenant.status = TenantStatus.ACTIVE
            tenant.save(update_fields=["status", "updated_at"])

    return tenant
