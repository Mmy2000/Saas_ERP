"""Scrap gold bought and sold for money (§7.8).

buy   scrap in per karat (net of loss) at the scrap price, as in a trade-in:
        Dr scrap stock (fine g, at the price paid)   Cr metal position (fine g)
        Dr metal position (money)                    Cr cash box / bank / the seller's account
sell  scrap out of the branch's stock at an agreed price per gram:
        Dr cash box / bank / the buyer's account     Cr scrap stock (fine g, at what it cost)
        Dr metal position (fine g)                   Cr metal position (money, at cost)
        and the difference to the cost               Cr metal gains  or  Dr metal losses
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Karat
from apps.core.crypto import encrypt
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, UNIT_PRICE, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import LotBalance, MovementType, StockLot
from apps.inventory.services import DocRef, lot_for, move_lot, scrap_category
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
from apps.parties.domain import normalize_national_id
from apps.parties.models import Party, PartyRoleType
from apps.pricing.engine import quote_trade_in
from apps.pricing.selectors import current_price_board
from apps.treasury.holders import ensure_cash_available, resolve_tender

from .models import (
    ScrapPayment,
    ScrapPurchase,
    ScrapPurchaseLine,
    ScrapSale,
    ScrapSaleLine,
)

BUY_DOC, SELL_DOC = "purchasing.ScrapPurchase", "purchasing.ScrapSale"
ZERO = Decimal(0)


@dataclass(frozen=True)
class ScrapLineInput:
    karat_id: int
    gross_weight_g: Decimal | str
    loss_weight_g: Decimal | str = "0"
    price: Decimal | str | None = None  # buying: an override; selling: required, per gram


@dataclass(frozen=True)
class ScrapInput:
    branch_id: int
    lines: tuple[ScrapLineInput, ...]
    payment: str
    party_id: int | None = None  # seller (optional when buying) / buyer (required when selling)
    seller_name: str = ""
    seller_phone: str = ""
    seller_id_number: str = ""
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    note: str = ""


def _field_error(field: str, message: str) -> ValidationError:
    return ValidationError(message, fields={field: [message]})


def _line_error(index: int, message: str) -> ValidationError:
    return ValidationError(message, fields={"lines": {str(index): [message]}})


def _branch(pk) -> Branch:
    branch = Branch.objects.filter(pk=pk, is_active=True).first() if pk else None
    if branch is None:
        raise _field_error("branch", _("Unknown branch."))
    return branch


def _party(pk, *, required: bool) -> Party | None:
    if not pk:
        if required:
            raise _field_error("party", _("Choose who it is."))
        return None
    party = Party.objects.filter(pk=pk, is_active=True).first()
    if party is None:
        raise _field_error("party", _("Choose an active customer or supplier."))
    return party


def _party_account(party: Party):
    """A party's money account: customers if they are a customer, else suppliers."""
    is_customer = party.roles.filter(role=PartyRoleType.CUSTOMER).exists()
    return account_for("customers" if is_customer else "suppliers")


def _money_side(data: ScrapInput, branch, party, amount: Decimal, *, paying: bool):
    """The money line's account and party, and the box / bank account used."""
    if data.payment not in ScrapPayment.values:
        raise _field_error("payment", _("Choose how it is paid."))
    if data.payment == ScrapPayment.ACCOUNT:
        if party is None:
            raise _field_error("party", _("Only a customer or supplier can be paid on account."))
        return _party_account(party), party, {}
    home = functional_commodity().currency
    holder = resolve_tender(data.payment, branch, home, cash_box_id=data.cash_box_id,
                            bank_account_id=data.bank_account_id)
    if paying and holder.cash_box is not None:
        ensure_cash_available(holder.cash_box, amount, "payment")
    return holder.account, None, {"cash_box": holder.cash_box,
                                  "bank_account": holder.bank_account}


def _metal_lines(per_metal, *, sign: int, value_key: str) -> list[LedgerLine]:
    """Scrap stock in (sign 1) or out (-1) against the metal position, per metal."""
    home = functional_commodity()
    lines = []
    for metal_code, totals in sorted(per_metal.items()):
        fine, value = totals["fine"], totals[value_key]
        metal = metal_commodity(metal_code)
        lines += [
            LedgerLine(account=account_for("inventory_scrap"), commodity=metal,
                       quantity=sign * fine, functional_amount=sign * value),
            LedgerLine(account=account_for("metal_position"), commodity=metal,
                       quantity=-sign * fine, functional_amount=-sign * value),
            LedgerLine(account=account_for("metal_position"), commodity=home,
                       quantity=sign * value),
        ]
    return lines


