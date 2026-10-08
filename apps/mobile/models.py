"""A phone signed in to the mobile app: one row per sign-in, holding the current refresh token
(as a hash). Each refresh replaces it (rotation), signing out revokes it, and the shop can sign a
lost phone out from Settings → Mobile devices; access tokens name the row, so that is at once."""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


class MobileDevice(TenantScopedModel):
    membership = models.ForeignKey("iam.Membership", on_delete=models.CASCADE,
                                   related_name="mobile_devices")
    refresh_hash = models.CharField(max_length=64)  # sha256 of the current refresh token
    refresh_expires_at = models.DateTimeField()
    user_agent = models.CharField(max_length=300, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "refresh_hash"],
                                               name="mobile_device_refresh_uniq")]
        indexes = [models.Index(fields=["tenant", "membership", "revoked_at"],
                                name="mobile_device_member_idx")]

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.refresh_expires_at > timezone.now()
