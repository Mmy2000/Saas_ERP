"""Screens a member pinned (the star in the top bar): at the top of their sidebar and on their
dashboard. Only sidebar screens can be pinned, and each shows only while the member may open
it."""

from __future__ import annotations

from django.db.models import Max
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.errors import ValidationError

from .models import Shortcut

MAX_SHORTCUTS = 12


def screens() -> dict:
    """Every pinnable screen: url name → its sidebar item."""
    from apps.core.context_processors import NAVIGATION

    return {item.url_name: item for section in NAVIGATION for item in section.items
            if item.url_name != "home"}  # the dashboard is always first anyway


def pinned(actor) -> list[dict]:
    items = screens()
    rows = Shortcut.objects.filter(membership=actor.membership).values_list("screen", flat=True)
    return [{"screen": name, "label": str(items[name].label), "url": reverse(name),
             "icon": items[name].icon}
            for name in rows if name in items and actor.can(items[name].permission)]


def pin(actor, screen: str) -> None:
    item = screens().get(screen)
    if item is None or not actor.can(item.permission):
        raise ValidationError(_("This screen cannot be pinned."), fields={"screen": [screen]})
    mine = Shortcut.objects.filter(membership=actor.membership)
    if mine.filter(screen=screen).exists():
        return
    if mine.count() >= MAX_SHORTCUTS:
        raise ValidationError(_("You can pin up to %(count)s screens. Unpin one first.")
                              % {"count": MAX_SHORTCUTS}, code="IAM_TOO_MANY_SHORTCUTS")
    last = mine.aggregate(last=Max("position"))["last"]
    Shortcut.objects.create(membership=actor.membership, screen=screen,
                            position=0 if last is None else last + 1)


def unpin(actor, screen: str) -> None:
    Shortcut.objects.filter(membership=actor.membership, screen=screen).delete()
