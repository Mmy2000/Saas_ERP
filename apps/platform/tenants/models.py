"""Platform-scope tenant registry (§4.3). No tenant_id, no RLS: only the platform touches these,
plus the host lookup in TenantResolutionMiddleware."""

from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel

RESERVED_SLUGS = frozenset({"admin", "api", "www", "static", "media", "platform", "support"})

slug_validator = RegexValidator(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    "Lowercase letters, digits and hyphens; must start and end with a letter or digit.",
)


class TenantStatus(models.TextChoices):
    PROVISIONING = "provisioning"
    MIGRATING = "migrating"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"
    PURGED = "purged"


class Tenant(TimeStampedModel):
    slug = models.CharField(max_length=63, unique=True, validators=[slug_validator])
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=TenantStatus.choices, default=TenantStatus.PROVISIONING
    )

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return self.slug

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .selectors import forget_hosts

        forget_hosts(self.domains.values_list("domain", flat=True))


class TenantDomain(TimeStampedModel):
    domain = models.CharField(max_length=253, unique=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="domains")
    is_primary = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"], condition=Q(is_primary=True),
                name="tenants_tenantdomain_one_primary_uniq",
            ),
            models.CheckConstraint(
                condition=Q(domain=models.functions.Lower("domain")),
                name="tenants_tenantdomain_lowercase_check",
            ),
        ]

    def __str__(self):
        return self.domain

    def save(self, *args, **kwargs):
        self.domain = self.domain.strip().lower()
        super().save(*args, **kwargs)
        from .selectors import forget_hosts

        forget_hosts([self.domain])

    def delete(self, *args, **kwargs):
        from .selectors import forget_hosts

        forget_hosts([self.domain])
        return super().delete(*args, **kwargs)
