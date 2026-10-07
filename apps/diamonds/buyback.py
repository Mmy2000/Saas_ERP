"""Buying old diamond jewellery and loose stones from customers (legacy GHD40 "شراء ماس كسر").

Gweb kept only the gold weight, the stones' weight and the price. Here each thing bought is a
barcoded piece in stock (a diamond piece, or a loose stone), with its stones, so it can be
sold again, or its stones taken out with the setter. Its price is split: the gold at today's
value (never more than the price), the stones at the rest.

    Dr gold inventory (fine g, the gold part)    Cr metal position (fine g)
    Dr metal position (money, the gold part)
    Dr diamonds and stones (the stones part)
                                                 Cr cash box / bank / the seller's account
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import ItemCategory, Karat, ProductFamily, Tracking
from apps.core.crypto import encrypt
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import GRAMS_PER_CARAT, MONEY, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import ItemStatus, MovementType
from apps.inventory.services import DocRef, change_item_status, create_item
from apps.ledger.models import EntryKind
from apps.ledger.services import (
    LineInput,
    account_for,
    functional_commodity,
    metal_commodity,
    post_entry,
    reverse_entry,
)
from apps.org.models import Branch
from apps.parties.domain import normalize_national_id
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import fine_gram_value
from apps.treasury.holders import ensure_cash_available, resolve_tender

from .models import BuyBack, BuyBackLine
from .services import FAMILIES, add_stones, require_enabled

DOC_TYPE = "diamonds.BuyBack"
PAYMENTS = ("cash", "bank_transfer", "account")
ZERO = Decimal(0)


@dataclass(frozen=True)
class PieceInput:
    category_id: int
    price: Decimal | str
    karat_id: int | None = None  # pieces with gold; loose stones have none
    gross_weight_g: Decimal | str | None = None  # pieces; loose stones: from the carats
    stones_carat: Decimal | str = "0"
    stones_count: int = 1
    stones_note: str = ""  # colour, clarity… as told; details can be completed on the piece
    label_price: Decimal | str | None = None


@dataclass(frozen=True)
class BuyBackInput:
    branch_id: int
    pieces: tuple[PieceInput, ...]
    payment: str
    seller_id: int | None = None
    seller_name: str = ""
    seller_phone: str = ""
    seller_id_number: str = ""
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    note: str = ""


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def _number(value, step, field: str, *, positive: bool = False) -> Decimal:
    try:
        number = quantize(value if value not in (None, "") else "0", step)
    except (TypeError, ValueError, InvalidOperation):
        raise _error(field, _("Enter a number.")) from None
    if number < 0 or (positive and number <= 0):
        raise _error(field, _("Must be greater than zero.") if positive
                     else _("Must not be negative."))
    return number


def gold_value(karat, gross, carats) -> Decimal:
    """Today's value of the gold in a piece (the stones' weight taken off)."""
    if karat is None:
        return ZERO
    metal = max(gross - quantize(carats * GRAMS_PER_CARAT, WEIGHT), ZERO)
    return round_money(fine_weight(metal, karat.fineness) * fine_gram_value(karat.metal.code))


def _priced(index: int, piece: PieceInput):
    field = f"pieces.{index}"
    category = ItemCategory.objects.filter(pk=piece.category_id, is_active=True,
                                           product_family__in=FAMILIES,
                                           tracking=Tracking.SERIALIZED).first()
    if category is None:
        raise _error(field, _("Choose a diamond or stone category."))
    loose = category.product_family == ProductFamily.STONE
    carats = _number(piece.stones_carat, Decimal("0.001"), field, positive=loose)
    karat = None
    if loose:
        gross = quantize(carats * GRAMS_PER_CARAT, WEIGHT)
    else:
        karat = Karat.objects.select_related("metal").filter(pk=piece.karat_id or 0,
                                                             is_active=True).first()
        if karat is None:
            raise _error(field, _("Choose the karat."))
        gross = _number(piece.gross_weight_g, WEIGHT, field, positive=True)
    price = _number(piece.price, MONEY, field, positive=True)
    label = (_number(piece.label_price, MONEY, field)
             if piece.label_price not in (None, "") else None)
    gold = min(gold_value(karat, gross, carats), price)
    return category, karat, gross, carats, price, gold, label


def quote(pieces: tuple[PieceInput, ...]) -> list[dict]:
    """The gold in each piece at today's value, to help agree a price. Never writes."""
    require_enabled()
    answers = []
    for index, piece in enumerate(pieces):
        try:
            category, karat, gross, carats, _price, _gold, _label = _priced(
                index, PieceInput(**{**piece.__dict__, "price": piece.price or "1"}))
        except ValidationError:
            answers.append({"gold_value": None})
            continue
        answers.append({"gold_value": gold_value(karat, gross, carats)})
    return answers


