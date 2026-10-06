"""Retail sale use cases: quote (for the screen), post, void (§7.7, §8.4, §16.1).

`quote_sale` and `post_sale` share one computation, so what the seller saw is what is posted;
posting recomputes it from the database and ignores anything the browser calculated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency, Karat
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import round_money, to_decimal
from apps.core.sequences import allocate_number
from apps.diamonds.services import is_diamond
from apps.diamonds.services import require_enabled as require_diamonds
from apps.inventory.models import Item, ItemStatus, LotBalance, MovementType, StockLot
from apps.inventory.services import (
    SCRAP_CATEGORY_CODE,
    DocRef,
    change_item_status,
    lot_for,
    move_lot,
    scrap_category,
)
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import (
    account_for,
    functional_commodity,
    metal_commodity,
    money_commodity,
    post_entry,
    reverse_entry,
)
from apps.org.models import Branch
from apps.parties.models import Party, PartyRoleType
from apps.pricing.engine import (
    ItemQuote,
    TradeInQuote,
    quote_bulk,
    quote_item,
    quote_label_priced,
    quote_trade_in,
)
from apps.pricing.selectors import current_price_board, fine_gram_value, functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force
from apps.treasury.holders import Holder, default_cash_box, resolve_tender

from .models import (
    PaymentKind,
    PaymentTerms,
    Reservation,
    SalesInvoice,
    SalesInvoiceLine,
    SalesPayment,
    SalesTradeIn,
)

DOC_TYPE = "sales.SalesInvoice"
DISCOUNT_LIMIT = "sales.discount.max_rate.gold"
DIAMOND_DISCOUNT_LIMIT = "diamonds.discount.max_rate"
PAYMENT_TOLERANCE = Decimal("0.01")
ZERO = Decimal(0)


@dataclass(frozen=True)
class SaleLineInput:
    """A piece (item_id or barcode) or a weight of bulk gold (category, karat, weight)."""

    item_id: int | None = None
    barcode: str = ""
    discount_rate: Decimal | str = "0"
    category_id: int | None = None
    karat_id: int | None = None
    gross_weight_g: Decimal | str | None = None
    qty: int = 0

    @property
    def is_bulk(self) -> bool:
        return not (self.item_id or self.barcode) and self.gross_weight_g not in (None, "")


@dataclass(frozen=True)
class TradeInInput:
    karat_id: int
    gross_weight_g: Decimal | str
    loss_weight_g: Decimal | str = "0"
    price_override: Decimal | str | None = None


@dataclass(frozen=True)
class PaymentInput:
    kind: str
    currency_code: str
    amount: Decimal | str
    reference: str = ""
    # Which box / bank account / terminal; defaults per treasury.holders.resolve_tender.
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    terminal_id: int | None = None


@dataclass(frozen=True)
class SaleInput:
    branch_id: int
    lines: tuple[SaleLineInput, ...]
    trade_ins: tuple[TradeInInput, ...] = ()
    payments: tuple[PaymentInput, ...] = ()
    payment_terms: str = PaymentTerms.CASH
    customer_id: int | None = None
    customer_name: str = ""
    customer_phone: str = ""
    note: str = ""
    reservation_id: int | None = None  # completing a reservation (apps.sales.reservations)


@dataclass
class PaymentQuote:
    kind: str
    currency: Currency
    amount: Decimal
    fx_rate: Decimal
    functional_amount: Decimal
    reference: str = ""
    source: PaymentInput | None = None  # what was entered, to resolve the holder on posting


@dataclass
class SaleQuote:
    branch: Branch
    board: object
    customer: Party | None
    lines: list[ItemQuote] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    trade_ins: list[tuple[Karat, TradeInQuote]] = field(default_factory=list)
    payments: list[PaymentQuote] = field(default_factory=list)
    subtotal: Decimal = ZERO
    discount: Decimal = ZERO
    total: Decimal = ZERO
    trade_in: Decimal = ZERO
    paid: Decimal = ZERO
    due: Decimal = ZERO  # total − trade-in: what the customer still has to pay
    change: Decimal = ZERO
    balance: Decimal = ZERO  # left on the customer's account (credit sales)
    remaining: Decimal = ZERO  # still to pay before a cash sale can be posted
    problems: list[str] = field(default_factory=list)
    reservation: object | None = None


def _line_error(group: str, index: int, message: str, code: str) -> ValidationError:
    return ValidationError(message, code=code, fields={group: {str(index): [message]}})


def _find_item(line: SaleLineInput, branch, reserved_ids=frozenset()) -> Item:
    items = Item.objects.select_related("category", "karat__metal", "branch")
    item = (items.filter(pk=line.item_id).first() if line.item_id
            else items.filter(barcode=line.barcode.strip()).first())
    if item is None:
        raise DomainError(_("No piece with barcode %(barcode)s.") % {"barcode": line.barcode},
                          code="SALES_ITEM_NOT_FOUND")
    # A reserved piece can only be sold by completing its own reservation.
    sellable = (item.status == ItemStatus.IN_STOCK
                or (item.status == ItemStatus.RESERVED and item.pk in reserved_ids))
    if not sellable or item.branch_id != branch.pk:
        raise DomainError(
            _("Piece %(barcode)s is not available here (%(status)s, %(branch)s).")
            % {"barcode": item.barcode, "status": item.get_status_display(),
               "branch": item.branch.name}, code="SALES_ITEM_UNAVAILABLE")
    return item


def _may_sell_diamonds(actor, branch) -> None:
    """Diamond pieces need the Diamonds feature for the client and the right to sell them."""
    require_diamonds()
    if actor is not None and not actor.can("diamonds.sell", branch):
        raise DomainError(_("You may not sell diamond pieces."), code="SALES_DIAMONDS_DENIED")


def _find_lot(line: SaleLineInput, branch):
    """The branch's bulk stock of a category and karat (scrap is sold from its own screen)."""
    lot = (StockLot.objects.select_related("category", "karat__metal")
           .filter(category_id=line.category_id, karat_id=line.karat_id, branch=branch)
           .exclude(category__code=SCRAP_CATEGORY_CODE).first())
    balance = LotBalance.objects.filter(lot=lot).first() if lot else None
    if lot is None or balance is None or balance.gross_weight_g <= 0:
        raise DomainError(_("No gold of this kind in stock here."), code="SALES_BULK_NONE")
    return lot, balance