# --- buying -------------------------------------------------------------------------------------

def quote_scrap_purchase(data: ScrapInput, *, actor=None):
    """Price the lines on today's board. Never writes."""
    board = current_price_board()
    if board is None:
        raise DomainError(_("No gold price has been published yet."), code="PRICING_NO_BOARD")
    may_override = actor is None or actor.can("purchasing.scrap.override_price")
    karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(
        pk__in=[line.karat_id for line in data.lines], is_active=True)}
    quotes = []
    for index, line in enumerate(data.lines):
        karat = karats.get(line.karat_id)
        if karat is None:
            raise _line_error(index, _("Unknown karat."))
        try:
            quotes.append((karat, quote_trade_in(
                karat, board, gross_weight_g=line.gross_weight_g,
                loss_weight_g=line.loss_weight_g, price_override=line.price,
                may_override=may_override)))
        except (DomainError, ValidationError) as exc:
            raise _line_error(index, exc.message) from exc
    return board, quotes


def buy_scrap(data: ScrapInput, *, actor=None) -> ScrapPurchase:
    with transaction.atomic():
        branch = _branch(data.branch_id)
        if actor is not None:
            actor.require("purchasing.scrap.buy", branch=branch)
        if not data.lines:
            raise _field_error("lines", _("Add at least one line."))
        seller = _party(data.party_id, required=False)
        if seller is None and not data.seller_name.strip():
            raise _field_error("seller_name", _("Write down who is selling."))
        board, quotes = quote_scrap_purchase(data, actor=actor)
        total = sum((quote.amount for _k, quote in quotes), ZERO)
        account, party, holder = _money_side(data, branch, seller, total, paying=True)

        today = timezone.localdate()
        id_number = normalize_national_id(data.seller_id_number) if data.seller_id_number else ""
        purchase = ScrapPurchase.objects.create(
            branch=branch, business_date=today, seller=seller,
            seller_name=(seller.name if seller else data.seller_name.strip())[:200],
            seller_phone=data.seller_phone.strip()[:30],
            seller_id_encrypted=encrypt(id_number) if id_number else "",
            price_board=board, payment=data.payment, note=data.note.strip(),
            total_amount=total,
            total_gross_weight_g=sum((q.gross_weight_g for _k, q in quotes), ZERO),
            total_fine_weight_g=sum((q.fine_weight_g for _k, q in quotes), ZERO),
            created_by=getattr(actor, "user", None), **holder)
        doc = DocRef(BUY_DOC, purchase.pk)
        per_metal = defaultdict(lambda: {"fine": ZERO, "value": ZERO})
        for karat, quote in quotes:
            ScrapPurchaseLine.objects.create(
                purchase=purchase, karat=karat, gross_weight_g=quote.gross_weight_g,
                loss_weight_g=quote.loss_weight_g, net_weight_g=quote.net_weight_g,
                fine_weight_g=quote.fine_weight_g, price_per_g=quote.price_per_g,
                price_overridden=quote.price_overridden, amount=quote.amount)
            move_lot(lot_for(scrap_category(), karat, branch), qty=0,
                     gross_weight_g=quote.net_weight_g, cost_amount=quote.amount,
                     movement_type=MovementType.SCRAP_IN, business_date=today, doc=doc)
            per_metal[karat.metal.code]["fine"] += quote.fine_weight_g
            per_metal[karat.metal.code]["value"] += quote.amount

        purchase.number = allocate_number("SB", branch=branch, fiscal_year=today.year)
        purchase.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=BUY_DOC,
            source_id=purchase.pk,
            memo=_("Scrap purchase %(number)s") % {"number": purchase.number},
            lines=[*_metal_lines(per_metal, sign=1, value_key="value"),
                   LedgerLine(account=account, commodity=functional_commodity(), quantity=-total,
                              party=party)])
        _post(purchase, actor)
    return purchase


# --- selling ------------------------------------------------------------------------------------

