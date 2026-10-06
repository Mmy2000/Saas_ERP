"""Wholesale to trade accounts, by weight (§7.7; legacy `Sh2`).

Each line is priced as its gold at the board's sell price for the karat, plus a making charge
per gram agreed with the trader (the category's list rate unless given).

settled in gold    Dr trade account  fine g (at today's value)  Cr gold inventory  fine g
                   Dr trade account  making charge             Cr making revenue
settled in money   Dr trade account  gold + making charge      Cr gold / making revenue
                   Dr cost of goods  fine g                    Cr gold inventory  fine g
both               Dr cost of goods  making cost paid          Cr gold inventory

A return takes whole lines of a posted sale back and reverses them at the sale's values.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import ItemCategory
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import UNIT_PRICE, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import Item, ItemStatus, LotBalance, MovementType, StockLot
from apps.inventory.services import SCRAP_CATEGORY_CODE, DocRef, change_item_status, move_lot
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
from apps.pricing.engine import category_making_rate
from apps.pricing.models import PriceSide
from apps.pricing.selectors import current_price_board, functional_currency

from .models import (
    SettlementBasis,
    TradeReturn,
    TradeReturnLine,
    TradeSale,
    TradeSaleLine,
)

SALE_DOC = "sales.TradeSale"
RETURN_DOC = "sales.TradeReturn"
ZERO = Decimal(0)


@dataclass(frozen=True)
class TradeLineInput:
    """A piece (barcode or item id) or bulk gold (category, karat, weight, pieces)."""

    barcode: str = ""
    item_id: int | None = None
    category_id: int | None = None
    karat_id: int | None = None
    gross_weight_g: Decimal | str | None = None
    qty: int = 0
    making_rate: Decimal | str | None = None  # per gram; None = the category's list rate


@dataclass(frozen=True)
class TradeSaleInput:
    branch_id: int
    trade_account_id: int | None
    settlement_basis: str
    lines: tuple[TradeLineInput, ...]
    note: str = ""


@dataclass
class TradeLineQuote:
    category: ItemCategory
    karat: object
    item: Item | None
    lot: StockLot | None
    qty: int
    gross_weight_g: Decimal
    fine_weight_g: Decimal
    metal_price_per_g: Decimal
    making_rate: Decimal
    metal_amount: Decimal
    making_amount: Decimal
    cost_amount: Decimal
    metal_value: Decimal

    @property
    def label(self) -> str:
        return self.item.barcode if self.item else ""


@dataclass
class TradeQuote:
    branch: Branch
    board: object
    basis: str
    lines: list[TradeLineQuote] = field(default_factory=list)
    categories: dict[int, dict] = field(default_factory=dict)  # id → name, list rate

    def total(self, name: str):
        return sum((getattr(line, name) for line in self.lines), ZERO)

    @property
    def money_amount(self) -> Decimal:
        """What the trader owes in money."""
        making = self.total("making_amount")
        return making if self.basis == SettlementBasis.METAL else making + self.total(
            "metal_amount")


def _line_error(index: int, message: str) -> ValidationError:
    return ValidationError(message, fields={"lines": {str(index): [message]}})


def _piece(line: TradeLineInput, branch) -> Item:
    items = Item.objects.select_related("category", "karat__metal")
    item = (items.filter(pk=line.item_id).first() if line.item_id
            else items.filter(barcode=line.barcode.strip()).first())
    if item is None:
        raise DomainError(_("No piece with barcode %(barcode)s.") % {"barcode": line.barcode},
                          code="SALES_ITEM_NOT_FOUND")
    if item.status != ItemStatus.IN_STOCK or item.branch_id != branch.pk:
        raise DomainError(_("Piece %(barcode)s is not in stock at %(branch)s.")
                          % {"barcode": item.barcode, "branch": branch.name},
                          code="SALES_ITEM_UNAVAILABLE")
    if item.karat is None:
        raise DomainError(_("Piece %(barcode)s has no karat and cannot be priced by weight.")
                          % {"barcode": item.barcode}, code="PRICING_NO_KARAT")
    if item.stone_cost_amount or item.category.product_family in ("diamond", "stone"):
        raise DomainError(_("Diamond pieces are sold at their label price, not by weight."),
                          code="SALES_DIAMOND_BY_WEIGHT")
    return item


def _lot(line: TradeLineInput, branch):
    lot = (StockLot.objects.select_related("category", "karat__metal")
           .filter(category_id=line.category_id, karat_id=line.karat_id, branch=branch,
                   karat__isnull=False)
           .exclude(category__code=SCRAP_CATEGORY_CODE).first())
    balance = LotBalance.objects.filter(lot=lot).first() if lot else None
    if balance is None or balance.gross_weight_g <= 0:
        raise DomainError(_("No gold of this kind in stock here."), code="SALES_BULK_NONE")
    return lot, balance


def _making_rate(line: TradeLineInput, category, home: str) -> Decimal:
    if line.making_rate in (None, ""):
        return category_making_rate(category.pk, home)
    try:
        rate = quantize(line.making_rate, UNIT_PRICE)
    except (TypeError, ValueError):
        raise ValidationError(_("Enter a number.")) from None
    if rate < 0:
        raise ValidationError(_("The making charge cannot be negative."))
    return rate


def _branch(branch_id) -> Branch:
    branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    if branch is None:
        raise ValidationError(_("Unknown branch."), fields={"branch": [_("Unknown branch.")]})
    return branch


def quote_trade_sale(data: TradeSaleInput) -> TradeQuote:
    """Price the goods for the screen and for posting. Never writes."""
    branch = _branch(data.branch_id)
    if data.settlement_basis not in SettlementBasis.values:
        message = _("Choose how the trader pays.")
        raise ValidationError(message, fields={"settlement_basis": [message]})
    board = current_price_board()
    if board is None:
        raise DomainError(_("No gold price has been published yet."), code="PRICING_NO_BOARD")
    home = functional_currency()
    quote = TradeQuote(branch=branch, board=board, basis=data.settlement_basis)
    seen: set[int] = set()
    taken: dict[int, list] = defaultdict(lambda: [ZERO, 0])  # per lot: weight, pieces
    for index, line in enumerate(data.lines):
        try:
            if line.barcode or line.item_id:
                item = _piece(line, branch)
                if item.pk in seen:
                    raise DomainError(_("Piece %(barcode)s is already on this sale.")
                                      % {"barcode": item.barcode}, code="SALES_DUPLICATE_ITEM")
                seen.add(item.pk)
                lot, category, karat, qty = None, item.category, item.karat, 1
                gross, fine, cost = item.gross_weight_g, item.fine_weight_g, item.cost_amount
            else:
                lot, balance = _lot(line, branch)
                item, category, karat, qty = None, lot.category, lot.karat, max(line.qty, 0)
                try:
                    gross = quantize(line.gross_weight_g, WEIGHT)
                except (TypeError, ValueError):
                    raise ValidationError(_("Enter the weight.")) from None
                used = taken[lot.pk]
                used[0] += gross
                used[1] += qty
                if gross <= 0 or used[0] > balance.gross_weight_g or used[1] > balance.qty:
                    raise DomainError(
                        _("Only %(weight)s g (%(qty)s pieces) of %(name)s here.")
                        % {"weight": balance.gross_weight_g, "qty": balance.qty,
                           "name": lot.category.name}, code="SALES_BULK_SHORT")
                fine = fine_weight(gross, karat.fineness)
                cost = round_money(balance.cost_amount * gross / balance.gross_weight_g)
            rate = _making_rate(line, category, home)
            price = board.price(karat, PriceSide.SELL)
        except (DomainError, ValidationError) as exc:
            raise _line_error(index, exc.message) from exc
        quote.categories.setdefault(category.pk, {
            "name": category.name, "list_rate": category_making_rate(category.pk, home)})
        quote.lines.append(TradeLineQuote(
            category=category, karat=karat, item=item, lot=lot, qty=qty, gross_weight_g=gross,
            fine_weight_g=fine, metal_price_per_g=price, making_rate=rate,
            metal_amount=round_money(price * gross), making_amount=round_money(rate * gross),
            cost_amount=cost, metal_value=metal_value(karat, fine)))
    return quote


def _ledger_lines(in_metal: bool, party, lines, sign: int = 1) -> list[LedgerLine]:
    """sign = +1 for the sale, -1 for a return of some of its lines."""
    home = functional_commodity()
    trade = account_for("trade_accounts")
    result: list[LedgerLine] = []

    def add(account, commodity, quantity, functional=None, with_party=False):
        if quantity:
            result.append(LedgerLine(account=account, commodity=commodity,
                                     quantity=sign * quantity,
                                     functional_amount=None if functional is None
                                     else sign * functional,
                                     party=party if with_party else None))

    metal: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in lines:
        metal[line.karat.metal.code][0] += line.fine_weight_g
        metal[line.karat.metal.code][1] += line.metal_value
    for metal_code, (fine, value) in sorted(metal.items()):
        commodity = metal_commodity(metal_code)
        if in_metal:  # the trader now owes the fine gold itself
            add(trade, commodity, fine, value, with_party=True)
        else:
            add(account_for("cogs_gold"), commodity, fine, value)
        add(account_for("inventory_gold"), commodity, -fine, -value)

    gold = sum((line.metal_amount for line in lines), ZERO)
    making = sum((line.making_amount for line in lines), ZERO)
    add(trade, home, making if in_metal else gold + making, with_party=True)
    if not in_metal:
        add(account_for("sales_gold"), home, -gold)
    add(account_for("sales_making"), home, -making)
    cost = sum((line.cost_amount for line in lines), ZERO)
    add(account_for("cogs_gold"), home, cost)
    add(account_for("inventory_gold"), home, -cost)
    return result


def post_trade_sale(data: TradeSaleInput, *, actor=None) -> TradeSale:
    with transaction.atomic():
        branch = _branch(data.branch_id)
        if actor is not None:
            actor.require("sales.trade.create", branch=branch)
        party = (Party.objects.filter(pk=data.trade_account_id, is_active=True,
                                      roles__role=PartyRoleType.TRADE_ACCOUNT).first()
                 if data.trade_account_id else None)
        if party is None:
            raise ValidationError(_("Choose the trade account."),
                                  fields={"trade_account": [_("Required.")]})
        if not data.lines:
            raise ValidationError(_("Add at least one piece or weight."),
                                  fields={"lines": [_("Add at least one piece or weight.")]})
        quote = quote_trade_sale(data)

        today = timezone.localdate()
        doc = TradeSale.objects.create(
            branch=branch, trade_account=party, settlement_basis=quote.basis,
            price_board=quote.board, business_date=today, note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        ref = DocRef(SALE_DOC, doc.pk)
        for line in quote.lines:
            if line.lot is not None:
                movement = move_lot(line.lot, qty=-line.qty, gross_weight_g=-line.gross_weight_g,
                                    cost_amount=-line.cost_amount,
                                    movement_type=MovementType.TRADE_SALE, business_date=today,
                                    doc=ref)
                line.fine_weight_g = -movement.fine_weight_g
        for line in sorted((x for x in quote.lines if x.item), key=lambda x: x.item.pk):
            change_item_status(line.item.pk, to=ItemStatus.SOLD,
                               allowed_from=(ItemStatus.IN_STOCK,), branch=branch,
                               movement_type=MovementType.TRADE_SALE,
                               business_date=today, doc=ref)
        TradeSaleLine.objects.bulk_create([TradeSaleLine(
            trade_sale=doc, item=line.item, lot=line.lot, category=line.category,
            karat=line.karat, qty=line.qty, gross_weight_g=line.gross_weight_g,
            fine_weight_g=line.fine_weight_g, metal_price_per_g=line.metal_price_per_g,
            making_rate=line.making_rate, metal_amount=line.metal_amount,
            making_amount=line.making_amount, cost_amount=line.cost_amount,
            metal_value=line.metal_value) for line in quote.lines])

        doc.total_qty = int(quote.total("qty"))
        doc.total_gross_weight_g = quote.total("gross_weight_g")
        doc.total_fine_weight_g = quote.total("fine_weight_g")
        doc.metal_amount = quote.total("metal_amount")
        doc.making_amount = quote.total("making_amount")
        doc.money_amount = quote.money_amount
        doc.number = allocate_number("TS", branch=branch, fiscal_year=today.year)
        doc.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO,
            lines=_ledger_lines(doc.in_metal, party, quote.lines),
            source_type=SALE_DOC, source_id=doc.pk,
            memo=_("Wholesale %(number)s") % {"number": doc.number})
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.posted_by = getattr(actor, "user", None)
        doc.save()
    return doc


def void_trade_sale(sale_id: int, *, reason: str = "", actor=None) -> TradeSale:
    """Cancel a posted trade sale: goods back into stock, the entry reversed."""
    with transaction.atomic():
        doc = (TradeSale.objects.select_for_update(of=("self",)).select_related("branch")
               .filter(pk=sale_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.trade.void", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        if doc.returns.filter(status=DocStatus.POSTED).exists():
            raise DomainError(_("Cancel the returns of this sale first."),
                              code="SALES_HAS_RETURNS")
        today = timezone.localdate()
        _goods_back(doc, doc.lines.all(), DocRef(SALE_DOC, doc.pk), today)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": doc.number})
        doc.status = DocStatus.VOIDED
        doc.voided_at = timezone.now()
        doc.voided_by = getattr(actor, "user", None)
        doc.void_reason = reason.strip()[:300]
        doc.save()
    return doc


def _goods_back(sale: TradeSale, lines, ref: DocRef, today) -> None:
    for line in lines.select_related("lot__karat").order_by("item_id", "lot_id"):
        if line.lot_id:
            move_lot(line.lot, qty=line.qty, gross_weight_g=line.gross_weight_g,
                     cost_amount=line.cost_amount, movement_type=MovementType.TRADE_RETURN,
                     business_date=today, doc=ref)
        else:
            change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                               allowed_from=(ItemStatus.SOLD,),
                               movement_type=MovementType.TRADE_RETURN, business_date=today,
                               doc=ref, branch=sale.branch, stock_delta=1)


def _goods_out(sale: TradeSale, lines, ref: DocRef, today) -> None:
    for line in lines.select_related("lot__karat").order_by("item_id", "lot_id"):
        if line.lot_id:
            move_lot(line.lot, qty=-line.qty, gross_weight_g=-line.gross_weight_g,
                     cost_amount=-line.cost_amount, movement_type=MovementType.TRADE_SALE,
                     business_date=today, doc=ref)
        else:
            change_item_status(line.item_id, to=ItemStatus.SOLD,
                               allowed_from=(ItemStatus.IN_STOCK,),
                               movement_type=MovementType.TRADE_SALE, business_date=today,
                               doc=ref, branch=sale.branch)


# --- returns ------------------------------------------------------------------------------------

def returned_line_ids(sale: TradeSale) -> set[int]:
    """Lines of `sale` already taken back by a return that is not cancelled."""
    return set(TradeReturnLine.objects.filter(
        original_line__trade_sale=sale, trade_return__status=DocStatus.POSTED,
    ).values_list("original_line_id", flat=True))


def post_trade_return(sale_id: int, line_ids: list[int], *, reason: str = "",
                      actor=None) -> TradeReturn:
    with transaction.atomic():
        sale = (TradeSale.objects.select_for_update(of=("self",))
                .select_related("branch", "trade_account").filter(pk=sale_id).first())
        if sale is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.trade.return", branch=sale.branch)
        if sale.status != DocStatus.POSTED:
            raise DomainError(_("Only posted sales can take returns."), code="DOC_NOT_POSTED")
        wanted = set(line_ids)
        lines = list(sale.lines.select_related("karat__metal").filter(pk__in=wanted))
        if not lines or len(lines) != len(wanted):
            raise ValidationError(_("Choose the pieces being returned."),
                                  fields={"lines": [_("Choose at least one piece.")]})
        if returned_line_ids(sale) & wanted:
            raise DomainError(_("Some of these pieces were already returned."),
                              code="SALES_ALREADY_RETURNED")

        today = timezone.localdate()
        doc = TradeReturn.objects.create(
            branch=sale.branch, business_date=today, original_sale=sale,
            trade_account=sale.trade_account, reason=reason.strip()[:300],
            created_by=getattr(actor, "user", None))
        _goods_back(sale, sale.lines.filter(pk__in=wanted), DocRef(RETURN_DOC, doc.pk), today)
        TradeReturnLine.objects.bulk_create(
            [TradeReturnLine(trade_return=doc, original_line=line) for line in lines])

        making = sum((line.making_amount for line in lines), ZERO)
        doc.total_gross_weight_g = sum((line.gross_weight_g for line in lines), ZERO)
        doc.total_fine_weight_g = sum((line.fine_weight_g for line in lines), ZERO)
        doc.money_amount = making if sale.in_metal else making + sum(
            (line.metal_amount for line in lines), ZERO)
        doc.number = allocate_number("TR", branch=sale.branch, fiscal_year=today.year)
        doc.journal_entry = post_entry(
            branch=sale.branch, business_date=today, kind=EntryKind.AUTO,
            lines=_ledger_lines(sale.in_metal, sale.trade_account, lines, sign=-1),
            source_type=RETURN_DOC, source_id=doc.pk,
            memo=_("Return %(number)s of wholesale %(sale)s")
            % {"number": doc.number, "sale": sale.number})
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.posted_by = getattr(actor, "user", None)
        doc.save()
    return doc


def void_trade_return(return_id: int, *, reason: str = "", actor=None) -> TradeReturn:
    """Undo a return: the goods count as sold again and its entry is reversed."""
    with transaction.atomic():
        doc = (TradeReturn.objects.select_for_update(of=("self",))
               .select_related("branch", "original_sale__branch").filter(pk=return_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.trade.return", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted returns can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        sale = doc.original_sale
        _goods_out(sale, sale.lines.filter(return_lines__trade_return=doc),
                   DocRef(RETURN_DOC, doc.pk), today)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled return %(number)s") % {"number": doc.number})
        doc.status = DocStatus.VOIDED
        doc.voided_at = timezone.now()
        doc.voided_by = getattr(actor, "user", None)
        doc.void_reason = reason.strip()[:300]
        doc.save()
    return doc
