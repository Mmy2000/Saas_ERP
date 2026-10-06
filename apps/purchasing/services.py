"""Supplier invoice use cases: draft, post, void (§7.8, §16.1).

Posting, in one transaction:
1. serialized lines → one Item per piece (barcode, weights, cost); bulk lines → lot receipts;
2. the invoice number (PI);
3. one ledger entry: the supplier is owed the gold in fine grams (per metal) and the making
   charges in the invoice currency; inventory takes both. Diamond pieces and loose stones
   (apps.diamonds) also owe their stones' cost, carried on "diamonds and stones" inventory.
Voiding reverses all three, and only while every piece is still in stock at the branch.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import Currency, ItemCategory, Karat, ProductFamily, Tracking
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import FX_RATE, GRAMS_PER_CARAT, UNIT_PRICE, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import ItemStatus, MovementType
from apps.inventory.services import DocRef, change_item_status, create_item, lot_for, move_lot
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
from apps.parties.models import Party
from apps.pricing.selectors import fine_gram_value, functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force

from .models import (
    SELLER_ACCOUNT,
    SellerRole,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierInvoicePiece,
)

DOC_TYPE = "purchasing.SupplierInvoice"
# Families whose lines need a karat, and the metal that karat must be (None = any metal).
METAL_FAMILIES = {ProductFamily.GOLD: "gold", ProductFamily.SILVER: "silver",
                  ProductFamily.BULLION_COIN: None, ProductFamily.DIAMOND: "gold"}


@dataclass(frozen=True)
class LineInput:
    category_id: int
    karat_id: int | None
    qty: int = 0
    gross_weight_g: Decimal | str | None = None  # bulk lines
    piece_weights: tuple[Decimal | str, ...] = ()  # serialized lines: one weight per piece
    making_cost_rate: Decimal | str = "0"
    list_making_rate: Decimal | str = "0"
    note: str = ""


@dataclass(frozen=True)
class InvoiceInput:
    supplier_id: int
    branch_id: int
    business_date: date
    currency_code: str
    lines: tuple[LineInput, ...]
    fx_rate: Decimal | str | None = None
    supplier_reference: str = ""
    note: str = ""
    seller_role: str = SellerRole.SUPPLIER


# --- drafts -----------------------------------------------------------------------------------

def _line_error(index: int, field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."),
                           fields={"lines": {str(index): {field: [message]}}})


def _clean_lines(lines: tuple[LineInput, ...]) -> list[dict]:
    if not lines:
        raise ValidationError(_("Add at least one line."), fields={"lines": [_("Add a line.")]})
    categories = {c.pk: c for c in ItemCategory.objects.filter(
        pk__in=[ln.category_id for ln in lines], is_active=True)}
    karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(
        pk__in=[ln.karat_id for ln in lines if ln.karat_id], is_active=True)}
    cleaned = []
    for index, line in enumerate(lines):
        category = categories.get(line.category_id)
        if category is None:
            raise _line_error(index, "category", _("Unknown category."))
        karat = karats.get(line.karat_id) if line.karat_id else None
        if line.karat_id and karat is None:
            raise _line_error(index, "karat", _("Unknown karat."))
        expected_metal = METAL_FAMILIES.get(category.product_family)
        if category.product_family in METAL_FAMILIES and karat is None:
            raise _line_error(index, "karat", _("Choose the karat."))
        if karat is not None and expected_metal and karat.metal.code != expected_metal:
            raise _line_error(index, "karat", _("This karat does not match the category."))

        if category.tracking == Tracking.SERIALIZED:
            try:
                pieces = [quantize(w, WEIGHT) for w in line.piece_weights]
            except (TypeError, ValueError):
                raise _line_error(index, "piece_weights", _("Enter the weight of each piece.")) \
                    from None
            if not pieces or any(w <= 0 for w in pieces):
                raise _line_error(index, "piece_weights", _("Enter the weight of each piece."))
            qty, gross = len(pieces), sum(pieces, Decimal(0))
        else:
            pieces = []
            try:
                gross = quantize(line.gross_weight_g or "0", WEIGHT)
            except (TypeError, ValueError):
                gross = Decimal(0)
            if gross <= 0:
                raise _line_error(index, "gross_weight_g", _("Enter the total weight."))
            qty = int(line.qty or 0)
            if qty < 0:
                raise _line_error(index, "qty", _("Must not be negative."))

        try:
            making_rate = quantize(line.making_cost_rate or "0", UNIT_PRICE)
            list_rate = quantize(line.list_making_rate or "0", UNIT_PRICE)
        except (TypeError, ValueError):
            raise _line_error(index, "making_cost_rate", _("Enter a number.")) from None
        if making_rate < 0 or list_rate < 0:
            raise _line_error(index, "making_cost_rate", _("Must not be negative."))

        cleaned.append({
            "category": category, "karat": karat, "qty": qty, "gross_weight_g": gross,
            "fine_weight_g": fine_weight(gross, karat.fineness) if karat else Decimal(0),
            "making_cost_rate": making_rate, "making_cost_amount": round_money(making_rate * gross),
            "list_making_rate": list_rate, "note": line.note[:200], "pieces": pieces,
        })
    return cleaned


def _clean_header(data: InvoiceInput) -> dict:
    errors: dict[str, list[str]] = {}
    role = data.seller_role if data.seller_role in SellerRole.values else None
    supplier = Party.objects.filter(pk=data.supplier_id, is_active=True,
                                    roles__role=role).first() if role else None
    if supplier is None:
        errors["supplier"] = [_("Choose an active trade account.")
                              if role == SellerRole.TRADE_ACCOUNT
                              else _("Choose an active supplier.")]
    branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
    if branch is None:
        errors["branch"] = [_("Unknown branch.")]
    currency = Currency.objects.filter(code=data.currency_code, is_active=True).first()
    if currency is None:
        errors["currency"] = [_("Unknown currency.")]
    fx = None
    if data.fx_rate not in (None, ""):
        fx = quantize(data.fx_rate, FX_RATE)
        if fx <= 0:
            errors["fx_rate"] = [_("Must be greater than zero.")]
    if errors:
        raise ValidationError(_("Please correct the highlighted fields."), fields=errors)
    if currency.code == functional_currency():
        fx = None
    return {"supplier": supplier, "seller_role": role, "branch": branch, "currency": currency,
            "fx_rate": fx}


def save_draft(data: InvoiceInput, *, invoice_id: int | None = None,
               actor=None) -> SupplierInvoice:
    header = _clean_header(data)
    if actor is not None:
        actor.require("purchasing.invoice.create", branch=header["branch"])
    lines = _clean_lines(data.lines)
    with transaction.atomic():
        if invoice_id is None:
            invoice = SupplierInvoice(created_by=getattr(actor, "user", None))
        else:
            invoice = SupplierInvoice.objects.select_for_update().filter(pk=invoice_id).first()
            if invoice is None:
                raise NotFound(_("Not found."))
            if not invoice.is_draft:
                raise DomainError(_("Only drafts can be edited."), code="DOC_NOT_DRAFT")
        invoice.supplier = header["supplier"]
        invoice.seller_role = header["seller_role"]
        invoice.branch = header["branch"]
        invoice.currency = header["currency"]
        invoice.fx_rate = header["fx_rate"]
        invoice.business_date = data.business_date
        invoice.supplier_reference = data.supplier_reference.strip()[:60]
        invoice.note = data.note.strip()
        invoice.updated_by = getattr(actor, "user", None)
        invoice.save()
        invoice.lines.all().delete()
        for position, line in enumerate(lines):
            pieces = line.pop("pieces")
            saved = SupplierInvoiceLine.objects.create(invoice=invoice, position=position, **line)
            SupplierInvoicePiece.objects.bulk_create(
                [SupplierInvoicePiece(line=saved, gross_weight_g=w) for w in pieces])
    return invoice


def delete_draft(invoice_id: int, *, actor=None) -> None:
    invoice = SupplierInvoice.objects.filter(pk=invoice_id).first()
    if invoice is None:
        raise NotFound(_("Not found."))
    if actor is not None:
        actor.require("purchasing.invoice.create", branch=invoice.branch)
    if not invoice.is_draft:
        raise DomainError(_("Posted invoices are cancelled, not deleted."), code="DOC_NOT_DRAFT")
    invoice.delete()


# --- posting ----------------------------------------------------------------------------------

def _end_of(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.max))


def _stone_cost(lines) -> Decimal:
    """What the stones of the invoice's pieces cost, in the invoice currency."""
    return sum((piece.stone_cost for line in lines for piece in line.pieces.all()), Decimal(0))


