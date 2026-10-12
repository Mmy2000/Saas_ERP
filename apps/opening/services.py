"""Posting and cancelling opening balances (see models.py).

Every balance goes against the Opening balances equity account ("opening_equity"):

    a customer who owes us 5,000      Dr Customers (the customer)   Cr Opening balances
    a supplier we owe 100 g of 21K    Dr Opening balances (87.5 fine g)   Cr Suppliers (party)
    a cash box holding 20,000         Dr the box                    Cr Opening balances
    Maadi owes this branch 3,000      Dr Branch clearing (here)     Cr Opening balances (here)
                                      Dr Opening balances (Maadi)   Cr Branch clearing (Maadi)
    bulk stock and pieces             Dr Gold inventory / Scrap (fine g at today's board value,
                                         plus the making cost)       Cr Opening balances

Gold balances are kept in fine grams and valued at the gold board in force, like every other
gold movement; money in another currency at the exchange rate in force.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import Currency, ItemCategory, Karat, Tracking
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import Item, ItemStatus, MovementType
from apps.inventory.services import DocRef, change_item_status, create_item, lot_for, move_lot
from apps.inventory.valuation import StockPart, metal_value, stock_lines
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import (
    account_for,
    metal_commodity,
    money_commodity,
    post_entry,
    reverse_entry,
)
from apps.org.models import Branch
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import fx_rate

from .models import LineKind, OpeningBalance, OpeningLine

DOC_TYPE = "opening.OpeningBalance"
ZERO = Decimal(0)
MAX_LINES = 5000
KARAT_FAMILIES = ("gold", "silver", "bullion_coin")  # weighed by karat

# The ledger account of each kind of party balance.
ROLE_ACCOUNTS = {
    PartyRoleType.CUSTOMER: "customers",
    PartyRoleType.SUPPLIER: "suppliers",
    PartyRoleType.TRADE_ACCOUNT: "trade_accounts",
    PartyRoleType.WORKSHOP: "workshops",
    PartyRoleType.PARTNER: "partners_capital",
}


@dataclass(frozen=True)
class PartyBalance:
    role: str
    party_id: int
    amount: Decimal | str  # money in `currency_code`, or grams of `karat_id`
    owes_us: bool  # True: the party owes the business; False: the business owes them
    currency_code: str = ""
    karat_id: int | None = None


@dataclass(frozen=True)
class HolderBalance:
    kind: str  # box | bank
    holder_id: int
    amount: Decimal | str  # in the holder's currency; may be negative (an overdrawn bank)


@dataclass(frozen=True)
class BranchBalance:
    branch_id: int  # the other branch
    amount: Decimal | str  # money in `currency_code`, or grams of `karat_id`
    owes_us: bool  # True: the other branch owes this one
    currency_code: str = ""
    karat_id: int | None = None


@dataclass(frozen=True)
class StockBalance:
    category_id: int
    karat_id: int | None
    gross_weight_g: Decimal | str
    qty: int = 0
    cost_amount: Decimal | str = "0"  # making cost carried (company currency)


@dataclass(frozen=True)
class PieceBalance:
    category_id: int
    karat_id: int | None
    gross_weight_g: Decimal | str
    barcode: str = ""  # keep the old code so printed labels still scan; blank = number a new one
    stone_weight_ct: Decimal | str = "0"
    cost_amount: Decimal | str = "0"  # making cost paid
    list_making_rate: Decimal | str = "0"
    stone_cost_amount: Decimal | str = "0"
    label_price: Decimal | str | None = None
    supplier_id: int | None = None
    external_code: str = ""


@dataclass(frozen=True)
class OpeningInput:
    branch_id: int
    business_date: date | None = None  # the cut-over day; default today
    note: str = ""
    parties: list[PartyBalance] = field(default_factory=list)
    holders: list[HolderBalance] = field(default_factory=list)
    branches: list[BranchBalance] = field(default_factory=list)
    stock: list[StockBalance] = field(default_factory=list)
    pieces: list[PieceBalance] = field(default_factory=list)


class _Errors:
    """Field errors keyed like the form: parties.3.amount, pieces.12.barcode…"""

    def __init__(self):
        self.fields: dict[str, list[str]] = {}

    def add(self, key: str, message: str) -> None:
        self.fields.setdefault(key, []).append(str(message))

    def raise_if_any(self) -> None:
        if self.fields:
            raise ValidationError(_("Please correct the highlighted fields."), fields=self.fields)


def _number(value, places, errors: _Errors, key: str, *, positive=True, allow_zero=False):
    try:
        number = quantize(value if value not in (None, "") else "0", places)
    except (InvalidOperation, TypeError, ValueError):
        errors.add(key, _("Enter a number."))
        return None
    if positive and (number < 0 or (number == 0 and not allow_zero)):
        errors.add(key, _("Must be greater than zero."))
        return None
    return number


def _fx_value(currency_code: str, amount: Decimal) -> Decimal:
    return round_money(amount * fx_rate(currency_code))


# ---- building the lines ----------------------------------------------------------------------

def _party_lines(rows, branch, errors) -> tuple[list[OpeningLine], list[LedgerLine]]:
    from apps.parties.selectors import parties_with_role

    lines, ledger = [], []
    equity = account_for("opening_equity")
    karats = {k.pk: k for k in Karat.objects.select_related("metal")}
    currencies = {c.code: c for c in Currency.objects.filter(is_active=True)}
    for index, row in enumerate(rows):
        key = f"parties.{index}"
        if row.role not in ROLE_ACCOUNTS:
            errors.add(f"{key}.role", _("Choose who it is."))
            continue
        party = parties_with_role(row.role).filter(pk=row.party_id).first()
        if party is None:
            errors.add(f"{key}.party", _("Choose a name from the list."))
            continue
        account = account_for(ROLE_ACCOUNTS[row.role])
        sign = 1 if row.owes_us else -1
        if row.karat_id:
            karat = karats.get(row.karat_id)
            weight = _number(row.amount, WEIGHT, errors, f"{key}.amount")
            if karat is None:
                errors.add(f"{key}.karat", _("Choose a karat."))
            if karat is None or weight is None:
                continue
            fine = fine_weight(weight, karat.fineness)
            value = metal_value(karat, fine)
            commodity = metal_commodity(karat.metal.code)
            line = OpeningLine(kind=LineKind.PARTY, party=party, role=row.role, karat=karat,
                               weight_g=weight, fine_weight_g=sign * fine,
                               functional_amount=sign * value)
            quantity = sign * fine
        else:
            currency = currencies.get(row.currency_code)
            amount = _number(row.amount, MONEY, errors, f"{key}.amount")
            if currency is None:
                errors.add(f"{key}.currency", _("Choose a currency."))
            if currency is None or amount is None:
                continue
            value = _fx_value(currency.code, amount)
            commodity = money_commodity(currency)
            line = OpeningLine(kind=LineKind.PARTY, party=party, role=row.role, currency=currency,
                               amount=sign * amount, functional_amount=sign * value)
            quantity = sign * amount
        lines.append(line)
        ledger += [
            LedgerLine(account=account, commodity=commodity, quantity=quantity,
                       functional_amount=sign * value, party=party, branch=branch),
            LedgerLine(account=equity, commodity=commodity, quantity=-quantity,
                       functional_amount=-sign * value, branch=branch),
        ]
    return lines, ledger


def _branch_lines(rows, branch, errors) -> tuple[list[OpeningLine], list[LedgerLine]]:
    """Balances between branches. Each is booked on both sides at once (this branch and the
    other), so it is entered once, at either branch."""
    lines, ledger = [], []
    if not rows:
        return lines, ledger
    equity, clearing = account_for("opening_equity"), account_for("branch_clearing")
    karats = {k.pk: k for k in Karat.objects.select_related("metal")}
    currencies = {c.code: c for c in Currency.objects.filter(is_active=True)}
    others = {b.pk: b for b in Branch.objects.filter(is_active=True).exclude(pk=branch.pk)}
    for index, row in enumerate(rows):
        key = f"branches.{index}"
        other = others.get(row.branch_id)
        if other is None:
            errors.add(f"{key}.branch", _("Choose another branch."))
            continue
        earlier = (OpeningLine.objects.filter(kind=LineKind.BRANCH,
                                              opening__status=DocStatus.POSTED)
                   .filter(Q(opening__branch=branch, other_branch=other)
                           | Q(opening__branch=other, other_branch=branch))
                   .select_related("opening").first())
        if earlier is not None:
            errors.add(f"{key}.branch", _("Already entered in %(number)s; cancel it to enter "
                                          "it again.") % {"number": earlier.opening.number})
            continue
        sign = 1 if row.owes_us else -1
        if row.karat_id:
            karat = karats.get(row.karat_id)
            weight = _number(row.amount, WEIGHT, errors, f"{key}.amount")
            if karat is None:
                errors.add(f"{key}.karat", _("Choose a karat."))
            if karat is None or weight is None:
                continue
            fine = fine_weight(weight, karat.fineness)
            value = metal_value(karat, fine)
            commodity = metal_commodity(karat.metal.code)
            lines.append(OpeningLine(kind=LineKind.BRANCH, other_branch=other, karat=karat,
                                     weight_g=weight, fine_weight_g=sign * fine,
                                     functional_amount=sign * value))
            quantity = sign * fine
        else:
            currency = currencies.get(row.currency_code)
            amount = _number(row.amount, MONEY, errors, f"{key}.amount")
            if currency is None:
                errors.add(f"{key}.currency", _("Choose a currency."))
            if currency is None or amount is None:
                continue
            value = _fx_value(currency.code, amount)
            commodity = money_commodity(currency)
            lines.append(OpeningLine(kind=LineKind.BRANCH, other_branch=other, currency=currency,
                                     amount=sign * amount, functional_amount=sign * value))
            quantity = sign * amount
        for side, mirror in ((branch, 1), (other, -1)):  # the other branch: the other way
            ledger += [
                LedgerLine(account=clearing, commodity=commodity, quantity=mirror * quantity,
                           functional_amount=mirror * sign * value, branch=side),
                LedgerLine(account=equity, commodity=commodity, quantity=-mirror * quantity,
                           functional_amount=-mirror * sign * value, branch=side),
            ]
    return lines, ledger


def _holder_lines(rows, branch, errors) -> tuple[list[OpeningLine], list[LedgerLine]]:
    from apps.treasury.models import BankAccount, CashBox

    lines, ledger = [], []
    equity = account_for("opening_equity")
    for index, row in enumerate(rows):
        key = f"holders.{index}"
        if row.kind == "box":
            holder = CashBox.objects.select_related("currency", "account").filter(
                pk=row.holder_id, branch=branch, is_active=True).first()
        elif row.kind == "bank":
            holder = BankAccount.objects.select_related("currency", "account").filter(
                pk=row.holder_id, is_active=True).first()
        else:
            holder = None
        if holder is None:
            errors.add(f"{key}.holder", _("Choose a cash box of this branch or a bank account."))
            continue
        amount = _number(row.amount, MONEY, errors, f"{key}.amount", positive=row.kind == "box")
        if amount is None:
            continue
        if not amount:
            errors.add(f"{key}.amount", _("Enter the balance."))
            continue
        value = _fx_value(holder.currency.code, amount)
        commodity = money_commodity(holder.currency)
        lines.append(OpeningLine(
            kind=LineKind.HOLDER, cash_box=holder if row.kind == "box" else None,
            bank_account=holder if row.kind == "bank" else None, currency=holder.currency,
            amount=amount, functional_amount=value))
        ledger += [
            LedgerLine(account=holder.account, commodity=commodity, quantity=amount,
                       functional_amount=value, branch=branch),
            LedgerLine(account=equity, commodity=commodity, quantity=-amount,
                       functional_amount=-value, branch=branch),
        ]
    return lines, ledger


def _category(category_id, errors, key, tracking):
    category = ItemCategory.objects.filter(pk=category_id, is_active=True).first()
    if category is None:
        errors.add(f"{key}.category", _("Choose a category."))
    elif category.tracking != tracking:
        errors.add(f"{key}.category", _("This category is counted by weight.")
                   if tracking == Tracking.SERIALIZED else
                   _("This category is counted per piece: enter its pieces instead."))
        return None
    return category


def _karat(karat_id, category, errors, key):
    if not karat_id:
        if category is not None and category.product_family in KARAT_FAMILIES:
            errors.add(f"{key}.karat", _("Choose a karat."))
        return None
    karat = Karat.objects.select_related("metal").filter(pk=karat_id, is_active=True).first()
    if karat is None:
        errors.add(f"{key}.karat", _("Choose a karat."))
    return karat


def _check_stock(rows, errors) -> list[tuple[StockBalance, ItemCategory, Karat, Decimal, Decimal]]:
    checked = []
    for index, row in enumerate(rows):
        key = f"stock.{index}"
        category = _category(row.category_id, errors, key, Tracking.BULK)
        karat = _karat(row.karat_id, category, errors, key)
        weight = _number(row.gross_weight_g, WEIGHT, errors, f"{key}.gross_weight_g")
        cost = _number(row.cost_amount, MONEY, errors, f"{key}.cost_amount", allow_zero=True)
        if row.qty < 0:
            errors.add(f"{key}.qty", _("Cannot be negative."))
        if None not in (category, weight, cost) and (karat is not None or not row.karat_id):
            checked.append((row, category, karat, weight, cost))
    return checked


def _check_pieces(rows, errors) -> list[tuple[PieceBalance, ItemCategory, Karat | None, dict]]:
    from apps.parties.selectors import parties_with_role

    checked, seen = [], set()
    wanted = [r.barcode.strip() for r in rows if r.barcode.strip()]
    taken = set(Item.objects.filter(barcode__in=wanted).values_list("barcode", flat=True))
    suppliers = set(parties_with_role(PartyRoleType.SUPPLIER).values_list("pk", flat=True))
    for index, row in enumerate(rows):
        key = f"pieces.{index}"
        barcode = row.barcode.strip()
        if barcode:
            if len(barcode) > 32:
                errors.add(f"{key}.barcode", _("At most 32 characters."))
            elif barcode in taken:
                errors.add(f"{key}.barcode", _("A piece with this barcode already exists."))
            elif barcode in seen:
                errors.add(f"{key}.barcode", _("This barcode is used twice in the list."))
            seen.add(barcode)
        category = _category(row.category_id, errors, key, Tracking.SERIALIZED)
        karat = _karat(row.karat_id, category, errors, key)
        values = {
            "gross_weight_g": _number(row.gross_weight_g, WEIGHT, errors, f"{key}.gross_weight_g"),
            "stone_weight_ct": _number(row.stone_weight_ct, WEIGHT, errors,
                                       f"{key}.stone_weight_ct", allow_zero=True),
            "cost_amount": _number(row.cost_amount, MONEY, errors, f"{key}.cost_amount",
                                   allow_zero=True),
            "list_making_rate": _number(row.list_making_rate, MONEY, errors,
                                        f"{key}.list_making_rate", allow_zero=True),
            "stone_cost_amount": _number(row.stone_cost_amount, MONEY, errors,
                                         f"{key}.stone_cost_amount", allow_zero=True),
        }
        if row.label_price not in (None, ""):
            values["label_price"] = _number(row.label_price, MONEY, errors, f"{key}.label_price")
        if row.supplier_id and row.supplier_id not in suppliers:
            errors.add(f"{key}.supplier", _("Choose a supplier from the list."))
        if category is not None and None not in values.values() \
                and (karat is not None or not row.karat_id):
            checked.append((row, category, karat, values))
    return checked


# ---- posting ---------------------------------------------------------------------------------

def post_opening(data: OpeningInput, *, actor=None) -> OpeningBalance:
    """Validate everything first (all errors at once), then write the stock and the entry."""
    count = (len(data.parties) + len(data.holders) + len(data.branches) + len(data.stock)
             + len(data.pieces))
    if not count:
        raise ValidationError(_("Add at least one balance."), code="OPENING_EMPTY")
    if count > MAX_LINES:
        raise ValidationError(_("At most %(n)s balances in one document.") % {"n": MAX_LINES},
                              code="OPENING_TOO_MANY")
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise ValidationError(_("Please correct the highlighted fields."),
                                  fields={"branch": [_("Unknown branch.")]})
        if actor is not None:
            actor.require("ledger.opening.post", branch=branch)
        today = timezone.localdate()
        day = data.business_date or today
        if day > today:
            raise ValidationError(_("Please correct the highlighted fields."),
                                  fields={"business_date": [_("Cannot be in the future.")]})

        errors = _Errors()
        party_lines, ledger = _party_lines(data.parties, branch, errors)
        holder_lines, holder_ledger = _holder_lines(data.holders, branch, errors)
        branch_lines, branch_ledger = _branch_lines(data.branches, branch, errors)
        stock = _check_stock(data.stock, errors)
        pieces = _check_pieces(data.pieces, errors)
        errors.raise_if_any()

        user = getattr(actor, "user", None)
        doc = OpeningBalance.objects.create(branch=branch, business_date=day,
                                            note=data.note.strip(), created_by=user)
        ref = DocRef(DOC_TYPE, doc.pk)
        lines = party_lines + holder_lines + branch_lines
        ledger += holder_ledger + branch_ledger
        parts: list[StockPart] = []
        for row, category, karat, weight, cost in stock:
            lot = lot_for(category, karat, branch)
            movement = move_lot(lot, qty=row.qty, gross_weight_g=weight, cost_amount=cost,
                                movement_type=MovementType.OPENING, business_date=day, doc=ref)
            parts.append(StockPart(category, karat, movement.fine_weight_g, cost))
            lines.append(OpeningLine(kind=LineKind.STOCK, category=category, karat=karat,
                                     weight_g=weight, fine_weight_g=movement.fine_weight_g,
                                     qty=row.qty, cost_amount=cost, lot=lot))
        supplier_ids = {row.supplier_id for row, *_rest in pieces if row.supplier_id}
        suppliers = Party.objects.in_bulk(supplier_ids)
        for row, category, karat, values in pieces:
            item = create_item(
                category=category, karat=karat, branch=branch, business_date=day, doc=ref,
                supplier=suppliers.get(row.supplier_id), barcode=row.barcode.strip() or None,
                external_code=row.external_code.strip()[:60],
                movement_type=MovementType.OPENING, actor=actor,
                label_price=values.get("label_price"), **{
                    k: v for k, v in values.items() if k != "label_price"})
            parts.append(StockPart(category, karat, item.fine_weight_g, item.cost_amount,
                                   item.stone_cost_amount))
            lines.append(OpeningLine(kind=LineKind.PIECE, category=category, karat=karat,
                                     weight_g=item.gross_weight_g,
                                     fine_weight_g=item.fine_weight_g, qty=1,
                                     cost_amount=item.cost_amount, item=item))
        if parts:
            ledger += stock_lines(parts, sign=+1, counter_role="opening_equity", branch=branch)

        for line in lines:
            line.opening = doc
        OpeningLine.objects.bulk_create(lines)
        doc.number = allocate_number("OB", branch=branch, fiscal_year=day.year)
        if ledger:
            doc.journal_entry = post_entry(
                branch=branch, business_date=day, kind=EntryKind.OPENING, lines=ledger,
                source_type=DOC_TYPE, source_id=doc.pk, memo=_("Opening balances %(number)s")
                % {"number": doc.number})
        doc.status, doc.posted_at, doc.posted_by = DocStatus.POSTED, timezone.now(), user
        doc.save()
    return doc


def void_opening(doc_id: int, *, reason: str = "", actor=None) -> OpeningBalance:
    """Take everything back out: the entry is reversed, the pieces cancelled and the weights
    removed. Refused once a piece has been sold or moved, or the weight has been used."""
    with transaction.atomic():
        doc = (OpeningBalance.objects.select_for_update(of=("self",)).select_related("branch")
               .filter(pk=doc_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("ledger.opening.void", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        ref = DocRef(DOC_TYPE, doc.pk)
        for line in doc.lines.select_related("item", "lot").exclude(item=None):
            item = line.item
            if item.status != ItemStatus.IN_STOCK or item.branch_id != doc.branch_id:
                raise DomainError(
                    _("Piece %(barcode)s has been used since (%(status)s): it cannot be taken "
                      "out.") % {"barcode": item.barcode, "status": item.get_status_display()},
                    code="OPENING_PIECE_USED")
            change_item_status(item.pk, to=ItemStatus.VOIDED, allowed_from=(ItemStatus.IN_STOCK,),
                               movement_type=MovementType.OPENING, business_date=today, doc=ref,
                               branch=doc.branch)
        for line in doc.lines.select_related("lot").exclude(lot=None):
            move_lot(line.lot, qty=-line.qty, gross_weight_g=-line.weight_g,
                     cost_amount=-line.cost_amount, movement_type=MovementType.OPENING,
                     business_date=today, doc=ref)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled: %(reason)s") % {"reason": reason} if reason else "")
        doc.status, doc.voided_at = DocStatus.VOIDED, timezone.now()
        doc.voided_by, doc.void_reason = getattr(actor, "user", None), reason.strip()[:300]
        doc.save(update_fields=["status", "voided_at", "voided_by", "void_reason", "updated_at"])
    return doc
