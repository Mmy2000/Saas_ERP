"""In-app notifications: one row per member told (the bell in the top bar)."""

from __future__ import annotations

from django.db import models

from apps.core.models import TenantScopedModel


class Notification(TenantScopedModel):
    membership = models.ForeignKey("iam.Membership", on_delete=models.CASCADE,
                                   related_name="notifications")
    kind = models.CharField(max_length=32)  # apps.notifications.kinds.KINDS
    # The event, so that it reaches each member once, and can be marked done for everyone
    # (e.g. "stock_transfer:12" once the goods are received).
    key = models.CharField(max_length=120)
    branch = models.ForeignKey("org.Branch", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")
    params = models.JSONField(default=dict)  # the facts the text is written from
    url = models.CharField(max_length=300, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "membership", "key"],
                                    name="notifications_member_key_uniq"),
        ]
        indexes = [
            models.Index(fields=["tenant", "membership", "read_at"],
                         name="notifications_unread_idx"),
            models.Index(fields=["tenant", "key"], name="notifications_key_idx"),
        ]

    @property
    def text(self) -> tuple[str, str]:
        from .kinds import KINDS

        kind = KINDS.get(self.kind)
        if kind is None:
            return "", ""
        try:
            return kind.text(self.params)
        except (KeyError, TypeError, ValueError):  # facts from an older version of the text
            return str(kind.label), ""

    @property
    def icon(self) -> str:
        from .kinds import KINDS

        kind = KINDS.get(self.kind)
        return kind.icon if kind else "bell"


class NotificationMute(TenantScopedModel):
    """A kind the member asked not to be told about."""

    membership = models.ForeignKey("iam.Membership", on_delete=models.CASCADE,
                                   related_name="+")
    kind = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "membership", "kind"],
                                    name="notifications_mute_uniq"),
        ]