def _ledger_lines(invoice: SupplierInvoice, lines, fx: Decimal) -> list[LedgerLine]:
    inventory = account_for("inventory_gold")
    payable = account_for(SELLER_ACCOUNT[invoice.seller_role])
    result: list[LedgerLine] = []
    fine_by_metal: dict[str, Decimal] = defaultdict(Decimal)
    for line in lines:
        if line.karat is not None:
            fine_by_metal[line.karat.metal.code] += line.fine_weight_g
    for metal_code, fine in sorted(fine_by_metal.items()):
        if fine <= 0:
            continue
        commodity = metal_commodity(metal_code)
        value = round_money(fine * fine_gram_value(metal_code, _end_of(invoice.business_date)))
        result += [
            LedgerLine(account=inventory, commodity=commodity, quantity=fine,
                       functional_amount=value),
            LedgerLine(account=payable, commodity=commodity, quantity=-fine,
                       functional_amount=-value, party=invoice.supplier),
        ]
    making = sum((line.making_cost_amount for line in lines), Decimal(0))
    if making > 0:
        functional = round_money(making * fx)
        result += [
            LedgerLine(account=inventory, commodity=functional_commodity(), quantity=functional),
            LedgerLine(account=payable, commodity=money_commodity(invoice.currency),
                       quantity=-making, functional_amount=-functional, party=invoice.supplier),
        ]
    stones = _stone_cost(lines)
    if stones > 0:
        functional = round_money(stones * fx)
        result += [
            LedgerLine(account=account_for("inventory_diamonds"), commodity=functional_commodity(),
                       quantity=functional),
            LedgerLine(account=payable, commodity=money_commodity(invoice.currency),
                       quantity=-stones, functional_amount=-functional, party=invoice.supplier),
        ]
    return result


