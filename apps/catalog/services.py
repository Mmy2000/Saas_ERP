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

        currencies = {c.code: c for c in Currency.objects.filter(
            code__in=[m.currency_code for m in cmd.making_charges])}
        for charge in cmd.making_charges:
            currency = currencies.get(charge.currency_code)
            if currency is None:
                raise ValidationError(_("Unknown currency %(code)s.")
                                      % {"code": charge.currency_code},
                                      fields={"making_charges": [_("Unknown currency.")]})
            CategoryMakingCharge.objects.create(
                category=category, currency=currency,
                cost_rate_per_g=quantize(charge.cost_rate_per_g, UNIT_PRICE),
                list_rate_per_g=quantize(charge.list_rate_per_g, UNIT_PRICE),
            )
        return category