def quote_sale(data: SaleInput, *, actor=None) -> SaleQuote:
    """Price everything and check the payment. Never writes."""
    branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
    if branch is None:
        raise ValidationError(_("Unknown branch."), fields={"branch": [_("Unknown branch.")]})
    board = current_price_board()
    reservation = None
    if data.reservation_id:
        reservation = (Reservation.objects.select_related("price_board", "customer")
                       .filter(pk=data.reservation_id, branch=branch, status=DocStatus.POSTED,
                               sale__isnull=True).first())
        if reservation is None:
            raise DomainError(_("This reservation is not open."), code="SALES_RESERVATION_CLOSED")
        if reservation.price_locked:
            board = reservation.price_board
    if board is None:
        raise DomainError(_("No gold price has been published yet."), code="PRICING_NO_BOARD")
    reserved_ids = (frozenset(reservation.lines.values_list("item_id", flat=True))
                    if reservation else frozenset())

    customer = None
    if data.customer_id:
        customer = Party.objects.filter(pk=data.customer_id, is_active=True,
                                        roles__role=PartyRoleType.CUSTOMER).first()
        if customer is None:
            raise ValidationError(_("Unknown customer."),
                                  fields={"customer": [_("Unknown customer.")]})

    home = functional_currency()
    max_discount = actor.limit(DISCOUNT_LIMIT) if actor is not None else Decimal(1)
    diamond_discount = (actor.limit(DIAMOND_DISCOUNT_LIMIT) if actor is not None
                        else Decimal(1))
    if reservation is not None and (customer is None or customer.pk != reservation.customer_id):
        message = _("A reservation is sold to its own customer.")
        raise ValidationError(message, fields={"customer": [message]})
    quote = SaleQuote(branch=branch, board=board, customer=customer, reservation=reservation)

    seen = set()
    taken: dict[int, list] = defaultdict(lambda: [ZERO, 0])  # per lot: weight, pieces
    for index, line in enumerate(data.lines):
        try:
            if line.is_bulk:
                source, balance = _find_lot(line, branch)
                priced = quote_bulk(source, balance, board, gross_weight_g=line.gross_weight_g,
                                    qty=line.qty, discount_rate=line.discount_rate,
                                    max_discount=max_discount, functional_currency=home)
                used = taken[source.pk]
                used[0] += priced.gross_weight_g
                used[1] += line.qty
                if used[0] > balance.gross_weight_g or used[1] > balance.qty:
                    raise DomainError(
                        _("Only %(weight)s g (%(qty)s pieces) of %(name)s here.")
                        % {"weight": balance.gross_weight_g, "qty": balance.qty,
                           "name": source.category.name}, code="SALES_BULK_SHORT")
            else:
                source = _find_item(line, branch, reserved_ids)
                if source.pk in seen:
                    raise DomainError(_("Piece %(barcode)s is already on this sale.")
                                      % {"barcode": source.barcode}, code="SALES_DUPLICATE_ITEM")
                seen.add(source.pk)
                if is_diamond(source.category):
                    _may_sell_diamonds(actor, branch)
                    priced = quote_label_priced(source, board, discount_rate=line.discount_rate,
                                                max_discount=diamond_discount)
                else:
                    priced = quote_item(source, board, discount_rate=line.discount_rate,
                                        max_discount=max_discount, functional_currency=home)
        except (DomainError, ValidationError) as exc:
            raise _line_error("lines", index, exc.message, exc.code) from exc
        quote.items.append(source)
        quote.lines.append(priced)

    may_override = actor is None or actor.can("sales.trade_in.override_price", branch)
    karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(
        pk__in=[t.karat_id for t in data.trade_ins], is_active=True)}
    for index, trade in enumerate(data.trade_ins):
        karat = karats.get(trade.karat_id)
        try:
            if karat is None:
                raise ValidationError(_("Unknown karat."), code="SALES_TRADE_IN_KARAT")
            priced = quote_trade_in(karat, board, gross_weight_g=trade.gross_weight_g,
                                    loss_weight_g=trade.loss_weight_g,
                                    price_override=trade.price_override,
                                    may_override=may_override)
        except (DomainError, ValidationError) as exc:
            raise _line_error("trade_ins", index, exc.message, exc.code) from exc
        quote.trade_ins.append((karat, priced))

    currencies = {c.code: c for c in Currency.objects.filter(is_active=True)}
    for index, payment in enumerate(data.payments):
        currency = currencies.get(payment.currency_code)
        if payment.kind not in PaymentKind.values or currency is None:
            raise _line_error("payments", index, _("Unknown payment method."), "SALES_PAYMENT")
        amount = round_money(to_decimal(payment.amount))
        if amount <= 0:
            continue
        if payment.kind == PaymentKind.DEPOSIT and (
                reservation is None or currency.code != home
                or amount > reservation.deposit_amount):
            message = _("Deposits are used only by completing their reservation.")
            raise _line_error("payments", index, message, "SALES_PAYMENT")
        fx = Decimal(1) if currency.code == home else rate_in_force(currency.code)
        quote.payments.append(PaymentQuote(
            kind=payment.kind, currency=currency, amount=amount, fx_rate=fx,
            functional_amount=round_money(amount * fx), reference=payment.reference[:60],
            source=payment))

    quote.total = sum((line.line_total for line in quote.lines), ZERO)
    quote.discount = sum((line.discount_amount for line in quote.lines), ZERO)
    quote.subtotal = quote.total + quote.discount
    quote.trade_in = sum((t.amount for _k, t in quote.trade_ins), ZERO)
    quote.paid = sum((p.functional_amount for p in quote.payments), ZERO)
    quote.due = quote.total - quote.trade_in
    cash_in = sum((p.functional_amount for p in quote.payments if p.kind == PaymentKind.CASH),
                  ZERO)
    difference = quote.paid - quote.due

    if data.payment_terms == PaymentTerms.CREDIT:
        if customer is None:
            quote.problems.append("SALES_CUSTOMER_REQUIRED")
        if difference > PAYMENT_TOLERANCE:
            quote.problems.append("SALES_OVERPAID")
        quote.balance = max(-difference, ZERO)
    else:
        if difference < -PAYMENT_TOLERANCE:
            quote.remaining = -difference
            quote.problems.append("SALES_NOT_FULLY_PAID")
        elif difference > 0:
            quote.change = difference
            # Change is handed back in cash: only cash (or a trade-in worth more than the
            # goods) can produce it, never a card or transfer overpayment.
            if difference > cash_in + max(-quote.due, ZERO):
                quote.problems.append("SALES_OVERPAID")
    if not quote.lines:
        quote.problems.append("SALES_EMPTY")
    return quote


