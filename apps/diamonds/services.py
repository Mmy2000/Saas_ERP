"""Diamonds and gemstones: receiving diamond pieces and loose stones, their stone details and
label prices, and setting loose stones into pieces.

Everything here refuses to run unless the client has the Diamonds feature switched on in the
platform console (it is off by default): permissions say who may act, the feature says whether
the client has the module at all.

Receiving books a supplier invoice (apps.purchasing): the gold is owed in fine grams as for any
purchase, the making on the metal weight, and the stones' cost onto "diamonds and stones".
Setting stones into a piece moves the stones' cost and details onto it (both sit on the same
inventory account, so nothing is booked for that) and books the setter's labour:
    Dr gold inventory (money)   Cr the setter (a workshop)   or   Cr production labour absorbed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import Currency, ItemCategory, Karat, ProductFamily, Tracking
from apps.core.errors import DomainError, NotFound, PermissionDenied, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import GRAMS_PER_CARAT, MONEY, UNIT_PRICE, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.core.tenancy import get_current_tenant_id
from apps.inventory.models import Item, ItemStatus, MovementType
from apps.inventory.services import DocRef, change_item_status
from apps.ledger.models import EntryKind
from apps.ledger.services import (
    LineInput,
    account_for,
    functional_commodity,
    post_entry,
    reverse_entry,
)
from apps.org.models import Branch
from apps.parties.models import Party, PartyRoleType
from apps.platform.tenants.features import is_enabled
from apps.purchasing.models import (
    SellerRole,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierInvoicePiece,
)
from apps.purchasing.services import post_invoice

from .models import ItemStone, StoneKind, StoneSetting, StoneSettingStone, StoneShape

FEATURE = "diamonds"
FAMILIES = (ProductFamily.DIAMOND, ProductFamily.STONE)
SETTING_DOC = "diamonds.StoneSetting"
ZERO = Decimal(0)


# --- the switch ----------------------------------------------------------------------------------

def enabled() -> bool:
    tenant_id = get_current_tenant_id()
    return tenant_id is not None and is_enabled(tenant_id, FEATURE)


def require_enabled() -> None:
    if not enabled():
        raise PermissionDenied(_("Diamonds and gemstones are not part of this workspace."),
                               code="FEATURE_DISABLED")


def is_diamond(category) -> bool:
    return category is not None and category.product_family in FAMILIES


# --- stone details -------------------------------------------------------------------------------

@dataclass(frozen=True)
class StoneInput:
    carat: Decimal | str
    kind: str = StoneKind.DIAMOND
    shape: str = ""
    count: int = 1
    color: str = ""
    clarity: str = ""
    cut: str = ""
    lab: str = ""
    certificate_no: str = ""
    note: str = ""


def clean_stones(stones, field_name: str = "stones") -> list[dict]:
    """Validate stone rows (StoneInput or dicts) into plain dicts, as kept on purchase pieces."""
    cleaned = []
    for index, stone in enumerate(stones):
        data = stone if isinstance(stone, dict) else stone.__dict__

        def error(message, name="carat", index=index):
            return ValidationError(message, fields={field_name: {str(index): {name: [message]}}})

        try:
            carat = quantize(data.get("carat") or "0", Decimal("0.001"))
        except (TypeError, ValueError, InvalidOperation):
            raise error(_("Enter the carats.")) from None
        if carat <= 0:
            raise error(_("Enter the carats."))
        kind = data.get("kind") or StoneKind.DIAMOND
        if kind not in StoneKind.values:
            raise error(_("Choose the kind of stone."), "kind")
        shape = data.get("shape") or ""
        if shape and shape not in StoneShape.values:
            raise error(_("Choose the shape."), "shape")
        try:
            count = int(data.get("count") or 1)
        except (TypeError, ValueError):
            raise error(_("Enter how many."), "count") from None
        if count < 1:
            raise error(_("Enter how many."), "count")
        cleaned.append({
            "kind": kind, "shape": shape, "count": count, "carat": str(carat),
            **{key: str(data.get(key) or "").strip()[:limit] for key, limit in (
                ("color", 20), ("clarity", 20), ("cut", 20), ("lab", 40),
                ("certificate_no", 60), ("note", 200))},
        })
    return cleaned


def add_stones(item: Item, stones, *, setting=None) -> list[ItemStone]:
    rows = [ItemStone(item=item, setting=setting, kind=s["kind"], shape=s["shape"],
                      count=s["count"], carat=Decimal(s["carat"]), color=s["color"],
                      clarity=s["clarity"], cut=s["cut"], lab=s["lab"],
                      certificate_no=s["certificate_no"], note=s["note"])
            for s in clean_stones(stones)]
    return ItemStone.objects.bulk_create(rows)


def update_item(item_id: int, *, stones, label_price=None, actor=None) -> Item:
    """Replace a piece's stone details and set its label price (stone weight follows)."""
    require_enabled()
    with transaction.atomic():
        item = Item.objects.select_for_update().select_related("category", "branch").filter(
            pk=item_id).first()
        if item is None or not is_diamond(item.category):
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.manage", branch=item.branch)
        cleaned = clean_stones(stones)
        ItemStone.objects.filter(item=item, setting__isnull=True).delete()
        add_stones(item, cleaned)
        if label_price not in (None, ""):
            try:
                price = quantize(label_price, MONEY)
            except (TypeError, ValueError, InvalidOperation):
                raise _field("label_price", _("Enter a number.")) from None
            if price <= 0:
                raise _field("label_price", _("Must be greater than zero."))
            item.label_price = price
        item.stone_weight_ct = sum((s.carat for s in item.stones.all()), ZERO)
        item.updated_by = getattr(actor, "user", None)
        item.save()
    return item


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


