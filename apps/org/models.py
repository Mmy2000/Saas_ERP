"""Organisation structure of a tenant (§7.1)."""

from __future__ import annotations

import zoneinfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TenantScopedModel


def validate_timezone(value: str) -> None:
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"Unknown time zone: {value}") from exc


class TenantProfile(TenantScopedModel):
    """The tenant's business identity and locale. Legacy source: `Cod.com` + singletons."""

    display_name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    tax_registration_no = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp_number = models.CharField(max_length=30, blank=True)
    logo = models.FileField(upload_to="tenant-logos/", blank=True)
    functional_currency = models.CharField(max_length=3, default="EGP")
    timezone = models.CharField(max_length=64, default="Africa/Cairo",
                                validators=[validate_timezone])
    locale = models.CharField(max_length=8, default="ar", choices=settings.LANGUAGES)
    country = models.CharField(max_length=2, default="EG")  # ISO 3166; phone number region

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant"], name="org_tenantprofile_tenant_uniq"),
        ]

    def __str__(self):
        return self.display_name


class Branch(TenantScopedModel):
    """A shop or office. Legacy source: `Cod.Br1` (`br` → code, `brna` → name, `isdim`)."""

    code = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=200)
    is_head_office = models.BooleanField(default=False)
    handles_diamonds = models.BooleanField(default=False)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="org_branch_code_uniq"),
            models.UniqueConstraint(
                fields=["tenant"], condition=Q(is_head_office=True),
                name="org_branch_one_head_office_uniq",
            ),
            models.CheckConstraint(condition=Q(code__gte=1, code__lte=999),
                                   name="org_branch_code_range_check"),
        ]

    def __str__(self):
        return f"{self.code:02d} {self.name}"
