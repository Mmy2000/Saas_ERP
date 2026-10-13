"""The photo catalogue (legacy GHMRK2 كتالوج الذهب, GHMRK4 كتالوج الماس): pieces in stock with
their photo (or their category's) and today's price, filtered by branch, category, karat,
weight and price, and pieces still without a photo. Printed as an A5 PDF to show customers.

Gold pieces are priced like the sales screen before any discount or offer: today's selling
price for the karat plus the piece's making charge (its own, else its category's), times the
weight. Diamond pieces and loose stones show their label price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as FormValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.media import validate_image
from apps.core.numeric import round_money

from .models import Item, ItemStatus

ZERO = Decimal(0)
FILTERS = ("branch", "category", "karat", "w_min", "w_max", "p_min", "p_max", "photos")
MAX_PRICED = 3000  # pieces priced at once when filtering by price
LABEL_FAMILIES = ("diamond", "stone")


@dataclass
class Entry:
    item: Item
    price: Decimal | None
    photo_url: str


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value) if value else None
    except (InvalidOperation, ValueError):
        return None


def photo_url(item: Item) -> str:
    if item.photo:
        return item.photo.url
    category = item.category
    return category.photo.url if category.photo else ""


class Pricer:
    """Today's catalogue price of pieces, with one query for the board and one for the
    categories' making charges."""

    def __init__(self):
        from apps.catalog.models import CategoryMakingCharge
        from apps.pricing.selectors import current_price_board, functional_currency

        self.board = current_price_board()
        home = functional_currency()
        self.making = dict(CategoryMakingCharge.objects.filter(currency__code=home)
                           .values_list("category_id", "list_rate_per_g"))

    def __call__(self, item: Item) -> Decimal | None:
        if item.category.product_family in LABEL_FAMILIES:
            return item.label_price
        if self.board is None or item.karat_id is None:
            return None
        try:
            metal = self.board.price(item.karat)
        except DomainError:  # a karat not on today's board: no price shown
            return None
        making = item.list_making_rate or self.making.get(item.category_id) or ZERO
        return round_money((metal + making) * item.gross_weight_g)


def pieces(params, *, branch_ids=None):
    """(entries priced, filters, priced at all) for the catalogue's filters."""
    filters = {key: str(params.get(key, "") or "").strip() for key in FILTERS}
    items = (Item.objects.filter(status=ItemStatus.IN_STOCK)
             .select_related("category", "karat__metal", "branch"))
    if branch_ids is not None:
        items = items.filter(branch_id__in=branch_ids)
    for key in ("branch", "category", "karat"):
        if filters[key].isdigit():
            items = items.filter(**{f"{key}_id": int(filters[key])})
    low, high = _decimal(filters["w_min"]), _decimal(filters["w_max"])
    if low is not None:
        items = items.filter(gross_weight_g__gte=low)
    if high is not None:
        items = items.filter(gross_weight_g__lte=high)
    with_photo = ~Q(photo="") | ~Q(category__photo="")
    if filters["photos"] == "with":
        items = items.filter(with_photo)
    elif filters["photos"] == "without":
        items = items.exclude(with_photo)
    items = items.order_by("category__code", "karat__code", "gross_weight_g", "barcode")
    price_low, price_high = _decimal(filters["p_min"]), _decimal(filters["p_max"])
    return items, filters, (price_low, price_high)


def priced(items, price_range, *, limit: int | None = None) -> list[Entry]:
    """Price the pieces; with a price range, only those inside it (up to MAX_PRICED looked at)."""
    low, high = price_range
    price = Pricer()
    entries = []
    source = items[:MAX_PRICED] if (low is not None or high is not None) else (
        items[:limit] if limit else items)
    for item in source:
        value = price(item)
        if low is not None and (value is None or value < low):
            continue
        if high is not None and (value is None or value > high):
            continue
        entries.append(Entry(item, value, photo_url(item)))
        if limit and len(entries) >= limit:
            break
    return entries


# --- photos ----------------------------------------------------------------------------------

def _check(upload) -> None:
    try:
        validate_image(upload)
    except FormValidationError as exc:
        message = " ".join(exc.messages)
        raise ValidationError(message, fields={"photo": [message]}) from None
    if upload.name.lower().endswith(".ico"):
        message = _("Upload a PNG, JPEG, WEBP or GIF photo.")
        raise ValidationError(message, fields={"photo": [message]})


def set_photo(target, upload, *, actor=None, branch=None):
    """Replace (or with `upload` None, remove) a piece's or a category's photo."""
    if actor is not None:
        actor.require("inventory.item.photo", branch=branch)
    if upload is not None:
        _check(upload)
    with transaction.atomic():
        old = target.photo.name if target.photo else ""
        if upload is None:
            target.photo = ""
        else:
            target.photo.save(upload.name, upload, save=False)
        target.save(update_fields=["photo", "updated_at"])
    if old and old != (target.photo.name if target.photo else ""):
        target.photo.storage.delete(old)
    return target


def item_for_photo(item_id: int) -> Item:
    item = Item.objects.select_related("branch").filter(pk=item_id).first()
    if item is None:
        raise NotFound(_("Not found."))
    return item