def stones_summary(stones) -> str:
    """'1 × 0.50 ct D VS1 (GIA) + 12 × 0.24 ct' for lists and labels."""
    parts = []
    for stone in stones:
        grade = " ".join(part for part in (stone.color, stone.clarity) if part)
        text = f"{stone.count} × {stone.carat.normalize():f} ct"
        if grade:
            text += f" {grade}"
        if stone.lab:
            text += f" ({stone.lab})"
        parts.append(text)
    return " + ".join(parts)


# --- receiving from suppliers --------------------------------------------------------------------

@dataclass(frozen=True)
class PieceInput:
    category_id: int
    stones: tuple = ()
    karat_id: int | None = None
    gross_weight_g: Decimal | str | None = None  # pieces in gold; loose stones: from the carats
    making_cost_rate: Decimal | str = "0"  # per gram of gold, in the invoice currency
    stone_cost: Decimal | str = "0"  # in the invoice currency
    label_price: Decimal | str | None = None  # selling price, company currency


@dataclass(frozen=True)
class ReceiveInput:
    supplier_id: int
    branch_id: int
    currency_code: str
    pieces: tuple[PieceInput, ...]
    business_date: date | None = None
    supplier_reference: str = ""
    note: str = ""
    seller_role: str = SellerRole.SUPPLIER


def _piece_error(index: int, name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={"pieces": {str(index): {name: [message]}}})


