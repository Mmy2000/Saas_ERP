"""Catalog use cases: seeding a new tenant's reference data and creating item categories."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.errors import ValidationError
from apps.core.numeric import RATE, UNIT_PRICE, quantize, to_decimal

from .domain.metal import DEFAULT_GOLD_FINENESS, DEFAULT_SILVER_FINENESS
from .models import (
    MAX_CATEGORY_DEPTH,
    CategoryMakingCharge,
    Currency,
    ItemCategory,
    Karat,
    Metal,
    MetalCode,
    ProductFamily,
)

CURRENCY_SYMBOLS = {"EGP": "ج.م", "USD": "$", "AED": "د.إ", "SAR": "ر.س", "EUR": "€"}

GOLD_REFERENCE_KARAT = 21
SILVER_KARAT = 925
LEGACY_SILVER_CODE = 1  # `Cod.co1` stores silver as karat 1


def seed_reference_data(*, functional_currency: str = "EGP",
                        fineness_24k: Decimal | str | None = None) -> None:
    """Default currencies, metals and karats for a newly provisioned tenant (§5.7, §8.2).

    Karat and currency names are left blank so they display in each viewer's language; a
    tenant can still set its own. Runs inside the tenant's context and is idempotent.
    """
    for code in dict.fromkeys([functional_currency, "USD"]):
        Currency.objects.get_or_create(
            code=code, defaults={"symbol": CURRENCY_SYMBOLS.get(code, ""), "minor_units": 2}
        )

    gold, _created = Metal.objects.get_or_create(code=MetalCode.GOLD,
                                                 defaults={"name": str(MetalCode.GOLD.label)})
    silver, _created = Metal.objects.get_or_create(code=MetalCode.SILVER,
                                                   defaults={"name": str(MetalCode.SILVER.label)})

    gold_fineness = dict(DEFAULT_GOLD_FINENESS)
    if fineness_24k is not None:
        gold_fineness[24] = to_decimal(fineness_24k)
    for code, fineness in gold_fineness.items():
        Karat.objects.get_or_create(
            metal=gold, code=code,
            defaults={"fineness": fineness, "is_reference": code == GOLD_REFERENCE_KARAT,
                      "legacy_code": code},
        )
    Karat.objects.get_or_create(
        metal=silver, code=SILVER_KARAT,
        defaults={"fineness": DEFAULT_SILVER_FINENESS, "is_reference": True,
                  "legacy_code": LEGACY_SILVER_CODE},
    )


def _check_family(family: str) -> None:
    """Diamond and stone categories only where the platform switched diamonds on."""
    if family not in (ProductFamily.DIAMOND, ProductFamily.STONE):
        return
    from apps.core.tenancy import get_current_tenant_id
    from apps.platform.tenants.features import is_enabled

    tenant_id = get_current_tenant_id()
    if tenant_id is None or not is_enabled(tenant_id, "diamonds"):
        message = _("Diamonds and gemstones are not part of this workspace.")
        raise ValidationError(message, fields={"product_family": [message]})

@dataclass(frozen=True)
class MakingChargeInput:
    currency_code: str
    cost_rate_per_g: Decimal | str = "0"
    list_rate_per_g: Decimal | str = "0"


@dataclass(frozen=True)
class CreateItemCategoryCommand:
    code: str
    name: str
    product_family: str
    tracking: str
    parent_id: int | None = None
    short_name: str = ""
    default_karat_id: int | None = None
    barcode_prefix: int | None = None
    commission_rate: Decimal | str = "0"
    making_charges: tuple[MakingChargeInput, ...] = ()


def create_item_category(cmd: CreateItemCategoryCommand, *, actor=None) -> ItemCategory:
    if actor is not None:
        actor.require("catalog.category.manage")
    user = getattr(actor, "user", None)
    with transaction.atomic():
        parent = None
        if cmd.parent_id is not None:
            parent = ItemCategory.objects.filter(pk=cmd.parent_id).first()
            if parent is None:
                raise ValidationError(_("Unknown parent category."),
                                      fields={"parent_id": [_("Unknown category.")]})
            if parent.depth >= MAX_CATEGORY_DEPTH:
                raise ValidationError(
                    _("Categories are at most %(depth)d levels deep.")
                    % {"depth": MAX_CATEGORY_DEPTH},
                    code="CATALOG_CATEGORY_TOO_DEEP", fields={"parent_id": [_("Too deep.")]},
                )

        default_karat = None
        if cmd.default_karat_id is not None:
            default_karat = Karat.objects.filter(pk=cmd.default_karat_id).first()
            if default_karat is None:
                raise ValidationError(_("Unknown karat."),
                                      fields={"default_karat_id": [_("Unknown karat.")]})

        commission_rate = quantize(cmd.commission_rate, RATE)
        if not Decimal(0) <= commission_rate <= Decimal(1):
            raise ValidationError(_("Commission rate is a fraction between 0 and 1."),
                                  fields={"commission_rate": [_("Out of range.")]})
        _check_family(cmd.product_family)

        category = ItemCategory(
            code=cmd.code.strip(), name=cmd.name.strip(), short_name=cmd.short_name,
            parent=parent, depth=(parent.depth + 1) if parent else 1,
            product_family=cmd.product_family, tracking=cmd.tracking,
            default_karat=default_karat, barcode_prefix=cmd.barcode_prefix,
            commission_rate=commission_rate, created_by=user, updated_by=user,
        )
        category._stamp_tenant()  # so full_clean can check the per-tenant unique constraints
        try:
            category.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError(_("Invalid category."), fields=exc.message_dict) from exc
        category.save()

        _set_making_charges(category, cmd.making_charges)
        return category


def _set_making_charges(category: ItemCategory, charges) -> None:
    currencies = {c.code: c for c in Currency.objects.filter(
        code__in=[m.currency_code for m in charges])}
    for charge in charges:
        currency = currencies.get(charge.currency_code)
        if currency is None:
            raise ValidationError(_("Unknown currency %(code)s.") % {"code": charge.currency_code},
                                  fields={"making_charges": [_("Unknown currency.")]})
        CategoryMakingCharge.objects.update_or_create(
            category=category, currency=currency,
            defaults={"cost_rate_per_g": quantize(charge.cost_rate_per_g, UNIT_PRICE),
                      "list_rate_per_g": quantize(charge.list_rate_per_g, UNIT_PRICE)})


def _subtree(category: ItemCategory) -> list[ItemCategory]:
    """The category's descendants (not itself)."""
    found, frontier = [], [category.pk]
    while frontier:
        level = list(ItemCategory.objects.filter(parent_id__in=frontier))
        found += level
        frontier = [c.pk for c in level]
    return found