PROBLEM_MESSAGES = {
    "SALES_CUSTOMER_REQUIRED": lambda: _("A sale on account needs a customer."),
    "SALES_OVERPAID": lambda: _("Payments are more than the amount due."),
    "SALES_NOT_FULLY_PAID": lambda: _("A cash sale must be fully paid."),
    "SALES_EMPTY": lambda: _("Add at least one piece."),
}


def _holders(quote: SaleQuote) -> list[Holder]:
    """The box / bank account / terminal of each payment (may open the branch's cash box)."""
    holders = []
    for index, payment in enumerate(quote.payments):
        if payment.kind == PaymentKind.DEPOSIT:  # held on the customer deposits account
            holders.append(Holder())
            continue
        entered = payment.source or PaymentInput(kind=payment.kind, currency_code="", amount=0)
        holders.append(resolve_tender(
            payment.kind, quote.branch, payment.currency, cash_box_id=entered.cash_box_id,
            bank_account_id=entered.bank_account_id, terminal_id=entered.terminal_id,
            field_prefix=f"payments.{index}."))
    return holders


def _ledger_lines(invoice: SalesInvoice, quote: SaleQuote, lines, trade_ins,
                  holders: list[Holder]) -> list[LedgerLine]:
    home = functional_commodity()
    result: list[LedgerLine] = []

    def add(role, commodity, quantity, functional=None, party=None, account=None):
        if quantity:
            result.append(LedgerLine(account=account or account_for(role), commodity=commodity,
                                     quantity=quantity, functional_amount=functional,
                                     party=party))

    # Money received (change is given back from company-currency cash).
    change_left = quote.change
    change_box = None
    for payment, holder in zip(quote.payments, holders, strict=True):
        commodity = money_commodity(payment.currency)
        amount, functional = payment.amount, payment.functional_amount
        if payment.kind == PaymentKind.DEPOSIT:
            add("customer_deposits", home, amount, party=quote.customer)
            continue
        if payment.kind == PaymentKind.CASH and commodity.is_functional:
            change_box = change_box or holder.cash_box
            if change_left:
                taken = min(change_left, amount)
                amount, functional = amount - taken, functional - taken
                change_left -= taken
        add(None, commodity, amount, None if commodity.is_functional else functional,
            account=holder.account)
    if change_left:  # change beyond company-currency cash tendered (e.g. big trade-in)
        box = change_box or default_cash_box(quote.branch, home.currency)
        add(None, home, -change_left, account=box.account)
    if quote.balance:
        add("customers", home, quote.balance, party=quote.customer)

    # Scrap received: metal in, against the metal position; its value pays for the goods.
    by_metal: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for trade in trade_ins:
        by_metal[trade.karat.metal.code][0] += trade.fine_weight_g
        by_metal[trade.karat.metal.code][1] += trade.amount
    for metal_code, (fine, value) in sorted(by_metal.items()):
        commodity = metal_commodity(metal_code)
        add("inventory_scrap", commodity, fine, value)
        add("metal_position", commodity, -fine, -value)
        add("metal_position", home, value)

    # Revenue, split into gold, making charges and (diamond pieces) stones.
    add("sales_gold", home, -sum((line.metal_amount for line in lines), ZERO))
    add("sales_making", home, -sum((line.making_amount for line in lines), ZERO))
    add("sales_diamonds", home, -sum((line.stones_amount for line in lines), ZERO))

    # Cost of goods sold: the fine grams leave inventory, plus the making cost paid.
    sold: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in lines:
        if line.karat is None:  # a loose stone: no gold
            continue
        sold[line.karat.metal.code][0] += line.fine_weight_g
        sold[line.karat.metal.code][1] += line.metal_value
    for metal_code, (fine, value) in sorted(sold.items()):
        commodity = metal_commodity(metal_code)
        add("cogs_gold", commodity, fine, value)
        add("inventory_gold", commodity, -fine, -value)
    making_cost = sum((line.cost_amount for line in lines), ZERO)
    add("cogs_gold", home, making_cost)
    add("inventory_gold", home, -making_cost)
    stones = sum((line.stone_cost_amount for line in lines), ZERO)
    add("cogs_diamonds", home, stones)
    add("inventory_diamonds", home, -stones)
    return result


