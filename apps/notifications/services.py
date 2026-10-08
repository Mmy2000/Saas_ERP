"""Telling members about events (`notify`), and what they have read.

`notify` runs inside the transaction of the work it reports, so a cancelled sale or a failed
transfer never leaves a notification behind. Recipients are the members holding the kind's
permission in the branch, minus those who muted the kind and the person who did the work.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.iam.authz import members_who_can

from .kinds import KINDS, kinds_for
from .models import Notification, NotificationMute

KEEP_READ_DAYS = 90
LATEST = 12


def notify(kind: str, *, key: str, params: dict, url: str = "", branch=None, by=None,
           replaces: str = "") -> int:
    """Tell everyone concerned about `key` (once each). `by`: the user who did it, who is not
    told. `replaces`: a key prefix whose unread notifications this one supersedes (e.g. only
    the latest gold price matters). Returns how many members were told."""
    spec = KINDS[kind]
    members = members_who_can(spec.permission, branch).exclude(
        pk__in=NotificationMute.objects.filter(kind=kind).values("membership_id"))
    if by is not None:
        members = members.exclude(user_id=getattr(by, "pk", by))
    ids = list(members.values_list("pk", flat=True))
    if not ids:
        return 0
    if replaces:
        Notification.objects.filter(membership_id__in=ids, key__startswith=replaces,
                                    read_at__isnull=True).delete()
    Notification.objects.bulk_create(
        [Notification(membership_id=pk, kind=kind, key=key, params=params, url=url[:300],
                      branch_id=getattr(branch, "pk", branch)) for pk in ids],
        ignore_conflicts=True)
    return len(ids)


def resolve(key: str) -> None:
    """The event was dealt with (goods received, price approved): mark it read for all."""
    Notification.objects.filter(key=key, read_at__isnull=True).update(read_at=timezone.now())


def visible(actor):
    """The member's notifications they may still see (a lost permission hides its kinds)."""
    return Notification.objects.filter(membership=actor.membership,
                                       kind__in=[k.key for k in kinds_for(actor)])


def unread_count(actor) -> int:
    return visible(actor).filter(read_at__isnull=True).count()


def mark_read(actor, notification_id: int) -> None:
    visible(actor).filter(pk=notification_id, read_at__isnull=True).update(
        read_at=timezone.now())


def mark_all_read(actor) -> None:
    visible(actor).filter(read_at__isnull=True).update(read_at=timezone.now())


def preferences(actor) -> list[dict]:
    muted = set(NotificationMute.objects.filter(membership=actor.membership)
                .values_list("kind", flat=True))
    return [{"kind": kind.key, "label": str(kind.label), "description": str(kind.description),
             "icon": kind.icon, "on": kind.key not in muted} for kind in kinds_for(actor)]


def set_muted(actor, muted: set[str]) -> None:
    allowed = {kind.key for kind in kinds_for(actor)}
    muted = muted & allowed
    NotificationMute.objects.filter(membership=actor.membership).exclude(
        kind__in=muted).delete()
    NotificationMute.objects.bulk_create(
        [NotificationMute(membership=actor.membership, kind=kind) for kind in sorted(muted)],
        ignore_conflicts=True)


def clean_up(now=None) -> int:
    """Read notifications go after 90 days; unread ones after a year."""
    now = now or timezone.now()
    deleted, _ = Notification.objects.filter(
        Q(read_at__lt=now - timedelta(days=KEEP_READ_DAYS))
        | Q(read_at__isnull=True, created_at__lt=now - timedelta(days=365))).delete()
    return deleted