@dataclass(frozen=True)
class UpdateItemCategoryCommand(CreateItemCategoryCommand):
    is_active: bool = True


def update_item_category(category_id: int, cmd: UpdateItemCategoryCommand, *,
                         actor=None) -> ItemCategory:
    """Edit a category. Moving it moves its sub-categories too (the tree stays at most
    MAX_CATEGORY_DEPTH deep); its family and tracking are fixed once it has stock."""
    from apps.inventory.models import Item, StockLot

    if actor is not None:
        actor.require("catalog.category.manage")
    user = getattr(actor, "user", None)
    with transaction.atomic():
        category = ItemCategory.objects.select_for_update().filter(pk=category_id).first()
        if category is None:
            raise ValidationError(_("Unknown category."))
        descendants = _subtree(category)

        parent = None
        if cmd.parent_id is not None:
            parent = ItemCategory.objects.filter(pk=cmd.parent_id).first()
            if parent is None:
                raise ValidationError(_("Unknown parent category."),
                                      fields={"parent_id": [_("Unknown category.")]})
            if parent.pk == category.pk or parent.pk in {c.pk for c in descendants}:
                raise ValidationError(_("A category cannot be placed under itself."),
                                      fields={"parent_id": [_("Choose another category.")]})
        depth = (parent.depth + 1) if parent else 1
        height = max((c.depth for c in descendants), default=category.depth) - category.depth
        if depth + height > MAX_CATEGORY_DEPTH:
            raise ValidationError(
                _("Categories are at most %(depth)d levels deep.") % {"depth": MAX_CATEGORY_DEPTH},
                code="CATALOG_CATEGORY_TOO_DEEP", fields={"parent_id": [_("Too deep.")]})

        changes_kind = (cmd.tracking != category.tracking
                        or cmd.product_family != category.product_family)
        if changes_kind and (Item.objects.filter(category=category).exists()
                             or StockLot.objects.filter(category=category).exists()):
            raise ValidationError(
                _("This category already has stock, so its family and tracking cannot change."),
                code="CATALOG_CATEGORY_IN_USE",
                fields={"tracking": [_("Fixed: the category has stock.")]})

        default_karat = None
        if cmd.default_karat_id is not None:
            default_karat = Karat.objects.filter(pk=cmd.default_karat_id).first()
            if default_karat is None:
                raise ValidationError(_("Unknown karat."),
                                      fields={"default_karat_id": [_("Unknown karat.")]})
        commission_rate = quantize(cmd.commission_rate, RATE)
        if not Decimal(0) <= commission_rate <= Decimal(1):
            raise ValidationError(_("Commission rate is a fraction between 0 and 1."),
                                  fields={"commission_rate": [_("Out of range.")]})

        shift = depth - category.depth
        category.code, category.name = cmd.code.strip(), cmd.name.strip()
        category.short_name, category.parent, category.depth = cmd.short_name, parent, depth
        if cmd.product_family != category.product_family:
            _check_family(cmd.product_family)
        category.product_family, category.tracking = cmd.product_family, cmd.tracking
        category.default_karat, category.barcode_prefix = default_karat, cmd.barcode_prefix
        category.commission_rate, category.is_active = commission_rate, cmd.is_active
        category.updated_by = user
        try:
            category.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError(_("Invalid category."), fields=exc.message_dict) from exc
        category.save()
        if shift:
            for child in descendants:
                child.depth += shift
                child.save(update_fields=["depth", "updated_at"])
        _set_making_charges(category, cmd.making_charges)
        return category


