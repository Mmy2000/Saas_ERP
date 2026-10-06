"""Work orders with workshops: send gold out, receive goods back, loss and gain (§7.11, §8.5).

send     bulk gold or scrap leaves its lot; the workshop owes that fine gold
         Dr workshop (fine g)            Cr gold / scrap inventory (fine g)
         Dr goods at workshops (money)   Cr gold inventory (the making cost in the gold sent)
receive  new pieces, bulk gold and scrap come into stock
         Dr gold / scrap inventory       Cr workshop                    (fine g received)
         Dr metal loss                   Cr workshop                    (what did not come back)
         Dr workshop                     Cr metal gain                  (or what came back extra)
         Dr gold inventory (money)       Cr workshop                    (labour charged)
         Dr gold inventory (money)       Cr goods at workshops          (the carried making cost)
After a receipt the workshop owes no gold for the order; the labour stays owed until paid.
Loss and gain are explicit lines, never netted into a weight (§8.5).

In-house production orders (no workshop) book the same way, with "production in progress" in
place of the workshop and of goods at workshops. Labour is our own craftsmen's, already paid as
salaries: it is added to the cost of the goods against "production labour absorbed".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import Currency, ItemCategory, Karat, Tracking
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import UNIT_PRICE, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import ItemStatus, LotBalance, MovementType, StockLot
from apps.inventory.services import (
    SCRAP_CATEGORY_CODE,
    DocRef,
    change_item_status,
    create_item,
    lot_for,
    move_lot,
)
from apps.inventory.valuation import metal_value
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import (
    account_for,
    functional_commodity,
    metal_commodity,
    post_entry,
    reverse_entry,
)
from apps.org.models import Branch
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import fine_gram_value, functional_currency

from .models import (
    ReceiptLineType,
    WorkOrder,
    WorkOrderIssueLine,
    WorkOrderKind,
    WorkOrderPiece,
    WorkOrderReceiptLine,
)

DOC_TYPE = "manufacturing.WorkOrder"
ZERO = Decimal(0)


@dataclass(frozen=True)
class IssueLineInput:
    category_id: int
    karat_id: int
    gross_weight_g: Decimal | str
    qty: int = 0


@dataclass(frozen=True)
class IssueInput:
    branch_id: int
    workshop_id: int | None
    lines: tuple[IssueLineInput, ...]
    kind: str = WorkOrderKind.MANUFACTURE
    note: str = ""
    in_house: bool = False  # production in our own shop: no workshop
    craftsman_id: int | None = None


@dataclass(frozen=True)
class ReceiptLineInput:
    """What came back. The category decides the kind: pieces (one weight each), bulk gold
    (a total weight and an optional count), or the scrap category."""

    category_id: int
    karat_id: int
    piece_weights: tuple[Decimal | str, ...] = ()
    gross_weight_g: Decimal | str | None = None
    qty: int = 0
    labour_rate: Decimal | str = "0"  # charged per gram, in the company currency
    list_making_rate: Decimal | str = "0"  # selling making charge per gram for new pieces


@dataclass(frozen=True)
class ReceiptInput:
    lines: tuple[ReceiptLineInput, ...]
    accept_gain: bool = False
    note: str = ""


@dataclass
class PlannedLine:
    line_type: str
    category: ItemCategory
    karat: Karat
    qty: int
    gross_weight_g: Decimal
    fine_weight_g: Decimal
    labour_rate: Decimal
    labour_amount: Decimal
    list_making_rate: Decimal
    pieces: list[Decimal]
    cost_amount: Decimal = ZERO
    metal_value: Decimal = ZERO


@dataclass
class ReceiptPlan:
    lines: list[PlannedLine] = field(default_factory=list)
    issued: dict[str, Decimal] = field(default_factory=dict)  # fine g per metal
    received: dict[str, Decimal] = field(default_factory=dict)
    loss: dict[str, Decimal] = field(default_factory=dict)
    gain: dict[str, Decimal] = field(default_factory=dict)
    labour: Decimal = ZERO
    carried_to_goods: Decimal = ZERO
    carried_written_off: Decimal = ZERO  # nothing but scrap came back
    values: dict[tuple[str, str], Decimal] = field(default_factory=dict)  # ("loss", metal)


# --- sending -------------------------------------------------------------------------------------

def _line_error(index: int, message: str, name: str | None = None) -> ValidationError:
    detail = {name: [message]} if name else [message]
    return ValidationError(message, fields={"lines": {str(index): detail}})


def issue_work_order(data: IssueInput, *, actor=None) -> WorkOrder:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise ValidationError(_("Unknown branch."), fields={"branch": [_("Unknown branch.")]})
        if actor is not None:
            actor.require("manufacturing.order.issue", branch=branch)
        workshop = None
        if not data.in_house:
            workshop = (Party.objects.filter(pk=data.workshop_id, is_active=True,
                                             roles__role=PartyRoleType.WORKSHOP).first()
                        if data.workshop_id else None)
            if workshop is None:
                raise ValidationError(_("Choose the workshop."),
                                      fields={"workshop": [_("Required.")]})
        craftsman = None
        if data.craftsman_id:
            from apps.hr.models import Employee

            craftsman = Employee.objects.filter(pk=data.craftsman_id, is_active=True).first()
            if craftsman is None:
                raise ValidationError(_("Choose an active employee."),
                                      fields={"craftsman": [_("Choose an active employee.")]})
        if data.kind not in WorkOrderKind.values:
            raise ValidationError(_("Choose the kind of work."), fields={"kind": [_("Required.")]})
        if not data.lines:
            message = _("Add the gold you are sending.")
            raise ValidationError(message, fields={"lines": [message]})

        today = timezone.localdate()
        order = WorkOrder.objects.create(
            branch=branch, workshop=workshop, craftsman=craftsman, kind=data.kind,
            business_date=today, note=data.note.strip(), created_by=getattr(actor, "user", None))
        ref = DocRef(DOC_TYPE, order.pk)
        lines = []
        for index, line in enumerate(data.lines):
            lot = (StockLot.objects.select_related("category", "karat__metal")
                   .filter(category_id=line.category_id, karat_id=line.karat_id, branch=branch,
                           karat__isnull=False).first())
            balance = LotBalance.objects.filter(lot=lot).first() if lot else None
            try:
                gross = quantize(line.gross_weight_g, WEIGHT)
            except (TypeError, ValueError):
                raise _line_error(index, _("Enter the weight.")) from None
            qty = max(int(line.qty or 0), 0)
            if (balance is None or gross <= 0 or gross > balance.gross_weight_g
                    or qty > balance.qty):
                available = balance.gross_weight_g if balance else ZERO
                raise _line_error(index, _("Only %(weight)s g of this at %(branch)s.")
                                  % {"weight": available, "branch": branch.name})
            cost = round_money(balance.cost_amount * gross / balance.gross_weight_g)
            movement = move_lot(lot, qty=-qty, gross_weight_g=-gross, cost_amount=-cost,
                                movement_type=_out_type(order), business_date=today, doc=ref)
            fine = -movement.fine_weight_g
            lines.append(WorkOrderIssueLine(
                order=order, lot=lot, category=lot.category, karat=lot.karat, qty=qty,
                gross_weight_g=gross, fine_weight_g=fine, cost_amount=cost,
                metal_value=metal_value(lot.karat, fine)))
        WorkOrderIssueLine.objects.bulk_create(lines)

        order.issued_gross_weight_g = sum((ln.gross_weight_g for ln in lines), ZERO)
        order.issued_fine_weight_g = sum((ln.fine_weight_g for ln in lines), ZERO)
        order.carried_cost = sum((ln.cost_amount for ln in lines if not _is_scrap(ln.category)),
                                 ZERO)
        order.number = allocate_number("PRD" if order.in_house else "WO", branch=branch,
                                       fiscal_year=today.year)
        memo = (_("Gold into production %(number)s") if order.in_house
                else _("Gold sent to workshop %(number)s"))
        order.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO,
            lines=_issue_ledger(order, lines), source_type=DOC_TYPE, source_id=order.pk,
            memo=memo % {"number": order.number})
        order.status = DocStatus.POSTED
        order.posted_at = timezone.now()
        order.posted_by = getattr(actor, "user", None)
        order.save()
    return order


def _is_scrap(category) -> bool:
    return category.code == SCRAP_CATEGORY_CODE


def _inventory_role(category) -> str:
    return "inventory_scrap" if _is_scrap(category) else "inventory_gold"


def _holder(order: WorkOrder):
    """Who holds the gold while the order is out: the workshop (on its account), or us."""
    if order.in_house:
        return account_for("inventory_in_production"), None
    return account_for("workshops"), order.workshop


def _carried_account(order: WorkOrder):
    return account_for("inventory_in_production" if order.in_house else "inventory_at_workshop")


def _out_type(order: WorkOrder) -> str:
    return (MovementType.PRODUCTION_CONSUME if order.in_house
            else MovementType.WORKSHOP_ISSUE)


def _in_type(order: WorkOrder) -> str:
    return (MovementType.PRODUCTION_OUTPUT if order.in_house
            else MovementType.WORKSHOP_RECEIPT)


def _issue_ledger(order: WorkOrder, lines) -> list[LedgerLine]:
    workshop, party = _holder(order)
    result: list[LedgerLine] = []
    metal: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in lines:
        key = (_inventory_role(line.category), line.karat.metal.code)
        metal[key][0] += line.fine_weight_g
        metal[key][1] += line.metal_value
    for (role, metal_code), (fine, value) in sorted(metal.items()):
        commodity = metal_commodity(metal_code)
        result += [
            LedgerLine(account=workshop, commodity=commodity, quantity=fine,
                       functional_amount=value, party=party),
            LedgerLine(account=account_for(role), commodity=commodity, quantity=-fine,
                       functional_amount=-value),
        ]
    if order.carried_cost:
        home = functional_commodity()
        result += [
            LedgerLine(account=_carried_account(order), commodity=home,
                       quantity=order.carried_cost),
            LedgerLine(account=account_for("inventory_gold"), commodity=home,
                       quantity=-order.carried_cost),
        ]
    return result


def _locked(order_id: int) -> WorkOrder:
    order = (WorkOrder.objects.select_for_update(of=("self",))
             .select_related("branch", "workshop").filter(pk=order_id).first())
    if order is None:
        raise NotFound(_("Not found."))
    return order


def cancel_work_order(order_id: int, *, reason: str = "", actor=None) -> WorkOrder:
    """Before anything came back: the gold returns to its lots and the entry is reversed."""
    with transaction.atomic():
        order = _locked(order_id)
        if actor is not None:
            actor.require("manufacturing.order.cancel", branch=order.branch)
        if not order.at_workshop:
            raise DomainError(_("This order is no longer in production.") if order.in_house
                              else _("Only orders still at the workshop can be cancelled."),
                              code="MFG_NOT_AT_WORKSHOP")
        today = timezone.localdate()
        ref = DocRef(DOC_TYPE, order.pk)
        for line in order.issue_lines.select_related("lot__karat").order_by("lot_id"):
            move_lot(line.lot, qty=line.qty, gross_weight_g=line.gross_weight_g,
                     cost_amount=line.cost_amount, movement_type=_in_type(order),
                     business_date=today, doc=ref)
        if order.journal_entry_id:
            reverse_entry(order.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": order.number})
        order.status = DocStatus.VOIDED
        order.voided_at = timezone.now()
        order.voided_by = getattr(actor, "user", None)
        order.void_reason = reason.strip()[:300]
        order.save()
    return order


# --- receiving -----------------------------------------------------------------------------------

def plan_receipt(order: WorkOrder, data: ReceiptInput) -> ReceiptPlan:
    """Check what came back and work out loss, gain and costs. Never writes."""
    if not data.lines:
        message = _("Add what came back.")
        raise ValidationError(message, fields={"lines": [message]})
    categories = {c.pk: c for c in ItemCategory.objects.filter(
        pk__in=[ln.category_id for ln in data.lines], is_active=True)}
    karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(
        pk__in=[ln.karat_id for ln in data.lines], is_active=True)}
    plan = ReceiptPlan()
    for index, line in enumerate(data.lines):
        category, karat = categories.get(line.category_id), karats.get(line.karat_id)
        if category is None:
            raise _line_error(index, _("Unknown category."), "category")
        if karat is None:
            raise _line_error(index, _("Choose the karat."), "karat")
        if _is_scrap(category):
            line_type = ReceiptLineType.SCRAP
        elif category.tracking == Tracking.SERIALIZED:
            line_type = ReceiptLineType.PIECES
        else:
            line_type = ReceiptLineType.BULK

        pieces: list[Decimal] = []
        if line_type == ReceiptLineType.PIECES:
            try:
                pieces = [quantize(w, WEIGHT) for w in line.piece_weights]
            except (TypeError, ValueError):
                pieces = []
            if not pieces or any(w <= 0 for w in pieces):
                raise _line_error(index, _("Enter the weight of each piece."), "piece_weights")
            qty, gross = len(pieces), sum(pieces, ZERO)
        else:
            try:
                gross = quantize(line.gross_weight_g or "0", WEIGHT)
            except (TypeError, ValueError):
                gross = ZERO
            if gross <= 0:
                raise _line_error(index, _("Enter the total weight."), "gross_weight_g")
            qty = max(int(line.qty or 0), 0) if line_type == ReceiptLineType.BULK else 0
        try:
            labour_rate = quantize(line.labour_rate or "0", UNIT_PRICE)
            list_rate = quantize(line.list_making_rate or "0", UNIT_PRICE)
        except (TypeError, ValueError):
            raise _line_error(index, _("Enter a number."), "labour_rate") from None
        if labour_rate < 0 or list_rate < 0:
            raise _line_error(index, _("Must not be negative."), "labour_rate")
        if line_type == ReceiptLineType.SCRAP:
            labour_rate = list_rate = ZERO  # scrap is not worked goods
        fine = (sum((fine_weight(w, karat.fineness) for w in pieces), ZERO) if pieces
                else fine_weight(gross, karat.fineness))
        plan.lines.append(PlannedLine(
            line_type=line_type, category=category, karat=karat, qty=qty, gross_weight_g=gross,
            fine_weight_g=fine, labour_rate=labour_rate,
            labour_amount=round_money(labour_rate * gross), list_making_rate=list_rate,
            pieces=pieces, metal_value=metal_value(karat, fine)))

    issued: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in order.issue_lines.select_related("karat__metal"):
        issued[line.karat.metal.code] += line.fine_weight_g
    received: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for planned in plan.lines:
        received[planned.karat.metal.code] += planned.fine_weight_g
    plan.issued, plan.received = dict(issued), dict(received)
    for metal_code in sorted({*issued, *received}):
        difference = issued[metal_code] - received[metal_code]
        if difference > 0:
            plan.loss[metal_code] = difference
        elif difference < 0:
            plan.gain[metal_code] = -difference
    if plan.gain and not data.accept_gain:
        raise DomainError(_("More gold came back than was sent. Confirm the gain to continue."),
                          code="MFG_GAIN_UNCONFIRMED")

    # The making cost carried in the gold sent out moves into the worked goods, by weight.
    plan.labour = sum((ln.labour_amount for ln in plan.lines), ZERO)
    worked = [ln for ln in plan.lines if ln.line_type != ReceiptLineType.SCRAP]
    weight = sum((ln.gross_weight_g for ln in worked), ZERO)
    left = order.carried_cost
    for position, planned in enumerate(worked):
        share = (left if position == len(worked) - 1
                 else round_money(order.carried_cost * planned.gross_weight_g / weight))
        left -= share
        planned.cost_amount = planned.labour_amount + share
    if worked:
        plan.carried_to_goods = order.carried_cost
    else:
        plan.carried_written_off = order.carried_cost
    return plan


def _receipt_ledger(order: WorkOrder, plan: ReceiptPlan) -> list[LedgerLine]:
    workshop, party = _holder(order)
    home = functional_commodity()
    result: list[LedgerLine] = []

    def add(account, commodity, quantity, functional=None, party=None):
        if quantity:
            result.append(LedgerLine(account=account, commodity=commodity, quantity=quantity,
                                     functional_amount=functional, party=party))

    stock: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in plan.lines:
        key = (_inventory_role(line.category), line.karat.metal.code)
        stock[key][0] += line.fine_weight_g
        stock[key][1] += line.metal_value
    if order.in_house:
        _mirror_issue_values(order, plan, stock)
    for (role, metal_code), (fine, value) in sorted(stock.items()):
        commodity = metal_commodity(metal_code)
        add(account_for(role), commodity, fine, value)
        add(workshop, commodity, -fine, -value, party=party)
    for metal_code, fine in sorted(plan.loss.items()):
        commodity, value = metal_commodity(metal_code), plan.values.get(
            ("loss", metal_code), _value(metal_code, fine))
        add(account_for("metal_loss"), commodity, fine, value)
        add(workshop, commodity, -fine, -value, party=party)
    for metal_code, fine in sorted(plan.gain.items()):
        commodity, value = metal_commodity(metal_code), plan.values.get(
            ("gain", metal_code), _value(metal_code, fine))
        add(workshop, commodity, fine, value, party=party)
        add(account_for("metal_gain"), commodity, -fine, -value)

    add(account_for("inventory_gold"), home, plan.labour + plan.carried_to_goods)
    add(account_for("metal_loss"), home, plan.carried_written_off)
    if order.in_house:  # our own labour, already paid as salaries, becomes part of the cost
        add(account_for("labour_absorbed"), home, -plan.labour)
    else:
        add(workshop, home, -plan.labour, party=party)
    add(_carried_account(order), home, -order.carried_cost)
    return result


def _value(metal_code: str, fine: Decimal) -> Decimal:
    return round_money(fine * fine_gram_value(metal_code))


def _mirror_issue_values(order: WorkOrder, plan: ReceiptPlan, stock) -> None:
    """In-house, "production in progress" must come back to zero: what comes out (goods, scrap
    and loss, less any gain) is valued at the value per fine gram the gold went in at, and the
    last share takes the rounding. Fills `stock` values and plan.values for loss and gain."""
    issued: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in order.issue_lines.select_related("karat__metal"):
        issued[line.karat.metal.code][0] += line.fine_weight_g
        issued[line.karat.metal.code][1] += line.metal_value
    for metal_code in sorted({*issued, *(m for _r, m in stock)}):
        fine_in, value_in = issued[metal_code]
        rate = value_in / fine_in if fine_in else fine_gram_value(metal_code)
        loss = round_money(plan.loss.get(metal_code, ZERO) * rate)
        gain = round_money(plan.gain.get(metal_code, ZERO) * rate)
        keys = sorted(key for key in stock if key[1] == metal_code)
        if not keys:  # nothing came back: it is all loss
            loss = value_in + gain
        left = value_in - loss + gain
        for position, key in enumerate(keys):
            value = left if position == len(keys) - 1 else round_money(stock[key][0] * rate)
            stock[key][1] = value
            left -= value
        plan.values[("loss", metal_code)] = loss
        plan.values[("gain", metal_code)] = gain


def receive_work_order(order_id: int, data: ReceiptInput, *, actor=None) -> WorkOrder:
    with transaction.atomic():
        order = _locked(order_id)
        if actor is not None:
            actor.require("manufacturing.order.receive", branch=order.branch)
        if not order.at_workshop:
            raise DomainError(_("This order is no longer in production.") if order.in_house
                              else _("This order is not at the workshop."),
                              code="MFG_NOT_AT_WORKSHOP")
        plan = plan_receipt(order, data)

        today = timezone.localdate()
        ref = DocRef(DOC_TYPE, order.pk)
        currency = Currency.objects.get(code=functional_currency())
        for planned in plan.lines:
            saved = WorkOrderReceiptLine.objects.create(
                order=order, line_type=planned.line_type, category=planned.category,
                karat=planned.karat, qty=planned.qty, gross_weight_g=planned.gross_weight_g,
                fine_weight_g=planned.fine_weight_g, labour_rate=planned.labour_rate,
                labour_amount=planned.labour_amount, list_making_rate=planned.list_making_rate,
                cost_amount=planned.cost_amount, metal_value=planned.metal_value)
            if planned.line_type == ReceiptLineType.PIECES:
                left = planned.cost_amount
                for position, weight in enumerate(planned.pieces):
                    cost = (left if position == len(planned.pieces) - 1 else round_money(
                        planned.cost_amount * weight / planned.gross_weight_g))
                    left -= cost
                    item = create_item(
                        category=planned.category, karat=planned.karat, gross_weight_g=weight,
                        branch=order.branch, business_date=today, doc=ref,
                        supplier=order.workshop, cost_currency=currency,
                        cost_making_rate=quantize(cost / weight, UNIT_PRICE), cost_amount=cost,
                        list_making_rate=planned.list_making_rate,
                        movement_type=_in_type(order), actor=actor)
                    WorkOrderPiece.objects.create(line=saved, gross_weight_g=weight, item=item)
            else:
                # Scrap carries its gold value as cost, like scrap bought or traded in.
                cost = (planned.metal_value if planned.line_type == ReceiptLineType.SCRAP
                        else planned.cost_amount)
                move_lot(lot_for(planned.category, planned.karat, order.branch), qty=planned.qty,
                         gross_weight_g=planned.gross_weight_g, cost_amount=cost,
                         movement_type=_in_type(order), business_date=today, doc=ref)

        order.received_fine_weight_g = sum(plan.received.values(), ZERO)
        order.loss_fine_weight_g = sum(plan.loss.values(), ZERO)
        order.gain_fine_weight_g = sum(plan.gain.values(), ZERO)
        order.labour_amount = plan.labour
        order.receipt_note = data.note.strip()[:300]
        order.receive_entry = post_entry(
            branch=order.branch, business_date=today, kind=EntryKind.AUTO,
            lines=_receipt_ledger(order, plan), source_type=DOC_TYPE, source_id=order.pk,
            memo=(_("Produced %(number)s") if order.in_house
                  else _("Received from workshop %(number)s")) % {"number": order.number})
        order.received_at = timezone.now()
        order.received_on = today
        order.received_by = getattr(actor, "user", None)
        order.save()
    return order


def cancel_receipt(order_id: int, *, actor=None) -> WorkOrder:
    """Undo a receipt while the goods are untouched: the order is at the workshop again."""
    with transaction.atomic():
        order = _locked(order_id)
        if actor is not None:
            actor.require("manufacturing.order.cancel", branch=order.branch)
        if order.status != DocStatus.POSTED or order.received_at is None:
            raise DomainError(_("This order has no receipt to cancel."), code="MFG_NOT_RECEIVED")
        today = timezone.localdate()
        ref = DocRef(DOC_TYPE, order.pk)
        lines = list(order.receipt_lines.select_related("category", "karat")
                     .prefetch_related("pieces"))
        for line in sorted(lines, key=lambda ln: ln.pk):
            if line.line_type == ReceiptLineType.PIECES:
                for piece in sorted(line.pieces.all(), key=lambda p: p.item_id):
                    change_item_status(piece.item_id, to=ItemStatus.VOIDED,
                                       allowed_from=(ItemStatus.IN_STOCK,),
                                       movement_type=_out_type(order),
                                       business_date=today, doc=ref, branch=order.branch)
            else:
                cost = (line.metal_value if line.line_type == ReceiptLineType.SCRAP
                        else line.cost_amount)
                move_lot(lot_for(line.category, line.karat, order.branch), qty=-line.qty,
                         gross_weight_g=-line.gross_weight_g, cost_amount=-cost,
                         movement_type=_out_type(order), business_date=today, doc=ref)
        reverse_entry(order.receive_entry_id, business_date=today,
                      memo=_("Cancelled receipt of %(number)s") % {"number": order.number})
        order.receipt_lines.all().delete()
        order.received_at = order.received_on = order.received_by = None
        order.receive_entry = None
        order.received_fine_weight_g = order.loss_fine_weight_g = ZERO
        order.gain_fine_weight_g = order.labour_amount = ZERO
        order.receipt_note = ""
        order.save()
    return order