def receive(data: ReceiveInput, *, actor=None) -> SupplierInvoice:
    """One supplier invoice for diamond pieces and loose stones, posted at once."""
    require_enabled()
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("diamonds.receive", branch=branch)
        role = data.seller_role if data.seller_role in SellerRole.values else None
        supplier = Party.objects.filter(pk=data.supplier_id, is_active=True,
                                        roles__role=role).first() if role else None
        if supplier is None:
            raise _field("supplier", _("Choose an active supplier."))
        currency = Currency.objects.filter(code=data.currency_code, is_active=True).first()
        if currency is None:
            raise _field("currency", _("Unknown currency."))
        if not data.pieces:
            raise _field("pieces", _("Add at least one piece or stone."))

        categories = {c.pk: c for c in ItemCategory.objects.filter(
            pk__in=[p.category_id for p in data.pieces], is_active=True)}
        karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(
            pk__in=[p.karat_id for p in data.pieces if p.karat_id], is_active=True)}
        invoice = SupplierInvoice.objects.create(
            supplier=supplier, seller_role=role, branch=branch, currency=currency,
            business_date=data.business_date or timezone.localdate(),
            supplier_reference=data.supplier_reference.strip()[:60], note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        for index, piece in enumerate(data.pieces):
            category = categories.get(piece.category_id)
            if category is None or not is_diamond(category) \
                    or category.tracking != Tracking.SERIALIZED:
                raise _piece_error(index, "category", _("Choose a diamond or stone category."))
            stones = clean_stones(piece.stones, field_name=f"pieces.{index}.stones")
            carats = sum((Decimal(s["carat"]) for s in stones), ZERO)
            loose = category.product_family == ProductFamily.STONE
            karat = None if loose else karats.get(piece.karat_id)
            if not loose and karat is None:
                raise _piece_error(index, "karat", _("Choose the karat."))
            if loose:
                if not stones:
                    raise _piece_error(index, "stones", _("Describe the stones."))
                gross = quantize(carats * GRAMS_PER_CARAT, WEIGHT)
            else:
                try:
                    gross = quantize(piece.gross_weight_g or "0", WEIGHT)
                except (TypeError, ValueError, InvalidOperation):
                    gross = ZERO
                if gross <= 0:
                    raise _piece_error(index, "gross_weight_g", _("Enter the weight."))
                if carats * GRAMS_PER_CARAT >= gross:
                    raise _piece_error(index, "gross_weight_g",
                                       _("The stones weigh more than the piece."))
            try:
                rate = quantize(piece.making_cost_rate or "0", UNIT_PRICE)
                stone_cost = quantize(piece.stone_cost or "0", MONEY)
                label = (quantize(piece.label_price, MONEY)
                         if piece.label_price not in (None, "") else None)
            except (TypeError, ValueError, InvalidOperation):
                raise _piece_error(index, "stone_cost", _("Enter a number.")) from None
            if rate < 0 or stone_cost < 0 or (label is not None and label <= 0):
                raise _piece_error(index, "stone_cost", _("Must not be negative."))
            if label is None:
                raise _piece_error(index, "label_price", _("Enter the selling price."))
            metal = gross - quantize(carats * GRAMS_PER_CARAT, WEIGHT) if not loose else ZERO
            line = SupplierInvoiceLine.objects.create(
                invoice=invoice, position=index, category=category, karat=karat, qty=1,
                gross_weight_g=gross,
                fine_weight_g=fine_weight(metal, karat.fineness) if karat else ZERO,
                making_cost_rate=rate, making_cost_amount=round_money(rate * metal))
            SupplierInvoicePiece.objects.create(
                line=line, gross_weight_g=gross, stone_weight_ct=carats, stone_cost=stone_cost,
                label_price=label, stones=stones)
    return post_invoice(invoice.pk, actor=actor)


# --- setting stones into pieces ------------------------------------------------------------------

@dataclass(frozen=True)
class SettingInput:
    piece_id: int
    stone_ids: tuple[int, ...]
    gross_after_g: Decimal | str
    setter_id: int | None = None
    labour_amount: Decimal | str = "0"
    note: str = ""


def set_stones(data: SettingInput, *, actor=None) -> StoneSetting:
    require_enabled()
    with transaction.atomic():
        piece = (Item.objects.select_for_update().select_related("category", "branch")
                 .filter(pk=data.piece_id).first())
        if piece is None or piece.category.product_family != ProductFamily.DIAMOND:
            raise _field("piece", _("Choose a diamond piece (a mounting) in stock."))
        if piece.status != ItemStatus.IN_STOCK:
            raise _field("piece", _("The piece is not in stock."))
        branch = piece.branch
        if actor is not None:
            actor.require("diamonds.setting", branch=branch)
        ids = sorted(set(data.stone_ids))
        if not ids:
            raise _field("stones", _("Add the stones to set."))
        stones = list(Item.objects.select_related("category").filter(pk__in=ids)
                      .prefetch_related("stones"))
        for stone in stones:
            if stone.category.product_family != ProductFamily.STONE \
                    or stone.status != ItemStatus.IN_STOCK or stone.branch_id != branch.pk:
                raise _field("stones", _("Stone %(barcode)s is not a loose stone in stock here.")
                             % {"barcode": stone.barcode})
        if len(stones) != len(ids):
            raise _field("stones", _("Unknown stone."))
        setter = None
        if data.setter_id:
            setter = Party.objects.filter(pk=data.setter_id, is_active=True,
                                          roles__role=PartyRoleType.WORKSHOP).first()
            if setter is None:
                raise _field("setter", _("Choose the setter's workshop."))
        try:
            after = quantize(data.gross_after_g, WEIGHT)
            labour = quantize(data.labour_amount or "0", MONEY)
        except (TypeError, ValueError, InvalidOperation):
            raise _field("gross_after_g", _("Enter the weight after setting.")) from None
        if after <= 0:
            raise _field("gross_after_g", _("Enter the weight after setting."))
        if labour < 0:
            raise _field("labour_amount", _("Must not be negative."))

        today = timezone.localdate()
        carats = sum((s.stone_weight_ct for s in stones), ZERO)
        cost = sum((s.stone_cost_amount for s in stones), ZERO)
        setting = StoneSetting.objects.create(
            branch=branch, business_date=today, piece=piece, setter=setter,
            gross_before_g=piece.gross_weight_g, gross_after_g=after, stones_carat=carats,
            stones_cost=cost, labour_amount=labour, note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        doc = DocRef(SETTING_DOC, setting.pk)
        for stone in stones:
            change_item_status(stone.pk, to=ItemStatus.CONSUMED,
                               allowed_from=(ItemStatus.IN_STOCK,),
                               movement_type=MovementType.PRODUCTION_CONSUME,
                               business_date=today, doc=doc, branch=branch)
            StoneSettingStone.objects.create(setting=setting, stone=stone,
                                             carat=stone.stone_weight_ct,
                                             cost=stone.stone_cost_amount)
            add_stones(piece, [_as_dict(row) for row in stone.stones.all()], setting=setting)
        # The gold is unchanged; the piece now carries the stones and the labour.
        piece.gross_weight_g = after
        piece.stone_weight_ct += carats
        piece.stone_cost_amount += cost
        piece.cost_amount += labour
        piece.updated_by = getattr(actor, "user", None)
        piece.save()

        setting.number = allocate_number("SS", branch=branch, fiscal_year=today.year)
        if labour:
            home = functional_commodity()
            credit = (LineInput(account=account_for("workshops"), commodity=home,
                                quantity=-labour, party=setter) if setter
                      else LineInput(account=account_for("labour_absorbed"), commodity=home,
                                     quantity=-labour))
            setting.journal_entry = post_entry(
                branch=branch, business_date=today, kind=EntryKind.AUTO,
                source_type=SETTING_DOC, source_id=setting.pk,
                memo=_("Stones set in %(barcode)s") % {"barcode": piece.barcode},
                lines=[LineInput(account=account_for("inventory_gold"), commodity=home,
                                 quantity=labour), credit])
        setting.status = DocStatus.POSTED
        setting.posted_at = timezone.now()
        setting.posted_by = getattr(actor, "user", None)
        setting.save()
    return setting


def _as_dict(stone: ItemStone) -> dict:
    return {"kind": stone.kind, "shape": stone.shape, "count": stone.count,
            "carat": str(stone.carat), "color": stone.color, "clarity": stone.clarity,
            "cut": stone.cut, "lab": stone.lab, "certificate_no": stone.certificate_no,
            "note": stone.note}


def cancel_setting(setting_id: int, *, reason: str = "", actor=None) -> StoneSetting:
    """Undo a setting while the piece is untouched in stock: the stones come back loose."""
    require_enabled()
    with transaction.atomic():
        setting = (StoneSetting.objects.select_for_update(of=("self",))
                   .select_related("branch").filter(pk=setting_id).first())
        if setting is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.setting", branch=setting.branch)
        if setting.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        piece = Item.objects.select_for_update().get(pk=setting.piece_id)
        later = StoneSetting.objects.filter(piece=piece, status=DocStatus.POSTED,
                                            pk__gt=setting.pk).exists()
        if piece.status != ItemStatus.IN_STOCK or later:
            raise DomainError(_("The piece has moved on since: this setting cannot be undone."),
                              code="DIAMONDS_SETTING_LOCKED")
        today = timezone.localdate()
        doc = DocRef(SETTING_DOC, setting.pk)
        for row in setting.stones.order_by("stone_id"):
            change_item_status(row.stone_id, to=ItemStatus.IN_STOCK,
                               allowed_from=(ItemStatus.CONSUMED,),
                               movement_type=MovementType.PRODUCTION_OUTPUT, business_date=today,
                               doc=doc, branch=setting.branch, stock_delta=1)
        ItemStone.objects.filter(item=piece, setting=setting).delete()
        piece.gross_weight_g = setting.gross_before_g
        piece.stone_weight_ct -= setting.stones_carat
        piece.stone_cost_amount -= setting.stones_cost
        piece.cost_amount -= setting.labour_amount
        piece.save()
        if setting.journal_entry_id:
            reverse_entry(setting.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": setting.number})
        setting.status = DocStatus.VOIDED
        setting.voided_at = timezone.now()
        setting.voided_by = getattr(actor, "user", None)
        setting.void_reason = reason.strip()[:300]
        setting.save()
    return setting
