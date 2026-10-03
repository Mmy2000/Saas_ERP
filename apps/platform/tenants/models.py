"""Platform-scope tenant registry (§4.3). No tenant_id, no RLS: only the platform touches these,
plus the host lookup in TenantResolutionMiddleware."""

from __future__ import annotations

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

RESERVED_SLUGS = frozenset({"admin", "api", "www", "static", "media", "platform", "support"})

slug_validator = RegexValidator(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    "Lowercase letters, digits and hyphens; must start and end with a letter or digit.",
)


class TenantStatus(models.TextChoices):
    PROVISIONING = "provisioning", _("Being set up")
    MIGRATING = "migrating", _("Migrating")
    ACTIVE = "active", _("Active")
    SUSPENDED = "suspended", _("Suspended")
    ARCHIVED = "archived", _("Archived")
    PURGED = "purged", _("Purged")


class TenantPlan(models.TextChoices):
    TRIAL = "trial", _("Trial")
    STANDARD = "standard", _("Standard")
    PROFESSIONAL = "professional", _("Professional")
    ENTERPRISE = "enterprise", _("Enterprise")


class Tenant(TimeStampedModel):
    slug = models.CharField(max_length=63, unique=True, validators=[slug_validator])
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=TenantStatus.choices, default=TenantStatus.PROVISIONING
    )
    # Commercial settings, managed from the platform console.
    plan = models.CharField(max_length=16, choices=TenantPlan.choices,
                            default=TenantPlan.STANDARD)
    trial_ends_on = models.DateField(null=True, blank=True)
    max_branches = models.PositiveIntegerField(null=True, blank=True)  # None = no limit
    max_users = models.PositiveIntegerField(null=True, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)

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


EVENT_LABELS = {
    "tenant.created": _("Client created"),
    "tenant.updated": _("Plan and details changed"),
    "tenant.status": _("Status changed"),
    "profile.updated": _("Company settings changed"),
    "domain.added": _("Domain added"),
    "domain.removed": _("Domain removed"),
}


class PlatformEvent(models.Model):
    """What platform staff did in the console (append-only by convention)."""

    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="events")
    action = models.CharField(max_length=40)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "-created_at"], name="platform_event_tenant_idx")]

    def __str__(self):
        return f"{self.action} {self.tenant_id or ''}"

    @property
    def label(self) -> str:
        return str(EVENT_LABELS.get(self.action, self.action))