def post_invoice(invoice_id: int, *, actor=None) -> SupplierInvoice:
    with transaction.atomic():
        invoice = (SupplierInvoice.objects.select_for_update()
                   .select_related("branch", "currency", "supplier").filter(pk=invoice_id).first())
        if invoice is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("purchasing.invoice.post", branch=invoice.branch)
        if not invoice.is_draft:
            raise DomainError(_("This invoice is already posted."), code="DOC_NOT_DRAFT")
        lines = list(invoice.lines.select_related("category", "karat__metal")
                     .prefetch_related("pieces"))
        if not lines:
            raise DomainError(_("Add at least one line."), code="PURCHASING_EMPTY")

        fx = invoice.fx_rate or rate_in_force(invoice.currency.code, _end_of(invoice.business_date))
        doc = DocRef(DOC_TYPE, invoice.pk)
        for line in lines:
            if line.category.tracking == Tracking.SERIALIZED:
                for piece in line.pieces.all():
                    # Making is charged on the metal: the stones are not gold.
                    metal = max(piece.gross_weight_g - piece.stone_weight_ct * GRAMS_PER_CARAT,
                                Decimal(0))
                    piece.item = create_item(
                        category=line.category, karat=line.karat,
                        gross_weight_g=piece.gross_weight_g, branch=invoice.branch,
                        business_date=invoice.business_date, doc=doc, supplier=invoice.supplier,
                        cost_currency=invoice.currency, cost_making_rate=line.making_cost_rate,
                        cost_amount=round_money(line.making_cost_rate * metal * fx),
                        list_making_rate=line.list_making_rate, actor=actor,
                        stone_weight_ct=piece.stone_weight_ct,
                        stone_cost_amount=round_money(piece.stone_cost * fx),
                        label_price=piece.label_price,
                    )
                    piece.save(update_fields=["item", "updated_at"])
                    if piece.stones:
                        from apps.diamonds.services import add_stones

                        add_stones(piece.item, piece.stones)
            else:
                move_lot(lot_for(line.category, line.karat, invoice.branch), qty=line.qty,
                         gross_weight_g=line.gross_weight_g,
                         cost_amount=round_money(line.making_cost_amount * fx),
                         movement_type=MovementType.PURCHASE_RECEIPT,
                         business_date=invoice.business_date, doc=doc)

        invoice.number = allocate_number("PI", branch=invoice.branch,
                                         fiscal_year=invoice.business_date.year)
        ledger_lines = _ledger_lines(invoice, lines, fx)
        if ledger_lines:
            invoice.journal_entry = post_entry(
                branch=invoice.branch, business_date=invoice.business_date, lines=ledger_lines,
                kind=EntryKind.AUTO, source_type=DOC_TYPE, source_id=invoice.pk,
                memo=_("Supplier invoice %(number)s") % {"number": invoice.number},
            )
        invoice.fx_rate = None if invoice.currency.code == functional_currency() else fx
        invoice.status = DocStatus.POSTED
        invoice.posted_at = timezone.now()
        invoice.posted_by = getattr(actor, "user", None)
        invoice.save()
    return invoice


