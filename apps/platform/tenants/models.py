"""Platform-scope tenant registry (§4.3). No tenant_id, no RLS: only the platform touches these,
plus the host lookup in TenantResolutionMiddleware."""

from __future__ import annotations

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.media import PlatformUploadPath, validate_image
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


class BillingPeriod(models.TextChoices):
    MONTHLY = "monthly", _("Monthly")
    YEARLY = "yearly", _("Yearly")


plan_code_validator = RegexValidator(
    r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$",
    _("Lowercase letters, digits and hyphens."),
)


class Plan(TimeStampedModel):
    """A commercial plan, managed in the console (Plans). Clients on a plan get its features
    (PlanFeature, else on) and its limits unless the client has its own."""

    code = models.CharField(max_length=32, unique=True, validators=[plan_code_validator])
    name = models.CharField(max_length=100)  # English
    name_ar = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="EGP")
    billing_period = models.CharField(max_length=8, choices=BillingPeriod.choices,
                                      default=BillingPeriod.MONTHLY)
    # Defaults for clients on the plan; a client's own value wins. None = no limit (for the
    # request rate: the platform default, TENANT_REQUESTS_PER_MINUTE).
    max_branches = models.PositiveIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    requests_per_minute = models.PositiveIntegerField(null=True, blank=True)
    trial_days = models.PositiveIntegerField(null=True, blank=True)  # sets a new client's trial end
    is_active = models.BooleanField(default=True)  # offered for new clients
    is_default = models.BooleanField(default=False)  # preselected for new clients
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True),
                                               name="tenants_plan_one_default_uniq")]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        from django.utils.translation import get_language

        if (get_language() or "").startswith("ar") and self.name_ar:
            return self.name_ar
        return self.name

    def validate_constraints(self, exclude=None):
        # Making a plan the default un-defaults the old one in save(); the database constraint
        # still guarantees a single default.
        super().validate_constraints(exclude={*(exclude or ()), "is_default"})

    def save(self, *args, **kwargs):
        if self.is_default:
            Plan.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)
        from .features import forget

        forget()


STARTER_PLANS = [  # (code, name, Arabic name): also created by migration 0006_plans
    ("trial", "Trial", "تجريبي"),
    ("standard", "Standard", "قياسية"),
    ("professional", "Professional", "احترافية"),
    ("enterprise", "Enterprise", "مؤسسية"),
]


def ensure_plans() -> None:
    """A database with no plan at all (e.g. after a flush) gets the starter plans, so a
    client can always be created."""
    if Plan.objects.exists():
        return
    for position, (code, name, name_ar) in enumerate(STARTER_PLANS):
        Plan.objects.create(code=code, name=name, name_ar=name_ar, position=position,
                            is_default=code == "standard")


def default_plan_code() -> str:
    plan = Plan.objects.filter(is_default=True).first() or Plan.objects.first()
    return plan.code if plan else "standard"


class Tenant(TimeStampedModel):
    slug = models.CharField(max_length=63, unique=True, validators=[slug_validator])
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=TenantStatus.choices, default=TenantStatus.PROVISIONING
    )
    # Commercial settings, managed from the platform console.
    plan = models.ForeignKey(Plan, to_field="code", db_column="plan", on_delete=models.PROTECT,
                             related_name="tenants", default=default_plan_code)
    trial_ends_on = models.DateField(null=True, blank=True)
    # The client's own limits; None = the plan's (and then no limit).
    max_branches = models.PositiveIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    # Requests per minute before the workspace answers 429. None = the plan's, then the
    # platform default (TENANT_REQUESTS_PER_MINUTE); 0 = no limit. See tenants.traffic.
    requests_per_minute = models.PositiveIntegerField(null=True, blank=True)
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
        from .features import forget
        from .selectors import forget_hosts

        forget_hosts(self.domains.values_list("domain", flat=True))
        forget(self.pk)

    @property
    def branch_limit(self) -> int | None:
        return self.max_branches if self.max_branches is not None else self.plan.max_branches

    @property
    def user_limit(self) -> int | None:
        return self.max_users if self.max_users is not None else self.plan.max_users


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
    "job.retried": _("Background job run again"),
    "job.cancelled": _("Background job cancelled"),
    "traffic.limit": _("Request limit changed"),
    "platform.settings": _("Platform settings changed"),
    "feature.client": _("Feature changed for a client"),
    "feature.plan": _("Plan features changed"),
    "plan.created": _("Plan created"),
    "plan.updated": _("Plan changed"),
    "plan.deleted": _("Plan deleted"),
    "document.design": _("Document design changed"),
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