def post_sale(data: SaleInput, *, actor=None) -> SalesInvoice:
    with transaction.atomic():
        quote = quote_sale(data, actor=actor)
        if actor is not None:
            actor.require("sales.invoice.create", branch=quote.branch)
        if quote.problems:
            code = quote.problems[0]
            raise DomainError(PROBLEM_MESSAGES[code](), code=code)

        today = timezone.localdate()
        customer = quote.customer
        invoice = SalesInvoice.objects.create(
            branch=quote.branch, business_date=today, customer=customer,
            customer_name=(customer.name if customer else data.customer_name.strip())[:200],
            customer_phone=(customer.phone if customer else data.customer_phone.strip())[:30],
            payment_terms=data.payment_terms, price_board=quote.board,
            sold_by=getattr(actor, "user", None), note=data.note.strip(),
            subtotal_amount=quote.subtotal, discount_amount=quote.discount,
            total_amount=quote.total, trade_in_amount=quote.trade_in, paid_amount=quote.paid,
            change_amount=quote.change, balance_amount=quote.balance,
            created_by=getattr(actor, "user", None),
        )
        doc = DocRef(DOC_TYPE, invoice.pk)

        lines = []
        # Lock and move pieces, then lots, in id order (stable lock ordering, §16.2).
        for position, (source, priced) in sorted(
                enumerate(zip(quote.items, quote.lines, strict=True)),
                key=lambda pair: (isinstance(pair[1][0], StockLot), pair[1][0].pk)):
            if isinstance(source, StockLot):
                movement = move_lot(source, qty=-priced.qty,
                                    gross_weight_g=-priced.gross_weight_g,
                                    cost_amount=-priced.cost_amount,
                                    movement_type=MovementType.SALE, business_date=today, doc=doc)
                fine = -movement.fine_weight_g
                where = {"lot": source, "qty": priced.qty}
            else:
                change_item_status(source.pk, to=ItemStatus.SOLD,
                                   allowed_from=(ItemStatus.IN_STOCK, ItemStatus.RESERVED),
                                   movement_type=MovementType.SALE, business_date=today,
                                   doc=doc, branch=quote.branch)
                fine = priced.fine_weight_g
                where = {"item": source, "qty": 1}
            value = (round_money(fine * fine_gram_value(source.karat.metal.code))
                     if source.karat else ZERO)
            lines.append(SalesInvoiceLine(
                invoice=invoice, position=position, karat=source.karat,
                category=source.category, **where,
                gross_weight_g=priced.gross_weight_g, fine_weight_g=fine,
                metal_price_per_g=priced.metal_price_per_g, making_rate=priced.making_rate,
                discount_rate=priced.discount_rate, making_rate_net=priced.making_rate_net,
                metal_amount=priced.metal_amount, making_amount=priced.making_amount,
                discount_amount=priced.discount_amount, line_total=priced.line_total,
                cost_amount=priced.cost_amount, metal_value=value,
                stones_amount=priced.stones_amount, stone_cost_amount=priced.stone_cost,
            ))
        lines.sort(key=lambda line: line.position)
        SalesInvoiceLine.objects.bulk_create(lines)

        trade_ins = []
        for karat, priced in quote.trade_ins:
            trade_ins.append(SalesTradeIn.objects.create(
                invoice=invoice, karat=karat, gross_weight_g=priced.gross_weight_g,
                loss_weight_g=priced.loss_weight_g, net_weight_g=priced.net_weight_g,
                fine_weight_g=priced.fine_weight_g, price_per_g=priced.price_per_g,
                amount=priced.amount, price_overridden=priced.price_overridden))
            move_lot(lot_for(scrap_category(), karat, quote.branch), qty=0,
                     gross_weight_g=priced.net_weight_g, cost_amount=priced.amount,
                     movement_type=MovementType.SCRAP_IN, business_date=today, doc=doc)

        holders = _holders(quote)
        SalesPayment.objects.bulk_create([
            SalesPayment(invoice=invoice, kind=p.kind, currency=p.currency, amount=p.amount,
                         fx_rate=p.fx_rate, functional_amount=p.functional_amount,
                         reference=p.reference, **holder.fields)
            for p, holder in zip(quote.payments, holders, strict=True)
        ])

        invoice.number = allocate_number("SI", branch=quote.branch, fiscal_year=today.year)
        invoice.journal_entry = post_entry(
            branch=quote.branch, business_date=today,
            lines=_ledger_lines(invoice, quote, lines, trade_ins, holders), kind=EntryKind.AUTO,
            source_type=DOC_TYPE, source_id=invoice.pk, absorb_rounding=True,
            memo=_("Sale %(number)s") % {"number": invoice.number},
        )
        invoice.status = DocStatus.POSTED
        invoice.posted_at = timezone.now()
        invoice.posted_by = getattr(actor, "user", None)
        invoice.save()
    return invoice