def void_invoice(invoice_id: int, *, reason: str = "", actor=None) -> SupplierInvoice:
    with transaction.atomic():
        invoice = (SupplierInvoice.objects.select_for_update().select_related("branch", "currency")
                   .filter(pk=invoice_id).first())
        if invoice is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("purchasing.invoice.void", branch=invoice.branch)
        if invoice.status != DocStatus.POSTED:
            raise DomainError(_("Only posted invoices can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        doc = DocRef(DOC_TYPE, invoice.pk)
        fx = invoice.fx_rate or Decimal(1)
        lines = invoice.lines.select_related("category", "karat").prefetch_related("pieces")
        for line in sorted(lines, key=lambda ln: ln.pk):
            if line.category.tracking == Tracking.SERIALIZED:
                for piece in sorted(line.pieces.all(), key=lambda p: p.item_id):
                    change_item_status(piece.item_id, to=ItemStatus.VOIDED,
                                       allowed_from=(ItemStatus.IN_STOCK,),
                                       movement_type=MovementType.PURCHASE_VOID,
                                       business_date=today, doc=doc, branch=invoice.branch)
            else:
                move_lot(lot_for(line.category, line.karat, invoice.branch), qty=-line.qty,
                         gross_weight_g=-line.gross_weight_g,
                         cost_amount=-round_money(line.making_cost_amount * fx),
                         movement_type=MovementType.PURCHASE_VOID, business_date=today, doc=doc)
        if invoice.journal_entry_id:
            reverse_entry(invoice.journal_entry_id, business_date=today,
                          memo=_("Cancelled supplier invoice %(number)s")
                          % {"number": invoice.number})
        invoice.status = DocStatus.VOIDED
        invoice.voided_at = timezone.now()
        invoice.voided_by = getattr(actor, "user", None)
        invoice.void_reason = reason.strip()[:300]
        invoice.save()
    return invoice


def totals(invoice: SupplierInvoice) -> dict:
    """Display totals: pieces, weights, fine grams per metal, making charges."""
    result = {"pieces": 0, "gross_weight_g": Decimal(0), "making_cost_amount": Decimal(0),
              "fine_by_metal": defaultdict(Decimal), "stone_cost": Decimal(0)}
    lines = list(invoice.lines.select_related("karat__metal", "category")
                 .prefetch_related("pieces"))
    result["stone_cost"] = _stone_cost(lines)
    for line in lines:
        result["pieces"] += line.qty if line.category.tracking == Tracking.SERIALIZED else 0
        result["gross_weight_g"] += line.gross_weight_g
        result["making_cost_amount"] += line.making_cost_amount
        if line.karat is not None:
            result["fine_by_metal"][line.karat.metal.code] += line.fine_weight_g
    result["fine_by_metal"] = dict(result["fine_by_metal"])
    return result