@dataclass(frozen=True)
class KaratCommand:
    fineness: Decimal | str
    display_name: str = ""
    is_active: bool = True


def _fineness(value) -> Decimal:
    fineness = quantize(value, Decimal("0.001"))
    if not Decimal(0) < fineness <= Decimal(1000):
        raise ValidationError(_("Fineness is between 0 and 1000 (‰)."),
                              fields={"fineness": [_("Out of range.")]})
    return fineness


def create_karat(metal_code: str, code: int, cmd: KaratCommand, *, actor=None) -> Karat:
    if actor is not None:
        actor.require("catalog.karat.manage")
    metal = Metal.objects.filter(code=metal_code).first()
    if metal is None:
        raise ValidationError(_("Unknown metal."), fields={"metal": [_("Unknown metal.")]})
    if Karat.objects.filter(metal=metal, code=code).exists():
        raise ValidationError(_("This karat already exists."),
                              fields={"code": [_("Already exists.")]})
    return Karat.objects.create(metal=metal, code=code, fineness=_fineness(cmd.fineness),
                                display_name=cmd.display_name.strip(), is_active=cmd.is_active)


def update_karat(karat_id: int, cmd: KaratCommand, *, actor=None) -> Karat:
    """Fineness applies to documents from now on; posted documents keep their weights."""
    if actor is not None:
        actor.require("catalog.karat.manage")
    karat = Karat.objects.select_related("metal").filter(pk=karat_id).first()
    if karat is None:
        raise ValidationError(_("Unknown karat."))
    if karat.is_reference and not cmd.is_active:
        raise ValidationError(_("The reference karat cannot be deactivated."),
                              fields={"is_active": [_("This is the reference karat.")]})
    karat.fineness = _fineness(cmd.fineness)
    karat.display_name = cmd.display_name.strip()
    karat.is_active = cmd.is_active
    karat.save(update_fields=["fineness", "display_name", "is_active", "updated_at"])
    return karat