def void_sale(invoice_id: int, *, reason: str = "", actor=None) -> SalesInvoice:
    """Cancel a posted sale: pieces back in stock, scrap back out, ledger reversed. Same-day
    cancellations need sales.invoice.void; older ones sales.invoice.void_any (§8.7)."""
    with transaction.atomic():
        invoice = (SalesInvoice.objects.select_for_update().select_related("branch")
                   .filter(pk=invoice_id).first())
        if invoice is None:
            raise NotFound(_("Not found."))
        today = timezone.localdate()
        if actor is not None:
            permission = ("sales.invoice.void" if invoice.business_date == today
                          else "sales.invoice.void_any")
            actor.require(permission, branch=invoice.branch)
        if invoice.status != DocStatus.POSTED:
            raise DomainError(_("Only posted sales can be cancelled."), code="DOC_NOT_POSTED")
        if invoice.returns.filter(status=DocStatus.POSTED).exists():
            raise DomainError(_("Cancel the returns of this sale first."),
                              code="SALES_HAS_RETURNS")
        doc = DocRef(DOC_TYPE, invoice.pk)
        for line in invoice.lines.select_related("lot__karat").order_by("item_id", "lot_id"):
            if line.lot_id:
                move_lot(line.lot, qty=line.qty, gross_weight_g=line.gross_weight_g,
                         cost_amount=line.cost_amount, movement_type=MovementType.SALE_RETURN,
                         business_date=today, doc=doc)
                continue
            change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                               allowed_from=(ItemStatus.SOLD,),
                               movement_type=MovementType.SALE_RETURN, business_date=today,
                               doc=doc, branch=invoice.branch, stock_delta=1)
        for trade in invoice.trade_ins.select_related("karat"):
            move_lot(lot_for(scrap_category(), trade.karat, invoice.branch), qty=0,
                     gross_weight_g=-trade.net_weight_g, cost_amount=-trade.amount,
                     movement_type=MovementType.SCRAP_OUT, business_date=today, doc=doc)
        if invoice.journal_entry_id:
            reverse_entry(invoice.journal_entry_id, business_date=today,
                          memo=_("Cancelled sale %(number)s") % {"number": invoice.number})
        invoice.status = DocStatus.VOIDED
        invoice.voided_at = timezone.now()
        invoice.voided_by = getattr(actor, "user", None)
        invoice.void_reason = reason.strip()[:300]
        invoice.save()
    return invoice

