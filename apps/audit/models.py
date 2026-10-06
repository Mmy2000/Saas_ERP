"""The audit trail (§21): one row per insert, change or delete of an audited table, written by
a database trigger (apps/audit/triggers.py), so nothing can change those tables unrecorded."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class AuditAction(models.TextChoices):
    INSERT = "I", _("added")
    UPDATE = "U", _("changed")
    DELETE = "D", _("deleted")


class AuditEvent(TenantScopedModel):
    table_name = models.CharField(max_length=63)
    row_id = models.BigIntegerField()
    action = models.CharField(max_length=1, choices=AuditAction.choices)
    # Insert/delete: {column: value}. Change: {column: [old, new]}. Secrets read "***".
    changes = models.JSONField(default=dict)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.PROTECT, related_name="+", db_constraint=False)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "-at"], name="audit_event_at_idx"),
            models.Index(fields=["tenant", "table_name", "row_id"], name="audit_event_row_idx"),
        ]
