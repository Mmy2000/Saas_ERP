"""The setter's work (legacy Install, GHN1–GHN4), done properly.

1. Hand over (GHN1): mountings (gold or diamond pieces) and loose stones go to a setter. They
   leave stock ("at workshop") and their value moves to "goods at workshops":
       Dr goods at workshops      Cr gold inventory (fine g + making) / diamonds and stones
   Each line remembers the gold's value when handed over, so it comes back at the same value.
2. Set (GHN3): a mounting and stones of the job come back as one piece: a StoneSetting of the
   job (details and cost of the stones onto the piece, re-weighed) whose entry also brings the
   piece back from the setter, plus the setter's labour on the piece's cost and his account.
   A gold mounting becomes a diamond piece (its category changes; a label price can be given).
3. Return (GHN4 for pieces never set): what the setter did not use comes back unchanged.
4. Take stones out (GHN4 "فك"): stones set here leave the piece again, back in stock as the
   same loose stones (barcode, details and cost), the piece re-weighed, an optional fee for the
   work on the piece's cost and the setter's account. Gweb lost the stones' cost and deleted the
   piece; here nothing is lost.

A job closes when nothing is left with the setter. The setter's statement shows his labour and
fees; the job page and the list show what he holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, ProductFamily, Tracking
from apps.core.errors import DomainError, NotFound
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, WEIGHT, quantize
from apps.core.sequences import allocate_number
from apps.inventory.models import Item, ItemStatus, MovementType
from apps.inventory.services import DocRef, change_item_status
from apps.inventory.valuation import StockPart, metal_value, stock_lines
from apps.ledger.models import EntryKind
from apps.ledger.services import post_entry, reverse_entry
from apps.org.models import Branch
from apps.parties.models import Party, PartyRoleType

from .models import (
    ItemStone,
    JobLineState,
    SettingJob,
    SettingJobLine,
    StoneRemoval,
    StoneSetting,
)
from .parcels import give_back, price_uses
from .services import _field, apply_setting, labour_lines, require_enabled

JOB_DOC = "diamonds.SettingJob"
REMOVAL_DOC = "diamonds.StoneRemoval"
MOUNTINGS = (ProductFamily.GOLD, ProductFamily.DIAMOND)
HOLDING = "inventory_at_workshop"
ZERO = Decimal(0)


def _part(item: Item, *, stone_cost=None) -> StockPart:
    return StockPart(item.category, item.karat, item.fine_weight_g, item.cost_amount,
                     item.stone_cost_amount if stone_cost is None else stone_cost)


def _setter(setter_id) -> Party:
    setter = Party.objects.filter(pk=setter_id, is_active=True,
                                  roles__role=PartyRoleType.WORKSHOP).first()
    if setter is None:
        raise _field("setter", _("Choose the setter's workshop."))
    return setter


def _amount(value, field: str, step=MONEY) -> Decimal:
    try:
        amount = quantize(value or "0", step)
    except (TypeError, ValueError, InvalidOperation):
        raise _field(field, _("Enter a number.")) from None
    if amount < 0:
        raise _field(field, _("Must not be negative."))
    return amount


def _close_if_done(job: SettingJob) -> None:
    open_lines = job.lines.filter(state=JobLineState.OUT).exists()
    job.closed_at = None if open_lines else timezone.now()
    job.save(update_fields=["closed_at", "updated_at"])


# --- 1. hand over -----------------------------------------------------------------------------

@dataclass(frozen=True)
class JobInput:
    branch_id: int
    setter_id: int
    item_ids: tuple[int, ...]
    note: str = ""


def hand_over(data: JobInput, *, actor=None) -> SettingJob:
    require_enabled()
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("diamonds.setting", branch=branch)
        setter = _setter(data.setter_id)
        ids = sorted(set(data.item_ids))
        if not ids:
            raise _field("items", _("Add the pieces and stones to hand over."))
        items = list(Item.objects.select_for_update(of=("self",))
                     .select_related("category", "karat__metal").filter(pk__in=ids))
        if len(items) != len(ids):
            raise _field("items", _("Unknown piece."))
        for item in items:
            family = item.category.product_family
            if (family not in (*MOUNTINGS, ProductFamily.STONE)
                    or item.category.tracking != Tracking.SERIALIZED):
                raise _field("items", _("%(barcode)s is not a mounting or a loose stone.")
                             % {"barcode": item.barcode})
            if item.status != ItemStatus.IN_STOCK or item.branch_id != branch.pk:
                raise _field("items", _("%(barcode)s is not in stock here.")
                             % {"barcode": item.barcode})

        today = timezone.localdate()
        job = SettingJob.objects.create(branch=branch, business_date=today, setter=setter,
                                        note=data.note.strip(),
                                        created_by=getattr(actor, "user", None))
        doc = DocRef(JOB_DOC, job.pk)
        values = []
        for item in items:
            value = metal_value(item.karat, item.fine_weight_g)
            values.append(value)
            SettingJobLine.objects.create(job=job, item=item, metal_value=value,
                                          category=item.category)
            change_item_status(item.pk, to=ItemStatus.AT_WORKSHOP,
                               allowed_from=(ItemStatus.IN_STOCK,),
                               movement_type=MovementType.WORKSHOP_ISSUE, business_date=today,
                               doc=doc, branch=branch)
        job.number = allocate_number("SJ", branch=branch, fiscal_year=today.year)
        job.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=JOB_DOC,
            source_id=job.pk, memo=_("Handed to %(setter)s for setting") % {"setter": setter.name},
            lines=stock_lines([_part(item) for item in items], sign=-1, counter_role=HOLDING,
                              branch=branch, values=values))
        job.status = DocStatus.POSTED
        job.posted_at = timezone.now()
        job.posted_by = getattr(actor, "user", None)
        job.save()
    return job


def _job(job_id: int, actor) -> SettingJob:
    job = (SettingJob.objects.select_for_update(of=("self",)).select_related("branch", "setter")
           .filter(pk=job_id).first())
    if job is None:
        raise NotFound(_("Not found."))
    if actor is not None:
        actor.require("diamonds.setting", branch=job.branch)
    if job.status != DocStatus.POSTED:
        raise DomainError(_("This job is cancelled."), code="DOC_NOT_POSTED")
    return job


def _out_lines(job: SettingJob, line_ids, field: str) -> list[SettingJobLine]:
    ids = sorted(set(line_ids))
    lines = list(job.lines.select_related("item__category", "item__karat__metal")
                 .filter(pk__in=ids, state=JobLineState.OUT).order_by("pk"))
    if not ids or len(lines) != len(ids):
        raise _field(field, _("Choose what is still with the setter."))
    return lines


# --- 3. return unused -------------------------------------------------------------------------

def return_items(job_id: int, line_ids, *, actor=None) -> SettingJob:
    """What the setter did not use comes back to stock unchanged, at the value it left with."""
    require_enabled()
    with transaction.atomic():
        job = _job(job_id, actor)
        lines = _out_lines(job, line_ids, "lines")
        today = timezone.localdate()
        doc = DocRef(JOB_DOC, job.pk)
        for line in lines:
            change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                               allowed_from=(ItemStatus.AT_WORKSHOP,),
                               movement_type=MovementType.WORKSHOP_RECEIPT, business_date=today,
                               doc=doc, branch=job.branch, stock_delta=1)
        entry = post_entry(
            branch=job.branch, business_date=today, kind=EntryKind.AUTO, source_type=JOB_DOC,
            source_id=job.pk,
            memo=_("Back from %(setter)s unused") % {"setter": job.setter.name},
            lines=stock_lines([_part(line.item) for line in lines], sign=1, counter_role=HOLDING,
                              branch=job.branch, values=[line.metal_value for line in lines]))
        now = timezone.now()
        for line in lines:
            line.state, line.return_entry, line.returned_at = JobLineState.RETURNED, entry, now
            line.save(update_fields=["state", "return_entry", "returned_at", "updated_at"])
        _close_if_done(job)
    return job


# --- 2. set -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class SetInput:
    mounting_line_id: int
    stone_line_ids: tuple[int, ...]
    gross_after_g: Decimal | str
    labour_amount: Decimal | str = "0"
    category_id: int | None = None  # a gold mounting becomes a piece of this diamond category
    label_price: Decimal | str | None = None
    note: str = ""
    parcel_uses: tuple = ()  # small stones from the shop's parcels, by the carat


def set_from_job(job_id: int, data: SetInput, *, actor=None) -> StoneSetting:
    require_enabled()
    with transaction.atomic():
        job = _job(job_id, actor)
        [mounting_line] = _out_lines(job, [data.mounting_line_id], "mounting")
        piece = Item.objects.select_for_update(of=("self",)).select_related(
            "category", "karat__metal").get(pk=mounting_line.item_id)
        if piece.category.product_family not in MOUNTINGS:
            raise _field("mounting", _("Choose the piece the stones go into."))
        stone_lines_ = (_out_lines(job, data.stone_line_ids, "stones")
                        if data.stone_line_ids or not data.parcel_uses else [])
        stones = list(Item.objects.select_for_update(of=("self",)).select_related("category")
                      .prefetch_related("stones").filter(pk__in=[ln.item_id for ln in stone_lines_])
                      .order_by("pk"))
        if any(stone.category.product_family != ProductFamily.STONE for stone in stones):
            raise _field("stones", _("Only loose stones go into a piece."))
        after = _amount(data.gross_after_g, "gross_after_g", WEIGHT)
        if after <= 0:
            raise _field("gross_after_g", _("Enter the weight after setting."))
        labour = _amount(data.labour_amount, "labour_amount")
        if piece.category.product_family == ProductFamily.GOLD:
            category = ItemCategory.objects.filter(
                pk=data.category_id or 0, is_active=True, product_family=ProductFamily.DIAMOND,
                tracking=Tracking.SERIALIZED).first()
            if category is None:
                raise _field("category", _("Choose the diamond category the piece becomes."))
            piece.category = category
        if data.label_price not in (None, ""):
            label = _amount(data.label_price, "label_price")
            piece.label_price = label or None

        # Back from the setter in the same entry: the mounting at the value it left with, its
        # making cost, and its stones' cost now including the stones just set.
        back = stock_lines(
            [_part(piece, stone_cost=piece.stone_cost_amount
                   + sum((s.stone_cost_amount for s in stones), ZERO))],
            sign=1, counter_role=HOLDING, branch=job.branch, values=[mounting_line.metal_value])
        setting = apply_setting(piece=piece, stones=stones, after=after, labour=labour,
                                setter=job.setter, branch=job.branch, note=data.note,
                                actor=actor, job=job, extra_lines=back,
                                parcels=price_uses(data.parcel_uses, job.branch,
                                                   job.setter))
        change_item_status(piece.pk, to=ItemStatus.IN_STOCK, allowed_from=(ItemStatus.AT_WORKSHOP,),
                           movement_type=MovementType.WORKSHOP_RECEIPT,
                           business_date=setting.business_date, doc=DocRef(JOB_DOC, job.pk),
                           branch=job.branch, stock_delta=1)
        for line in (mounting_line, *stone_lines_):
            line.state, line.setting = JobLineState.SET, setting
            line.save(update_fields=["state", "setting", "updated_at"])
        _close_if_done(job)
    return setting


def undo_job_setting(setting: StoneSetting, *, reason: str = "", actor=None) -> StoneSetting:
    """Cancel a setting made from a job while the piece is untouched: the piece and its stones
    are back with the setter, as before."""
    piece = (Item.objects.select_for_update(of=("self",)).select_related("category")
             .get(pk=setting.piece_id))
    later = StoneSetting.objects.filter(piece=piece, status=DocStatus.POSTED,
                                        pk__gt=setting.pk).exists()
    if piece.status != ItemStatus.IN_STOCK or later:
        raise DomainError(_("The piece has moved on since: this setting cannot be undone."),
                          code="DIAMONDS_SETTING_LOCKED")
    job = SettingJob.objects.select_for_update().get(pk=setting.job_id)
    today = timezone.localdate()
    change_item_status(piece.pk, to=ItemStatus.AT_WORKSHOP, allowed_from=(ItemStatus.IN_STOCK,),
                       movement_type=MovementType.WORKSHOP_ISSUE, business_date=today,
                       doc=DocRef(JOB_DOC, job.pk), branch=setting.branch)
    stone_ids = list(setting.stones.values_list("stone_id", flat=True))
    Item.objects.filter(pk__in=stone_ids).update(status=ItemStatus.AT_WORKSHOP)
    ItemStone.objects.filter(item=piece, setting=setting).delete()
    give_back(doc_type="diamonds.StoneSetting", doc_id=setting.pk, day=today)
    piece.refresh_from_db()
    piece.gross_weight_g = setting.gross_before_g
    piece.stone_weight_ct -= setting.stones_carat
    piece.stone_cost_amount -= setting.stones_cost
    piece.cost_amount -= setting.labour_amount
    piece.category_id = job.lines.get(setting=setting, item_id=piece.pk).category_id
    piece.save()
    if setting.journal_entry_id:
        reverse_entry(setting.journal_entry_id, business_date=today,
                      memo=_("Cancelled %(number)s") % {"number": setting.number})
    job.lines.filter(setting=setting).update(state=JobLineState.OUT, setting=None)
    _close_if_done(job)
    setting.status = DocStatus.VOIDED
    setting.voided_at = timezone.now()
    setting.voided_by = getattr(actor, "user", None)
    setting.void_reason = reason.strip()[:300]
    setting.save()
    return setting


# --- 4. take stones out -----------------------------------------------------------------------

@dataclass(frozen=True)
class RemovalInput:
    setting_id: int
    stone_ids: tuple[int, ...]
    gross_after_g: Decimal | str
    fee_amount: Decimal | str = "0"
    setter_id: int | None = None
    note: str = ""


def remove_stones(data: RemovalInput, *, actor=None) -> StoneRemoval:
    require_enabled()
    with transaction.atomic():
        setting = (StoneSetting.objects.select_for_update(of=("self",))
                   .select_related("branch").filter(pk=data.setting_id).first())
        if setting is None or setting.status != DocStatus.POSTED:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.setting", branch=setting.branch)
        piece = Item.objects.select_for_update().get(pk=setting.piece_id)
        if piece.status != ItemStatus.IN_STOCK:
            raise DomainError(_("The piece is not in stock."), code="DIAMONDS_PIECE_NOT_IN_STOCK")
        ids = sorted(set(data.stone_ids))
        rows = list(setting.stones.select_related("stone").filter(stone_id__in=ids,
                                                                  removal__isnull=True))
        if not ids or len(rows) != len(ids):
            raise _field("stones", _("Choose stones that are still in the piece."))
        after = _amount(data.gross_after_g, "gross_after_g", WEIGHT)
        if after <= 0:
            raise _field("gross_after_g", _("Enter the weight after taking the stones out."))
        fee = _amount(data.fee_amount, "fee_amount")
        setter = _setter(data.setter_id) if data.setter_id else None

        today = timezone.localdate()
        carats = sum((row.carat for row in rows), ZERO)
        cost = sum((row.cost for row in rows), ZERO)
        removal = StoneRemoval.objects.create(
            branch=setting.branch, business_date=today, piece=piece, setting=setting,
            setter=setter, gross_before_g=piece.gross_weight_g, gross_after_g=after,
            stones_carat=carats, stones_cost=cost, fee_amount=fee, note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        doc = DocRef(REMOVAL_DOC, removal.pk)
        for row in rows:
            change_item_status(row.stone_id, to=ItemStatus.IN_STOCK,
                               allowed_from=(ItemStatus.CONSUMED,),
                               movement_type=MovementType.STONES_REMOVED, business_date=today,
                               doc=doc, branch=setting.branch, stock_delta=1)
            ItemStone.objects.filter(item=piece, setting=setting, source_id=row.stone_id).delete()
            row.removal = removal
            row.save(update_fields=["removal", "updated_at"])
        # The stones' cost stays on "diamonds and stones" (piece and loose stones share it).
        piece.gross_weight_g = after
        piece.stone_weight_ct -= carats
        piece.stone_cost_amount -= cost
        piece.cost_amount += fee
        piece.updated_by = getattr(actor, "user", None)
        piece.save()
        removal.number = allocate_number("SU", branch=setting.branch, fiscal_year=today.year)
        lines = labour_lines(fee, setter)
        if lines:
            removal.journal_entry = post_entry(
                branch=setting.branch, business_date=today, kind=EntryKind.AUTO,
                source_type=REMOVAL_DOC, source_id=removal.pk, lines=lines,
                memo=_("Stones taken out of %(barcode)s") % {"barcode": piece.barcode})
        removal.status = DocStatus.POSTED
        removal.posted_at = timezone.now()
        removal.posted_by = getattr(actor, "user", None)
        removal.save()
    return removal