def buy_back(data: BuyBackInput, *, actor=None) -> BuyBack:
    require_enabled()
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("diamonds.buy_back", branch=branch)
        if not data.pieces:
            raise _error("pieces", _("Add what is being bought."))
        seller = None
        if data.seller_id:
            seller = Party.objects.filter(pk=data.seller_id, is_active=True,
                                          roles__role=PartyRoleType.CUSTOMER).first()
            if seller is None:
                raise _error("seller", _("Choose an active customer."))
        elif not data.seller_name.strip():
            raise _error("seller_name", _("Write down who is selling."))
        priced = [_priced(index, piece) for index, piece in enumerate(data.pieces)]
        total = sum((row[4] for row in priced), ZERO)
        gold_total = sum((row[5] for row in priced), ZERO)

        if data.payment not in PAYMENTS:
            raise _error("payment", _("Choose how it is paid."))
        home = functional_commodity()
        holder_fields, money_account, money_party = {}, None, None
        if data.payment == "account":
            if seller is None:
                raise _error("payment", _("Only a customer can be paid on account."))
            money_account, money_party = account_for("customers"), seller
        else:
            holder = resolve_tender(data.payment, branch, home.currency,
                                    cash_box_id=data.cash_box_id,
                                    bank_account_id=data.bank_account_id)
            if holder.cash_box is not None:
                ensure_cash_available(holder.cash_box, total, "payment")
            money_account = holder.account
            holder_fields = {"cash_box": holder.cash_box, "bank_account": holder.bank_account}

        today = timezone.localdate()
        id_number = normalize_national_id(data.seller_id_number) if data.seller_id_number else ""
        bought = BuyBack.objects.create(
            branch=branch, business_date=today, seller=seller,
            seller_name="" if seller else data.seller_name.strip()[:200],
            seller_phone="" if seller else data.seller_phone.strip()[:30],
            seller_id_encrypted=encrypt(id_number) if id_number else "",
            payment=data.payment, total_amount=total, gold_value=gold_total,
            note=data.note.strip(), created_by=getattr(actor, "user", None), **holder_fields)
        doc = DocRef(DOC_TYPE, bought.pk)
        fine_by_metal: dict[str, list[Decimal]] = {}
        for piece, (category, karat, gross, carats, price, gold, label) in zip(
                data.pieces, priced, strict=True):
            item = create_item(category=category, karat=karat, gross_weight_g=gross,
                               branch=branch, business_date=today, doc=doc,
                               stone_weight_ct=carats, cost_amount="0",
                               stone_cost_amount=price - gold, label_price=label, actor=actor)
            if carats:
                add_stones(item, [{"kind": "diamond", "count": max(int(piece.stones_count or 1), 1),
                                   "carat": str(carats), "note": piece.stones_note.strip()[:200]}])
            BuyBackLine.objects.create(buy_back=bought, item=item, price=price, gold_value=gold)
            if karat is not None and item.fine_weight_g:
                totals = fine_by_metal.setdefault(karat.metal.code, [ZERO, ZERO])
                totals[0] += item.fine_weight_g
                totals[1] += gold

        lines = []
        for metal_code, (fine, value) in sorted(fine_by_metal.items()):
            metal = metal_commodity(metal_code)
            lines += [
                LineInput(account=account_for("inventory_gold"), commodity=metal, quantity=fine,
                          functional_amount=value),
                LineInput(account=account_for("metal_position"), commodity=metal,
                          quantity=-fine, functional_amount=-value),
                LineInput(account=account_for("metal_position"), commodity=home, quantity=value),
            ]
        if total - gold_total:
            lines.append(LineInput(account=account_for("inventory_diamonds"), commodity=home,
                                   quantity=total - gold_total))
        lines.append(LineInput(account=money_account, commodity=home, quantity=-total,
                               party=money_party))
        bought.number = allocate_number("DB", branch=branch, fiscal_year=today.year)
        bought.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=DOC_TYPE,
            source_id=bought.pk, lines=lines,
            memo=_("Old diamonds bought %(number)s") % {"number": bought.number})
        bought.status = DocStatus.POSTED
        bought.posted_at = timezone.now()
        bought.posted_by = getattr(actor, "user", None)
        bought.save()
    return bought


def void_buy_back(buy_back_id: int, *, reason: str = "", actor=None) -> BuyBack:
    """Cancel while every piece bought is still in stock and untouched."""
    require_enabled()
    with transaction.atomic():
        bought = (BuyBack.objects.select_for_update(of=("self",)).select_related("branch")
                  .filter(pk=buy_back_id).first())
        if bought is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.buy_back", branch=bought.branch)
        if bought.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        doc = DocRef(DOC_TYPE, bought.pk)
        for line in bought.lines.select_related("item").order_by("item_id"):
            if line.item.status != ItemStatus.IN_STOCK or line.item.branch_id != bought.branch_id:
                raise DomainError(_("%(barcode)s has moved on since: this cannot be cancelled.")
                                  % {"barcode": line.item.barcode}, code="DIAMONDS_BUYBACK_LOCKED")
            change_item_status(line.item_id, to=ItemStatus.VOIDED,
                               allowed_from=(ItemStatus.IN_STOCK,),
                               movement_type=MovementType.PURCHASE_VOID, business_date=today,
                               doc=doc, branch=bought.branch)
        if bought.journal_entry_id:
            reverse_entry(bought.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": bought.number})
        bought.status = DocStatus.VOIDED
        bought.voided_at = timezone.now()
        bought.voided_by = getattr(actor, "user", None)
        bought.void_reason = reason.strip()[:300]
        bought.save()
    return bought
