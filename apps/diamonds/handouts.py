"""Stones from parcels handed to workshops and setters (legacy Prod GHPR14 / 15 / 16 and the
workshop stone balances GHSHH23).

A workshop holds stones of a parcel by the carat, at their cost. They leave the parcel at its
average cost and come back, are set into pieces, or are written off at the holding's average:

    handed out    Dr stock at workshops                 Cr diamonds and stones
    taken back    Dr diamonds and stones                Cr stock at workshops
    set           Dr diamonds and stones (the piece)    Cr stock at workshops
    charged       Dr the workshop's account (money)     Cr stock at workshops
    lost          Dr stone losses                       Cr stock at workshops

Gweb kept these only as weights and counts, with no posting, and its balance checks did not
look at the workshop or the parcel; here every change is a movement on both sides.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import round_money
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import (
    LineInput,
    account_for,
    functional_commodity,
    post_entry,
    reverse_entry,
)
from apps.parties.models import Party, PartyRoleType

from .models import (
    HandoutKind,
    ParcelMovement,
    ParcelMovementKind,
    StoneHandout,
    StoneParcel,
    WorkshopStoneMovement,
    WorkshopStones,
)
from .services import require_enabled

DOC_TYPE = "diamonds.StoneHandout"
ZERO = Decimal(0)
AT_WORKSHOP = "inventory_at_workshop"
# Where the cost goes when stones leave the workshop for good, by kind.
SETTLE_ROLE = {HandoutKind.BACK: "inventory_diamonds", HandoutKind.CHARGED: "workshops",
               HandoutKind.LOST: "stone_loss"}


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def holding_for(workshop, parcel, *, create: bool = False) -> WorkshopStones | None:
    rows = WorkshopStones.objects.select_for_update()
    held = rows.filter(workshop=workshop, parcel=parcel).first()
    if held is None and create:
        WorkshopStones.objects.bulk_create([WorkshopStones(workshop=workshop, parcel=parcel)],
                                           ignore_conflicts=True)
        held = rows.get(workshop=workshop, parcel=parcel)
    return held


def move_held(held: WorkshopStones, kind: str, carats: Decimal, count: int, cost: Decimal, *,
              doc_type: str, doc_id: int, day) -> None:
    held.carats += carats
    held.count += count
    held.cost_amount += cost
    if held.carats < 0 or held.count < 0 or held.cost_amount < 0:
        raise DomainError(_("%(workshop)s holds only %(carats)s ct (%(count)s stones) of parcel "
                            "%(code)s.") % {"workshop": held.workshop.name,
                                            "carats": held.carats - carats,
                                            "count": held.count - count,
                                            "code": held.parcel.code},
                          code="DIAMONDS_WORKSHOP_SHORT")
    held.save(update_fields=["carats", "count", "cost_amount", "updated_at"])
    WorkshopStoneMovement.objects.create(holding=held, kind=kind, carats=carats, count=count,
                                         cost_amount=cost, document_type=doc_type,
                                         document_id=doc_id, business_date=day)


def held_cost(held: WorkshopStones, carats: Decimal) -> Decimal:
    """What `carats` of the holding cost, at its average (all of it when emptied)."""
    if carats >= held.carats:
        return held.cost_amount
    return round_money(held.cost_amount * carats / held.carats) if held.carats else ZERO


def restore_held(*, doc_type: str, doc_id: int, day) -> None:
    """A cancelled setting: stones it took from a workshop's holding go back to it."""
    for move in WorkshopStoneMovement.objects.filter(
            document_type=doc_type, document_id=doc_id,
            kind=ParcelMovementKind.SET).order_by("pk"):
        held = WorkshopStones.objects.select_for_update().select_related(
            "workshop", "parcel").get(pk=move.holding_id)
        move_held(held, ParcelMovementKind.UNSET, -move.carats, -move.count, -move.cost_amount,
                  doc_type=doc_type, doc_id=doc_id, day=day)


@dataclass(frozen=True)
class HandoutInput:
    workshop_id: int
    parcel_id: int
    kind: str  # HandoutKind
    carats: Decimal | str
    count: int = 0
    note: str = ""


