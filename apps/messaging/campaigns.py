"""WhatsApp campaigns from the shop's linked number (legacy whatsapp_gateway bulk sending, there
only from two reports and with text placeholders that could stop a campaign).

Who gets it: active customers with a phone (all, those of one home branch, those who owe the
shop, those who bought since a date, those who have not bought since a date), or a pasted list.
Numbers are checked and written with the country code; a number listed twice, a bad number and a
number on the opt-out list are skipped, and the shop sees why.

How it goes: one job sends one message and queues the next after a random gap (min_gap to
max_gap seconds), only between 09:00 and 21:00 the shop's time and up to `daily_cap` a day across
all campaigns; after FAILURES_TO_PAUSE failures in a row, or when the number is no longer
linked, the campaign pauses with the reason. One campaign sends at a time.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.platform.jobs.registry import enqueue

from . import gateways
from .models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    OptOut,
    RecipientStatus,
    WhatsAppLink,
)

AUDIENCES = ("all", "owing", "bought_since", "not_since", "list")
MAX_RECIPIENTS = 5000
FAILURES_TO_PAUSE = 8
SEND_FROM, SEND_UNTIL = time(9, 0), time(21, 0)
MIN_GAP = 20  # seconds: never faster than this, whatever is chosen
PLACEHOLDERS = ("{name}", "{company}")


@dataclass(frozen=True)
class Audience:
    kind: str = "all"
    branch_id: int | None = None
    since: date | None = None
    numbers: str = ""  # one per line: "name, phone" or a phone

    def as_json(self) -> dict:
        return {"kind": self.kind, "branch": self.branch_id,
                "since": self.since.isoformat() if self.since else None,
                "numbers": len([ln for ln in self.numbers.splitlines() if ln.strip()])}


def _customers(audience: Audience):
    from apps.core.models import DocStatus
    from apps.parties.selectors import parties_with_role
    from apps.sales.models import SalesInvoice

    people = parties_with_role("customer").filter(is_active=True).exclude(
        phone="", phone_e164="")  # no number at all: not part of a campaign
    if audience.branch_id:
        people = people.filter(home_branch_id=audience.branch_id)
    if audience.kind == "owing":
        from apps.ledger.models import BalanceProjection
        from apps.pricing.selectors import functional_currency

        owing = (BalanceProjection.objects.filter(account__role="customers",
                                                  commodity__code=functional_currency(),
                                                  party__isnull=False)
                 .values("party").annotate(q=Sum("quantity")).filter(q__gt=0)
                 .values_list("party", flat=True))
        people = people.filter(pk__in=list(owing))
    elif audience.kind in ("bought_since", "not_since"):
        if audience.since is None:
            raise ValidationError(_("Choose the date."), fields={"since": [_("Required.")]})
        posted = SalesInvoice.objects.filter(status=DocStatus.POSTED, customer__isnull=False)
        since = posted.filter(business_date__gte=audience.since).values("customer")
        if audience.kind == "bought_since":
            people = people.filter(pk__in=since)
        else:  # bought before, not since
            people = people.filter(pk__in=posted.values("customer")).exclude(pk__in=since)
    return [(p, p.name, p.phone_e164 or p.phone) for p in people.order_by("name")]


def _pasted(numbers: str):
    rows = []
    for line in numbers.splitlines():
        parts = [part.strip() for part in re.split(r"[,\t;]", line) if part.strip()]
        if not parts:
            continue
        phone = max(parts, key=lambda part: sum(ch.isdigit() for ch in part))
        name = next((part for part in parts if part is not phone), "")
        rows.append((None, name, phone))
    return rows


def recipients(audience: Audience) -> tuple[list[dict], list[dict]]:
    """(who gets it, who is skipped and why)."""
    if audience.kind not in AUDIENCES:
        raise ValidationError(_("Choose who gets the message."), fields={"kind": [_("Required.")]})
    found = _pasted(audience.numbers) if audience.kind == "list" else _customers(audience)
    opted = set(OptOut.objects.values_list("phone", flat=True))
    chosen, skipped, seen = [], [], set()
    for party, name, raw in found:
        phone = gateways.phone_digits(raw or "")
        row = {"party": party, "name": name[:200], "phone": phone or (raw or "")[:20]}
        if not phone:
            skipped.append({**row, "reason": _("No valid number")})
        elif phone in seen:
            skipped.append({**row, "reason": _("The same number twice")})
        elif phone in opted:
            skipped.append({**row, "reason": _("Asked not to get campaigns")})
        else:
            seen.add(phone)
            chosen.append(row)
    if len(chosen) > MAX_RECIPIENTS:
        raise ValidationError(_("At most %(count)s people in one campaign. Narrow the choice.")
                              % {"count": MAX_RECIPIENTS}, fields={"kind": [_("Too many.")]})
    return chosen, skipped


def preview(audience: Audience) -> dict:
    chosen, skipped = recipients(audience)
    reasons: dict[str, int] = {}
    for row in skipped:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    return {"count": len(chosen), "skipped": [{"reason": r, "count": n}
                                              for r, n in sorted(reasons.items())],
            "sample": [row["name"] or f"+{row['phone']}" for row in chosen[:6]]}


def render(message: str, name: str) -> str:
    """Placeholders are replaced as plain text: a stray brace never stops a campaign."""
    from .services import company_name

    return message.replace("{name}", name or "").replace("{company}", company_name()).strip()


def _linked() -> WhatsAppLink:
    link = WhatsAppLink.objects.first()
    if link is None or not link.connected:
        raise DomainError(_("Link the shop's WhatsApp number first (Settings → WhatsApp)."),
                          code="MESSAGING_NOT_LINKED")
    return link


@dataclass(frozen=True)
class CampaignInput:
    name: str
    message: str
    audience: Audience
    daily_cap: int = 150
    min_gap: int = 25
    max_gap: int = 70


def start(data: CampaignInput, *, actor=None) -> Campaign:
    name, message = data.name.strip()[:120], data.message.strip()
    if not name:
        raise ValidationError(_("Give the campaign a name."), fields={"name": [_("Required.")]})
    if not message:
        raise ValidationError(_("Write the message."), fields={"message": [_("Required.")]})
    if not 1 <= data.daily_cap <= 1000:
        raise ValidationError(_("Between 1 and 1000 a day."), fields={"daily_cap": [
            _("Between 1 and 1000 a day.")]})
    min_gap = max(int(data.min_gap), MIN_GAP)
    max_gap = max(int(data.max_gap), min_gap)
    with transaction.atomic():
        _linked()
        if Campaign.objects.select_for_update().filter(
                status__in=(CampaignStatus.RUNNING, CampaignStatus.PAUSED)).exists():
            raise DomainError(_("Another campaign is still sending or paused. Finish or cancel "
                                "it first."), code="MESSAGING_CAMPAIGN_BUSY")
        chosen, skipped = recipients(data.audience)
        if not chosen:
            raise ValidationError(_("Nobody to send to with this choice."),
                                  fields={"kind": [_("Nobody to send to.")]})
        campaign = Campaign.objects.create(
            name=name, message=message, audience=data.audience.as_json(), total=len(chosen)
            + len(skipped), skipped=len(skipped), daily_cap=data.daily_cap, min_gap=min_gap,
            max_gap=max_gap, started_at=timezone.now(), created_by=getattr(actor, "user", None))
        CampaignRecipient.objects.bulk_create(
            [CampaignRecipient(campaign=campaign, party=row["party"], name=row["name"],
                               phone=row["phone"]) for row in chosen]
            + [CampaignRecipient(campaign=campaign, party=row["party"], name=row["name"],
                                 phone=row["phone"][:20], status=RecipientStatus.SKIPPED,
                                 reason=row["reason"][:300]) for row in skipped])
        _next(campaign, at=_send_time(timezone.now()))
    return campaign


def _send_time(moment: datetime) -> datetime:
    """`moment`, or the next 09:00 when it falls outside the sending hours (the shop's time)."""
    local = timezone.localtime(moment)
    if SEND_FROM <= local.time() < SEND_UNTIL:
        return moment
    day = local.date() + (timedelta(days=1) if local.time() >= SEND_UNTIL else timedelta())
    return timezone.make_aware(datetime.combine(day, SEND_FROM))


def _next(campaign: Campaign, *, at: datetime) -> None:
    campaign.step += 1
    Campaign.objects.filter(pk=campaign.pk).update(step=campaign.step)
    enqueue("messaging.campaign_next", campaign_id=campaign.pk, step=campaign.step, run_at=at,
            dedupe_key=f"campaign:{campaign.pk}:{campaign.step}")


def _locked(campaign_id: int) -> Campaign:
    campaign = Campaign.objects.select_for_update().filter(pk=campaign_id).first()
    if campaign is None:
        raise NotFound(_("Not found."))
    return campaign


def pause(campaign_id: int, note: str = "") -> Campaign:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        if campaign.status != CampaignStatus.RUNNING:
            raise DomainError(_("This campaign is not sending."), code="MESSAGING_NOT_RUNNING")
        campaign.status, campaign.note = CampaignStatus.PAUSED, note[:300]
        campaign.save(update_fields=["status", "note", "updated_at"])
    return campaign


def resume(campaign_id: int) -> Campaign:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        if campaign.status != CampaignStatus.PAUSED:
            raise DomainError(_("This campaign is not paused."), code="MESSAGING_NOT_PAUSED")
        _linked()
        campaign.status, campaign.note, campaign.failures_in_a_row = CampaignStatus.RUNNING, "", 0
        campaign.save(update_fields=["status", "note", "failures_in_a_row", "updated_at"])
        _next(campaign, at=_send_time(timezone.now()))
    return campaign


def cancel(campaign_id: int) -> Campaign:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        if campaign.status not in (CampaignStatus.RUNNING, CampaignStatus.PAUSED):
            raise DomainError(_("This campaign has already ended."), code="MESSAGING_ENDED")
        campaign.status, campaign.finished_at = CampaignStatus.CANCELLED, timezone.now()
        campaign.save(update_fields=["status", "finished_at", "updated_at"])
    return campaign


def _sent_today() -> int:
    start_of_day = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    return CampaignRecipient.objects.filter(status=RecipientStatus.SENT,
                                            sent_at__gte=start_of_day).count()


def send_next(campaign_id: int, step: int = 0) -> None:
    """The job: send to the next person, then queue the one after."""
    with transaction.atomic():
        campaign = _locked(campaign_id)
        if campaign.status != CampaignStatus.RUNNING or step != campaign.step:
            return
        now = timezone.now()
        if _send_time(now) != now:  # outside the sending hours: carry on tomorrow morning
            _next(campaign, at=_send_time(now))
            return
        if _sent_today() >= campaign.daily_cap:
            tomorrow = timezone.make_aware(datetime.combine(
                timezone.localdate() + timedelta(days=1), SEND_FROM))
            _next(campaign, at=tomorrow)
            return
        link = WhatsAppLink.objects.first()
        if link is None or not link.connected:
            campaign.status = CampaignStatus.PAUSED
            campaign.note = _("Paused: the shop's WhatsApp number is not linked.")
            campaign.save(update_fields=["status", "note", "updated_at"])
            return
        person = (CampaignRecipient.objects.select_for_update()
                  .filter(campaign=campaign, status=RecipientStatus.QUEUED).order_by("id").first())
        if person is None:
            campaign.status, campaign.finished_at = CampaignStatus.DONE, now
            campaign.save(update_fields=["status", "finished_at", "updated_at"])
            return
        if OptOut.objects.filter(phone=person.phone).exists():  # asked since the start
            person.status, person.reason = RecipientStatus.SKIPPED, _("Asked not to get campaigns")
            person.save(update_fields=["status", "reason", "updated_at"])
            campaign.skipped += 1
            campaign.save(update_fields=["skipped", "updated_at"])
            _next(campaign, at=now)
            return
        result = gateways.own_send(link.session_id, phone=person.phone,
                                   text=render(campaign.message, person.name))
        if result.ok:
            person.status, person.sent_at, person.reason = RecipientStatus.SENT, now, ""
            campaign.sent += 1
            campaign.failures_in_a_row = 0
        else:
            person.status, person.reason = RecipientStatus.FAILED, result.error[:300]
            campaign.failed += 1
            campaign.failures_in_a_row += 1
        person.save(update_fields=["status", "sent_at", "reason", "updated_at"])
        if campaign.failures_in_a_row >= FAILURES_TO_PAUSE:
            campaign.status = CampaignStatus.PAUSED
            campaign.note = _("Paused after %(count)s failures in a row: %(reason)s") % {
                "count": campaign.failures_in_a_row, "reason": result.error[:200]}
        campaign.save()
        if campaign.status == CampaignStatus.RUNNING:
            gap = random.randint(campaign.min_gap, campaign.max_gap)  # noqa: S311 (pacing)
            _next(campaign, at=now + timedelta(seconds=gap))