def sell_scrap(data: ScrapInput, *, actor=None) -> ScrapSale:
    with transaction.atomic():
        branch = _branch(data.branch_id)
        if actor is not None:
            actor.require("purchasing.scrap.sell", branch=branch)
        if not data.lines:
            raise _field_error("lines", _("Add at least one line."))
        buyer = _party(data.party_id, required=True)
        karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(
            pk__in=[line.karat_id for line in data.lines])}
        category = scrap_category()
        today = timezone.localdate()
        sale = ScrapSale(branch=branch, business_date=today, buyer=buyer, payment=data.payment,
                         note=data.note.strip(), created_by=getattr(actor, "user", None))
        priced = []
        for index, line in enumerate(data.lines):
            karat = karats.get(line.karat_id)
            lot = StockLot.objects.filter(category=category, karat=karat, branch=branch).first() \
                if karat else None
            balance = LotBalance.objects.filter(lot=lot).first() if lot else None
            try:
                gross = quantize(line.gross_weight_g, WEIGHT)
                price = quantize(line.price, UNIT_PRICE)
            except (TypeError, ValueError):
                raise _line_error(index, _("Enter a number.")) from None
            if gross <= 0 or price <= 0:
                raise _line_error(index, _("Must be greater than zero."))
            if balance is None or gross > balance.gross_weight_g:
                available = balance.gross_weight_g if balance else ZERO
                raise _line_error(index, _("Only %(weight)s g of this scrap at the branch.")
                                  % {"weight": available})
            cost = quantize(balance.cost_amount * gross / balance.gross_weight_g, MONEY)
            priced.append((karat, lot, gross, price, round_money(gross * price), cost))
        total = sum((p[4] for p in priced), ZERO)
        account, party, holder = _money_side(data, branch, buyer, total, paying=False)
        for name, value in holder.items():
            setattr(sale, name, value)
        sale.save()

        doc = DocRef(SELL_DOC, sale.pk)
        per_metal = defaultdict(lambda: {"fine": ZERO, "cost": ZERO})
        for karat, lot, gross, price, amount, cost in priced:
            movement = move_lot(lot, qty=0, gross_weight_g=-gross, cost_amount=-cost,
                                movement_type=MovementType.SCRAP_OUT, business_date=today,
                                doc=doc)
            fine = -movement.fine_weight_g
            ScrapSaleLine.objects.create(sale=sale, karat=karat, gross_weight_g=gross,
                                         fine_weight_g=fine, price_per_g=price, amount=amount,
                                         cost_amount=cost)
            per_metal[karat.metal.code]["fine"] += fine
            per_metal[karat.metal.code]["cost"] += cost
        sale.total_amount = total
        sale.total_cost = sum((p[5] for p in priced), ZERO)
        sale.total_gross_weight_g = sum((p[2] for p in priced), ZERO)
        sale.total_fine_weight_g = sum((m["fine"] for m in per_metal.values()), ZERO)

        home = functional_commodity()
        lines = [LedgerLine(account=account, commodity=home, quantity=total, party=party),
                 *_metal_lines(per_metal, sign=-1, value_key="cost")]
        if sale.gain:
            lines.append(LedgerLine(account=account_for("metal_gain" if sale.gain > 0
                                                        else "metal_loss"),
                                    commodity=home, quantity=-sale.gain))
        sale.number = allocate_number("SS", branch=branch, fiscal_year=today.year)
        sale.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=SELL_DOC,
            source_id=sale.pk, memo=_("Scrap sale %(number)s") % {"number": sale.number},
            lines=lines)
        _post(sale, actor)
    return sale


# --- common -------------------------------------------------------------------------------------

def _post(doc, actor) -> None:
    doc.status = DocStatus.POSTED
    doc.posted_at = timezone.now()
    doc.posted_by = getattr(actor, "user", None)
    doc.save()


def void_scrap(model, doc_id: int, *, reason: str = "", actor=None):
    """Cancel a scrap purchase (the scrap leaves stock again) or sale (it comes back)."""
    with transaction.atomic():
        doc = (model.objects.select_for_update(of=("self",)).select_related("branch")
               .filter(pk=doc_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("purchasing.scrap.void", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        buying = model is ScrapPurchase
        ref = DocRef(BUY_DOC if buying else SELL_DOC, doc.pk)
        for line in doc.lines.select_related("karat").order_by("id"):
            weight = line.net_weight_g if buying else line.gross_weight_g
            cost = line.amount if buying else line.cost_amount
            sign = -1 if buying else 1
            move_lot(lot_for(scrap_category(), line.karat, doc.branch), qty=0,
                     gross_weight_g=sign * weight, cost_amount=sign * cost,
                     movement_type=MovementType.SCRAP_OUT if buying else MovementType.SCRAP_IN,
                     business_date=today, doc=ref)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": doc.number})
        doc.status = DocStatus.VOIDED
        doc.voided_at = timezone.now()
        doc.voided_by = getattr(actor, "user", None)
        doc.void_reason = reason.strip()[:300]
        doc.save()
    return doc
