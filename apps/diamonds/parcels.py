"""Parcels of small stones kept by weight (legacy "برتيتات": Ft1, GHZ11, GHZ16, GHZ71, and the
parcels the Install screens handed to setters).

A parcel holds carats, a count of stones and their cost; the average cost per carat is what
leaves it. Stones come in by purchase, and go out by sale or into a piece when stones are set:

    bought   Dr diamonds and stones (the price)      Cr the supplier / cash box / bank
    sold     Dr cash box / bank / the customer       Cr diamond sales
             Dr cost of diamonds sold (avg cost)     Cr diamonds and stones
    set      the cost moves onto the piece (the same account: nothing is booked)

Each change is a ParcelMovement, so a cancelled sale or setting puts back exactly what left.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, quantize, round_money
from apps.core.sequences import allocate_number
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
from apps.treasury.holders import ensure_cash_available, resolve_tender

from .models import (
    ParcelDeal,
    ParcelMovement,
    ParcelMovementKind,
    StoneKind,
    StoneParcel,
    StoneShape,
)
from .services import require_enabled

DEAL_DOC = "diamonds.ParcelDeal"
CARAT = Decimal("0.001")
ZERO = Decimal(0)
PAYMENTS = ("cash", "bank_transfer", "account")


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def _amount(value, step, field: str, *, positive: bool = True) -> Decimal:
    try:
        number = quantize(value if value not in (None, "") else "0", step)
    except (TypeError, ValueError, InvalidOperation):
        raise _error(field, _("Enter a number.")) from None
    if number < 0 or (positive and number <= 0):
        raise _error(field, _("Must be greater than zero."))
    return number


# --- parcels ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class ParcelInput:
    code: str
    branch_id: int
    kind: str = StoneKind.DIAMOND
    shape: str = ""
    size: str = ""
    color: str = ""
    clarity: str = ""
    note: str = ""
    sale_price_per_ct: Decimal | str | None = None
    is_active: bool = True


def save_parcel(data: ParcelInput, *, parcel_id: int | None = None, actor=None) -> StoneParcel:
    require_enabled()
    with transaction.atomic():
        parcel = (StoneParcel.objects.select_for_update().filter(pk=parcel_id).first()
                  if parcel_id else StoneParcel())
        if parcel is None:
            raise NotFound(_("Not found."))
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("diamonds.manage", branch=branch)
        if parcel.pk and parcel.branch_id != branch.pk and (parcel.carats or parcel.count):
            raise _error("branch", _("A parcel with stones in it stays in its branch."))
        code = data.code.strip()[:20]
        if not code:
            raise _error("code", _("This field is required."))
        if StoneParcel.objects.filter(code=code).exclude(pk=parcel.pk).exists():
            raise _error("code", _("Another parcel has this code."))
        if data.kind not in StoneKind.values:
            raise _error("kind", _("Choose the kind of stone."))
        if data.shape and data.shape not in StoneShape.values:
            raise _error("shape", _("Choose the shape."))
        parcel.code, parcel.branch, parcel.kind, parcel.shape = code, branch, data.kind, data.shape
        parcel.size, parcel.color = data.size.strip()[:40], data.color.strip()[:20]
        parcel.clarity, parcel.note = data.clarity.strip()[:20], data.note.strip()[:200]
        parcel.sale_price_per_ct = (_amount(data.sale_price_per_ct, MONEY, "sale_price_per_ct")
                                    if data.sale_price_per_ct not in (None, "") else None)
        parcel.is_active = data.is_active
        parcel.save()
    return parcel


def _move(parcel: StoneParcel, kind: str, carats: Decimal, count: int, cost: Decimal, *,
          doc_type: str, doc_id: int, day) -> None:
    parcel.carats += carats
    parcel.count += count
    parcel.cost_amount += cost
    if parcel.carats < 0 or parcel.count < 0 or parcel.cost_amount < 0:
        raise DomainError(_("Parcel %(code)s has only %(carats)s ct (%(count)s stones).") % {
            "code": parcel.code, "carats": parcel.carats - carats, "count": parcel.count - count},
            code="DIAMONDS_PARCEL_SHORT")
    parcel.save(update_fields=["carats", "count", "cost_amount", "updated_at"])
    ParcelMovement.objects.create(parcel=parcel, kind=kind, carats=carats, count=count,
                                  cost_amount=cost, document_type=doc_type, document_id=doc_id,
                                  business_date=day)


def _cost_of(parcel: StoneParcel, carats: Decimal) -> Decimal:
    """What `carats` of the parcel cost, at its average (all of it when emptied)."""
    if carats >= parcel.carats:
        return parcel.cost_amount
    return round_money(parcel.cost_amount * carats / parcel.carats) if parcel.carats else ZERO


# --- used in a piece --------------------------------------------------------------------------

@dataclass(frozen=True)
class ParcelUse:
    parcel_id: int
    carats: Decimal | str
    count: int = 0
    held: bool = False  # from the setter's own stones of this parcel, not the shop's


@dataclass
class PricedUse:
    parcel: StoneParcel
    carats: Decimal
    count: int
    cost: Decimal
    holding: object = None  # the setter's WorkshopStones, when the stones were with them

    def stone(self) -> dict:
        """The stone row it becomes on the piece."""
        return {"kind": self.parcel.kind, "shape": self.parcel.shape,
                "count": max(self.count, 1), "carat": str(self.carats),
                "color": self.parcel.color, "clarity": self.parcel.clarity,
                "note": _("From parcel %(code)s") % {"code": self.parcel.code}}


def price_uses(uses, branch, setter=None) -> list[PricedUse]:
    """Lock and check the parcels stones are taken from, at their average cost; or, for stones
    the setter already holds, their holding at its average cost."""
    from .handouts import held_cost, holding_for

    priced = []
    for use in uses:
        field = "parcels"
        parcel = StoneParcel.objects.select_for_update().filter(pk=use.parcel_id).first()
        if parcel is None or parcel.branch_id != branch.pk or not (parcel.is_active or use.held):
            raise _error(field, _("Choose a parcel of this branch."))
        carats = _amount(use.carats, CARAT, field)
        count = int(use.count or 0)
        if count < 0:
            raise _error(field, _("Must not be negative."))
        if use.held:
            held = holding_for(setter, parcel) if setter is not None else None
            if held is None or carats > held.carats or count > held.count:
                raise _error(field, _("The setter holds only %(carats)s ct (%(count)s stones) of "
                                      "parcel %(code)s.") % {
                    "carats": held.carats if held else ZERO, "count": held.count if held else 0,
                    "code": parcel.code})
            priced.append(PricedUse(parcel, carats, count, held_cost(held, carats), held))
            continue
        if carats > parcel.carats or count > parcel.count:
            raise _error(field, _("Parcel %(code)s has only %(carats)s ct (%(count)s stones).")
                         % {"code": parcel.code, "carats": parcel.carats, "count": parcel.count})
        priced.append(PricedUse(parcel, carats, count, _cost_of(parcel, carats)))
    return priced


def take(priced: list[PricedUse], *, doc_type: str, doc_id: int, day) -> None:
    from .handouts import move_held

    for use in priced:
        if use.holding is not None:
            move_held(use.holding, ParcelMovementKind.SET, -use.carats, -use.count, -use.cost,
                      doc_type=doc_type, doc_id=doc_id, day=day)
        else:
            _move(use.parcel, ParcelMovementKind.SET, -use.carats, -use.count, -use.cost,
                  doc_type=doc_type, doc_id=doc_id, day=day)


def give_back(*, doc_type: str, doc_id: int, day) -> None:
    """A cancelled setting: what it took from parcels (or a setter's holding) goes back."""
    from .handouts import restore_held

    restore_held(doc_type=doc_type, doc_id=doc_id, day=day)
    for move in ParcelMovement.objects.filter(document_type=doc_type, document_id=doc_id,
                                              kind=ParcelMovementKind.SET).order_by("pk"):
        parcel = StoneParcel.objects.select_for_update().get(pk=move.parcel_id)
        _move(parcel, ParcelMovementKind.UNSET, -move.carats, -move.count, -move.cost_amount,
              doc_type=doc_type, doc_id=doc_id, day=day)


# --- bought and sold --------------------------------------------------------------------------

@dataclass(frozen=True)
class DealInput:
    parcel_id: int
    kind: str  # buy | sell
    carats: Decimal | str
    price_per_ct: Decimal | str
    payment: str
    count: int = 0
    party_id: int | None = None  # the supplier (buying) or the customer (selling)
    party_name: str = ""  # a walk-in buyer
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    note: str = ""


def deal(data: DealInput, *, actor=None) -> ParcelDeal:
    require_enabled()
    if data.kind not in ("buy", "sell"):
        raise _error("kind", _("Choose buy or sell."))
    buying = data.kind == "buy"
    with transaction.atomic():
        parcel = (StoneParcel.objects.select_for_update().select_related("branch")
                  .filter(pk=data.parcel_id, is_active=True).first())
        if parcel is None:
            raise NotFound(_("Not found."))
        branch = parcel.branch
        if actor is not None:
            actor.require("diamonds.receive" if buying else "diamonds.sell", branch=branch)
        carats = _amount(data.carats, CARAT, "carats")
        count = int(data.count or 0)
        if count < 0:
            raise _error("count", _("Must not be negative."))
        price = _amount(data.price_per_ct, MONEY, "price_per_ct")
        amount = round_money(carats * price)
        role = PartyRoleType.SUPPLIER if buying else PartyRoleType.CUSTOMER
        party = None
        if data.party_id:
            party = Party.objects.filter(pk=data.party_id, is_active=True, roles__role=role).first()
            if party is None:
                raise _error("party", _("Choose an active supplier.") if buying
                             else _("Choose an active customer."))
        elif buying:
            raise _error("party", _("Choose the supplier."))
        elif not data.party_name.strip():
            raise _error("party_name", _("Write down who is buying."))
        if not buying and (carats > parcel.carats or count > parcel.count):
            raise _error("carats", _("Parcel %(code)s has only %(carats)s ct (%(count)s stones).")
                         % {"code": parcel.code, "carats": parcel.carats, "count": parcel.count})

        home = functional_commodity()
        if data.payment not in PAYMENTS:
            raise _error("payment", _("Choose how it is paid."))
        holder_fields, money_account, money_party = {}, None, None
        if data.payment == "account":
            if party is None:
                raise _error("payment", _("Only a registered customer can buy on account."))
            money_account = account_for("suppliers" if buying else "customers")
            money_party = party
        else:
            holder = resolve_tender(data.payment, branch, home.currency,
                                    cash_box_id=data.cash_box_id,
                                    bank_account_id=data.bank_account_id)
            if buying and holder.cash_box is not None:
                ensure_cash_available(holder.cash_box, amount, "payment")
            money_account = holder.account
            holder_fields = {"cash_box": holder.cash_box, "bank_account": holder.bank_account}

        today = timezone.localdate()
        cost = amount if buying else _cost_of(parcel, carats)
        made = ParcelDeal.objects.create(
            branch=branch, business_date=today, parcel=parcel, kind=data.kind, party=party,
            party_name="" if party else data.party_name.strip()[:200], payment=data.payment,
            carats=carats, count=count, price_per_ct=price, amount=amount, cost_amount=cost,
            note=data.note.strip(), created_by=getattr(actor, "user", None), **holder_fields)
        sign = 1 if buying else -1
        _move(parcel, ParcelMovementKind.BUY if buying else ParcelMovementKind.SELL,
              sign * carats, sign * count, sign * cost, doc_type=DEAL_DOC, doc_id=made.pk,
              day=today)
        diamonds = account_for("inventory_diamonds")
        if buying:
            lines = [LineInput(account=diamonds, commodity=home, quantity=amount),
                     LineInput(account=money_account, commodity=home, quantity=-amount,
                               party=money_party)]
        else:
            lines = [LineInput(account=money_account, commodity=home, quantity=amount,
                               party=money_party),
                     LineInput(account=account_for("sales_diamonds"), commodity=home,
                               quantity=-amount)]
            if cost:
                lines += [LineInput(account=account_for("cogs_diamonds"), commodity=home,
                                    quantity=cost),
                          LineInput(account=diamonds, commodity=home, quantity=-cost)]
        made.number = allocate_number("PB" if buying else "PS", branch=branch,
                                      fiscal_year=today.year)
        made.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=DEAL_DOC,
            source_id=made.pk, lines=lines,
            memo=(_("Stones bought into %(code)s") if buying
                  else _("Stones sold from %(code)s")) % {"code": parcel.code})
        made.status = DocStatus.POSTED
        made.posted_at = timezone.now()
        made.posted_by = getattr(actor, "user", None)
        made.save()
    return made


def void_deal(deal_id: int, *, reason: str = "", actor=None) -> ParcelDeal:
    require_enabled()
    with transaction.atomic():
        made = (ParcelDeal.objects.select_for_update(of=("self",)).select_related("branch")
                .filter(pk=deal_id).first())
        if made is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.receive" if made.kind == "buy" else "diamonds.sell",
                          branch=made.branch)
        if made.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        parcel = StoneParcel.objects.select_for_update().get(pk=made.parcel_id)
        sign = -1 if made.kind == "buy" else 1
        _move(parcel, ParcelMovementKind.VOID, sign * made.carats, sign * made.count,
              sign * made.cost_amount, doc_type=DEAL_DOC, doc_id=made.pk, day=today)
        if made.journal_entry_id:
            reverse_entry(made.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": made.number})
        made.status = DocStatus.VOIDED
        made.voided_at = timezone.now()
        made.voided_by = getattr(actor, "user", None)
        made.void_reason = reason.strip()[:300]
        made.save()
    return made
