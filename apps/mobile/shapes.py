"""This platform's records in the field names of Gweb's API, which the mobile app reads:

    mco      the piece's barcode (a number when it is all digits, as Gweb's column was)
    com_mco  the supplier's own code               idis / name   the piece's description
    co       the karat: 14 … 24, silver = 1         gco / gdis    the category
    br/brna  a branch's code and name               ssco / ssdis  the category's top group
    xo1      a stocktake's number                   go_cr         the gross weight (g)
"""

from __future__ import annotations

from decimal import Decimal

from apps.catalog.models import MetalCode

ZERO = Decimal(0)
# The karats the app knows; any other piece is left out of its lists (as Gweb's API did).
APP_KARATS = (1, 14, 18, 21, 22, 24)
EIGHTEEN = Decimal("0.750")


def karat_code(karat) -> int:
    """Gweb's `co`: the gold karat, 1 for silver, 0 when none (the app needs a number)."""
    if karat is None:
        return 0
    if karat.metal.code == MetalCode.SILVER:
        return 1
    return int(karat.code)


def karats_for(code: int):
    """The platform's karats behind one of Gweb's codes."""
    from apps.catalog.models import Karat

    karats = Karat.objects.select_related("metal")
    if code == 1:
        return karats.filter(metal__code=MetalCode.SILVER)
    return karats.filter(metal__code=MetalCode.GOLD, code=code)


def mco(barcode: str):
    return int(barcode) if barcode.isdigit() and len(barcode) < 19 else barcode


def money(value) -> str:
    return f"{Decimal(value or 0):.2f}"


def grams(value) -> str:
    return f"{Decimal(value or 0):.3f}"


def top_group(category):
    """The category's top-level group (Gweb's main group, Su)."""
    while category is not None and category.parent_id is not None:
        category = category.parent
    return category


# Gweb's access_* flags, from this platform's permissions. The app only shows them; the server
# decides what each request may do.
FLAGS = {
    "access_stores": "inventory.stock.view",
    "access_cost": "inventory.item.view_cost",
    "access_ghu11": "inventory.stocktake.count",
    "access_strip_gold": "inventory.stocktake.count",
    "access_gold_sell": "sales.invoice.create",
    "access_gold_buy": "purchasing.invoice.view",
    "access_client": "parties.customer.view",
    "access_cash": "treasury.view",
    "access_dashboard": "reports.daily_summary.view",
    "access_systemcodes": "catalog.view",
    "access_administration": "admin.users.manage",
}


def flags(actor) -> list[str]:
    return [flag for flag, code in FLAGS.items() if actor.can(code)]


def user(membership, actor) -> dict:
    from .tokens import is_owner

    account = membership.user
    first, _sep, last = (account.display_name or "").partition(" ")
    branch = membership.default_branch
    return {
        "id": account.pk, "username": membership.username, "first_name": first,
        "last_name": last, "email": account.email or "",
        "usercode": membership.legacy_usercode or membership.pk,
        "branch": branch.code if branch is not None else None,
        "branch_name": branch.name if branch is not None else "",
        "is_staff": is_owner(membership), "is_active": membership.is_active,
        "last_login": account.last_login.isoformat() if account.last_login else None,
        "permissions": flags(actor),
    }


def piece(item, *, show_cost: bool) -> dict:
    """An inventory piece (Gweb InventoryPieceSerializer)."""
    group = top_group(item.category)
    data = {
        "id": item.pk, "mco": mco(item.barcode), "com_mco": item.external_code or "",
        "name": item.note or item.category.name, "karat": karat_code(item.karat),
        "weight": grams(item.gross_weight_g), "pieces": 1, "category": item.category.name,
        "main_group": group.name if group else "", "branch": item.branch.code,
        "branch_name": item.branch.name, "updated_at": item.updated_at.date().isoformat(),
    }
    if show_cost:
        data.update({
            "unit_value": float(item.cost_amount), "tot_cr": money(item.cost_amount),
            "prc": money(item.cost_making_rate), "prcus": money(0),
            "sal_pr": money(item.label_price if item.label_price is not None
                            else item.list_making_rate)})
    return data


def stocktake_piece(line, *, show_cost: bool) -> dict:
    """A piece on a stocktake (Gweb InventoryItemSerializer)."""
    item = line.item
    fine = item.fine_weight_g if item is not None else ZERO
    data = {
        "id": item.pk if item is not None else line.pk,
        "mco": mco(line.barcode), "com_mco": (item.external_code if item else "") or "",
        "gdis": item.category.name if item is not None else "",
        "co": karat_code(item.karat) if item is not None else 0,
        "counted": line.result == "counted",
        "go_cr": grams(item.gross_weight_g if item is not None else 0),
        "go18_cr": grams(fine / EIGHTEEN),
    }
    if show_cost and item is not None:
        weight = item.gross_weight_g or Decimal(1)
        data.update({
            "tot_pg": money(item.cost_amount / weight), "tot_cr": money(item.cost_amount),
            "totus_pg": money(0), "totus_cr": money(0), "prc": money(item.cost_making_rate),
            "prcus": money(0), "sal_pr": money(item.label_price if item.label_price is not None
                                               else item.list_making_rate)})
    return data