class TenantTraffic(models.Model):
    """Requests to one client's workspace in one minute (UTC), written by TrafficMiddleware.
    `requests` counts everything that arrived, `throttled` the part refused with 429; the
    timings cover only the requests that were served."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")
    minute = models.DateTimeField()
    requests = models.PositiveIntegerField(default=0)
    throttled = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)  # 5xx
    slow = models.PositiveIntegerField(default=0)  # >= TRAFFIC_SLOW_MS
    total_ms = models.BigIntegerField(default=0)
    max_ms = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "minute"],
                                               name="tenants_traffic_minute_uniq")]
        indexes = [models.Index(fields=["minute"], name="tenants_traffic_minute_idx")]

    def __str__(self):
        return f"{self.tenant_id} {self.minute:%Y-%m-%d %H:%M}"


class TenantRouteTraffic(models.Model):
    """Served requests per client, day and URL pattern ("GET /sales/<int:pk>/"): which
    screens cost the server the most time."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="+")
    day = models.DateField()
    route = models.CharField(max_length=200)
    requests = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    slow = models.PositiveIntegerField(default=0)
    total_ms = models.BigIntegerField(default=0)
    max_ms = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "day", "route"],
                                               name="tenants_route_traffic_uniq")]
        indexes = [models.Index(fields=["day"], name="tenants_route_traffic_day_idx")]

    def __str__(self):
        return f"{self.tenant_id} {self.day} {self.route}"


class PlatformSettings(models.Model):
    """The platform's own branding, edited in the console (Settings). One row (pk=1)."""

    brand_name = models.CharField(max_length=100, default="Gweb")
    # Shown under each client's name in its workspace sidebar; empty = the brand name.
    tagline = models.CharField(max_length=120, blank=True)
    logo = models.FileField(upload_to=PlatformUploadPath("branding"), blank=True,
                            validators=[validate_image])
    # For dark backgrounds (console sidebar, login panel, dark theme); empty = the logo.
    logo_dark = models.FileField(upload_to=PlatformUploadPath("branding"), blank=True,
                                 validators=[validate_image])
    footer_text = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    CACHE_KEY = "platform:settings"

    class Meta:
        verbose_name = "platform settings"

    def __str__(self):
        return self.brand_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        from django.core.cache import cache

        cache.delete(self.CACHE_KEY)

    @classmethod
    def load(cls) -> PlatformSettings:
        from django.core.cache import cache

        settings_row = cache.get(cls.CACHE_KEY)
        if settings_row is None:
            settings_row = cls.objects.filter(pk=1).first() or cls(pk=1)
            settings_row.cached_links = list(PlatformLink.objects.order_by("position", "id"))
            cache.set(cls.CACHE_KEY, settings_row, 300)
        return settings_row


class PlatformLink(models.Model):
    """A link in the footer of every workspace sidebar (support, website, WhatsApp…)."""

    label = models.CharField(max_length=60)
    url = models.URLField(max_length=500)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import cache

        cache.delete(PlatformSettings.CACHE_KEY)

    def delete(self, *args, **kwargs):
        from django.core.cache import cache

        cache.delete(PlatformSettings.CACHE_KEY)
        return super().delete(*args, **kwargs)


class PlanFeature(models.Model):
    """A plan's choice for one feature, saved from the console (no row = on)."""

    plan = models.ForeignKey(Plan, to_field="code", db_column="plan", on_delete=models.CASCADE,
                             related_name="feature_rows")
    key = models.CharField(max_length=40)
    enabled = models.BooleanField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["plan", "key"],
                                               name="tenants_planfeature_uniq")]

    def __str__(self):
        return f"{self.plan_id}:{self.key}={self.enabled}"


class TenantFeature(models.Model):
    """One client's own choice for a feature, over its plan's default."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="features")
    key = models.CharField(max_length=40)
    enabled = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"],
                                               name="tenants_tenantfeature_uniq")]

    def __str__(self):
        return f"{self.tenant_id}:{self.key}={self.enabled}"
