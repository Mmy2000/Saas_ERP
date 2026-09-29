"""Base models for the whole platform (§6.3) and document numbering (§6.6)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .tenancy.context import TenantContextError, require_current_tenant_id
from .tenancy.managers import TenantManager


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedModel(TimeStampedModel):
    """Base for every tenant-owned table.

    * `objects` is filtered to the current tenant and raises without a tenant context.
    * save()/delete()/bulk_create stamp or check `tenant_id` against the context.
    * The table must also get `EnableTenantRLS` in its migration (checked by the test suite).
    * Subclasses must declare an index or unique constraint whose first column is `tenant`
      (the FK has no index of its own so that composite indexes lead with it instead).
    """

    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, db_index=False, related_name="+", editable=False
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    objects = TenantManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self._stamp_tenant()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._stamp_tenant()
        return super().delete(*args, **kwargs)

    def _stamp_tenant(self) -> None:
        tenant_id = require_current_tenant_id()
        if self.tenant_id is None:
            self.tenant_id = tenant_id
        elif self.tenant_id != tenant_id:
            raise TenantContextError(
                f"{self._meta.label} #{self.pk} belongs to tenant {self.tenant_id}, "
                f"current context is tenant {tenant_id}."
            )


class DocStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    POSTED = "posted", _("Posted")
    VOIDED = "voided", _("Cancelled")


class Document(TenantScopedModel):
    """Anything with a number and a lifecycle (§6.3, §6.7, ADR-008).

    Drafts can be edited and deleted and have no number. Posting allocates the number and
    applies the document's effects (stock, ledger) in one transaction. Posted documents are
    never edited or deleted: voiding reverses their effects and keeps them visible.
    """

    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    # NULL (not "") until posted, so the partial unique constraint ignores drafts.
    number = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001
    business_date = models.DateField()
    status = models.CharField(max_length=8, choices=DocStatus.choices, default=DocStatus.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")
    void_reason = models.CharField(max_length=300, blank=True)
    legacy_ref = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(fields=["tenant", "number"], condition=Q(number__isnull=False),
                                    name="%(app_label)s_%(class)s_number_uniq"),
        ]
        # Concrete documents add Index(fields=["tenant", "branch", "-business_date"]) with a
        # short explicit name (index names are limited to 30 characters).

    @property
    def is_draft(self) -> bool:
        return self.status == DocStatus.DRAFT


class IdempotencyRecord(TenantScopedModel):
    """Stored response of a money-moving POST, keyed by the client's Idempotency-Key (§11.4).
    status 0 = the first request is still running."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=64)
    request_path = models.CharField(max_length=300)
    response_status = models.PositiveSmallIntegerField(default=0)
    response_body = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user", "key"],
                                    name="core_idempotency_key_uniq"),
        ]

    def __str__(self):
        return f"{self.key} → {self.response_status or 'pending'}"


class DocumentSequence(TenantScopedModel):
    """One counter per (tenant, document type, branch, fiscal year).

    Allocated with SELECT … FOR UPDATE in the posting transaction (apps.core.sequences), so
    numbers are gapless as long as posted documents are never deleted, which they aren't.
    """

    doc_type = models.CharField(max_length=40)
    branch = models.ForeignKey("org.Branch", null=True, blank=True, on_delete=models.PROTECT)
    fiscal_year = models.PositiveSmallIntegerField()
    prefix = models.CharField(max_length=8)
    next_value = models.BigIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "doc_type", "branch", "fiscal_year"],
                nulls_distinct=False,
                name="core_documentsequence_scope_uniq",
            ),
            models.CheckConstraint(
                condition=Q(next_value__gte=1), name="core_documentsequence_next_value_check"
            ),
        ]

    def __str__(self):
        return f"{self.doc_type}/{self.branch_id or '*'}/{self.fiscal_year} → {self.next_value}"
