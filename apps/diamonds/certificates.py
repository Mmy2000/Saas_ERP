"""Certificates: checking a stone's lab report (legacy GHD52 "التحقق من شهادة IGI") and the
printed certificate cards (GHD15 printall, GHD19, GHD39).

A check asks the lab once and keeps the answer and the report's PDF; asking again only
happens when someone wants a fresh answer (or the last one failed). Only IGI publishes its
reports for checking this way.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import ValidationError
from apps.inventory.models import Item

from . import igi
from .models import CertificateCheck, CheckStatus, ItemStone
from .services import require_enabled

LABS = ("IGI",)


def latest(report_numbers) -> dict[str, CertificateCheck]:
    """The kept answer for each IGI report number (for showing next to the stones)."""
    numbers = [number for number in report_numbers if number]
    return {check.report_no: check for check in
            CertificateCheck.objects.filter(lab="IGI", report_no__in=numbers)}


def check(report_no: str, *, lab: str = "IGI", refresh: bool = False,
          actor=None) -> CertificateCheck:
    require_enabled()
    if actor is not None:
        actor.require("diamonds.view")
    number = "".join(ch for ch in (report_no or "") if ch.isalnum())[:60]
    if not number:
        raise ValidationError(_("Enter the report number."),
                              fields={"report_no": [_("Enter the report number.")]})
    lab = (lab or "IGI").upper()
    if lab not in LABS:
        raise ValidationError(_("Only IGI reports can be checked here."),
                              fields={"lab": [_("Only IGI reports can be checked here.")]})
    kept = CertificateCheck.objects.filter(lab=lab, report_no=number).first()
    if kept is not None and kept.status == CheckStatus.VERIFIED and not refresh:
        return kept
    answer = igi.fetch(number)
    with transaction.atomic():
        kept = (CertificateCheck.objects.select_for_update().filter(lab=lab, report_no=number)
                .first() or CertificateCheck(lab=lab, report_no=number))
        kept.status = answer.status
        kept.message = answer.message[:300]
        kept.data = answer.data or {}
        kept.checked_at = timezone.now()
        kept.checked_by = getattr(actor, "user", None)
        if not kept.pk:
            kept.save()  # the file path needs the tenant, set on save
        if answer.pdf:
            if kept.pdf:
                kept.pdf.delete(save=False)
            kept.pdf.save(f"igi-{number}.pdf", ContentFile(answer.pdf), save=False)
        kept.save()
    return kept


# --- certificate cards -------------------------------------------------------------------------

@dataclass
class Card:
    item: Item
    stones: list[ItemStone]
    carats: Decimal
    count: int
    customer: str = ""


def cards(items, *, customer: str = "") -> list[Card]:
    """One credit-card certificate per diamond piece or loose stone."""
    made = []
    for item in items:
        stones = list(item.stones.all())
        made.append(Card(item=item, stones=stones,
                         carats=sum((stone.carat for stone in stones), Decimal(0)),
                         count=sum((stone.count for stone in stones), 0), customer=customer))
    return made