def handout(data: HandoutInput, *, actor=None) -> StoneHandout:
    from .parcels import CARAT, _amount, _cost_of, _move

    require_enabled()
    if data.kind not in HandoutKind.values:
        raise _error("kind", _("Choose what happens to the stones."))
    with transaction.atomic():
        parcel = (StoneParcel.objects.select_for_update().select_related("branch")
                  .filter(pk=data.parcel_id).first())
        if parcel is None:
            raise _error("parcel", _("Choose the parcel."))
        if actor is not None:
            actor.require("diamonds.handout", branch=parcel.branch)
        workshop = Party.objects.filter(pk=data.workshop_id, is_active=True,
                                        roles__role=PartyRoleType.WORKSHOP).first()
        if workshop is None:
            raise _error("workshop", _("Choose the workshop or setter."))
        carats = _amount(data.carats, CARAT, "carats")
        count = int(data.count or 0)
        if count < 0:
            raise _error("count", _("Must not be negative."))
        out = data.kind == HandoutKind.OUT
        if out and not parcel.is_active:
            raise _error("parcel", _("This parcel is stopped."))

        today = timezone.localdate()
        doc = StoneHandout.objects.create(
            branch=parcel.branch, business_date=today, workshop=workshop, parcel=parcel,
            kind=data.kind, carats=carats, count=count, note=data.note.strip()[:300],
            created_by=getattr(actor, "user", None))
        held = holding_for(workshop, parcel, create=out)
        if out:
            if carats > parcel.carats or count > parcel.count:
                raise _error("carats", _("Parcel %(code)s has only %(carats)s ct (%(count)s "
                                         "stones).") % {"code": parcel.code,
                                                        "carats": parcel.carats,
                                                        "count": parcel.count})
            cost = _cost_of(parcel, carats)
            _move(parcel, ParcelMovementKind.HAND_OUT, -carats, -count, -cost,
                  doc_type=DOC_TYPE, doc_id=doc.pk, day=today)
            move_held(held, HandoutKind.OUT, carats, count, cost, doc_type=DOC_TYPE,
                      doc_id=doc.pk, day=today)
            debit, credit = account_for(AT_WORKSHOP), account_for("inventory_diamonds")
            party = None
        else:
            if held is None or carats > held.carats or count > held.count:
                raise _error("carats", _("%(workshop)s holds only %(carats)s ct (%(count)s "
                                         "stones) of parcel %(code)s.") % {
                    "workshop": workshop.name, "carats": held.carats if held else ZERO,
                    "count": held.count if held else 0, "code": parcel.code})
            cost = held_cost(held, carats)
            move_held(held, data.kind, -carats, -count, -cost, doc_type=DOC_TYPE, doc_id=doc.pk,
                      day=today)
            if data.kind == HandoutKind.BACK:
                _move(parcel, ParcelMovementKind.HAND_BACK, carats, count, cost,
                      doc_type=DOC_TYPE, doc_id=doc.pk, day=today)
            debit, credit = account_for(SETTLE_ROLE[data.kind]), account_for(AT_WORKSHOP)
            party = workshop if data.kind == HandoutKind.CHARGED else None

        doc.cost_amount = cost
        doc.number = allocate_number("SH", branch=parcel.branch, fiscal_year=today.year)
        if cost:
            home = functional_commodity()
            doc.journal_entry = post_entry(
                branch=parcel.branch, business_date=today, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=doc.pk, memo=_memo(doc),
                lines=[LineInput(account=debit, commodity=home, quantity=cost, party=party),
                       LineInput(account=credit, commodity=home, quantity=-cost)])
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.posted_by = getattr(actor, "user", None)
        doc.save()
    return doc


def _memo(doc: StoneHandout) -> str:
    words = {HandoutKind.OUT: _("Stones of %(code)s handed to %(workshop)s"),
             HandoutKind.BACK: _("Stones of %(code)s back from %(workshop)s"),
             HandoutKind.CHARGED: _("Stones of %(code)s charged to %(workshop)s"),
             HandoutKind.LOST: _("Stones of %(code)s lost by %(workshop)s")}
    return words[doc.kind] % {"code": doc.parcel.code, "workshop": doc.workshop.name}


def void_handout(doc_id: int, *, reason: str = "", actor=None) -> StoneHandout:
    """Undo it while the stones are still where it put them."""
    from .parcels import _move

    require_enabled()
    with transaction.atomic():
        doc = (StoneHandout.objects.select_for_update(of=("self",))
               .select_related("parcel", "workshop", "branch").filter(pk=doc_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.handout", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        parcel = StoneParcel.objects.select_for_update().get(pk=doc.parcel_id)
        for move in ParcelMovement.objects.filter(document_type=DOC_TYPE, document_id=doc.pk):
            _move(parcel, ParcelMovementKind.VOID, -move.carats, -move.count, -move.cost_amount,
                  doc_type=DOC_TYPE, doc_id=doc.pk, day=today)
        for move in WorkshopStoneMovement.objects.filter(document_type=DOC_TYPE,
                                                         document_id=doc.pk):
            held = WorkshopStones.objects.select_for_update().select_related(
                "workshop", "parcel").get(pk=move.holding_id)
            move_held(held, "void", -move.carats, -move.count, -move.cost_amount,
                      doc_type=DOC_TYPE, doc_id=doc.pk, day=today)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": doc.number})
        doc.status = DocStatus.VOIDED
        doc.voided_at = timezone.now()
        doc.voided_by = getattr(actor, "user", None)
        doc.void_reason = reason.strip()[:300]
        doc.save()
    return doc


def holdings(branch_ids=None):
    """What each workshop holds now, for the page and the report."""
    rows = (WorkshopStones.objects.select_related("workshop", "parcel__branch")
            .filter(carats__gt=0) | WorkshopStones.objects.select_related(
                "workshop", "parcel__branch").filter(count__gt=0))
    if branch_ids is not None:
        rows = rows.filter(parcel__branch_id__in=branch_ids)
    return rows.order_by("workshop__name", "parcel__code")
