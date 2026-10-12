"""Parcels of small stones kept by weight (legacy "برتيتات": Ft1, GHZ11, GHZ16, GHZ71, and the
parcels the Install screens handed to setters).

A parcel holds carats, a count of stones and their cost; the average cost per carat is what
leaves it. Stones come in by purchase, and go out by sale or into a piece when stones are set:

    bought   Dr diamonds and stones (the price)      Cr the supplier / cash box / bank
    sold     Dr cash box / bank / the customer       Cr diamond sales
             Dr cost of diamonds sold (avg cost)     Cr diamonds and stones
    set      the cost moves onto the piece (the same account: nothing is booked)
    returned Dr the supplier / cash box / bank       Cr diamonds and stones (avg cost)
             an agreed price other than the cost goes to cost of diamonds sold
    moved    Dr branch clearing (sending branch)     Cr diamonds and stones (avg cost)
             Dr diamonds and stones (receiving)      Cr branch clearing (receiving branch)
    counted  stones missing  Dr stone losses         Cr diamonds and stones (avg cost)
             stones found    the mirror image

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
    ParcelCount,
    ParcelCountLine,
    ParcelDeal,
    ParcelMovement,
    ParcelMovementKind,
    ParcelTransfer,
    StoneKind,
    StoneParcel,
    StoneShape,
)
from .services import require_enabled

DEAL_DOC = "diamonds.ParcelDeal"
TRANSFER_DOC = "diamonds.ParcelTransfer"
COUNT_DOC = "diamonds.ParcelCount"
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
    kind: str  # buy | sell | return (to the supplier)
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
    if data.kind not in ("buy", "sell", "return"):
        raise _error("kind", _("Choose buy or sell."))
    buying, returning = data.kind == "buy", data.kind == "return"
    from_supplier = buying or returning
    with transaction.atomic():
        parcel = (StoneParcel.objects.select_for_update().select_related("branch")
                  .filter(pk=data.parcel_id, is_active=True).first())
        if parcel is None:
            raise NotFound(_("Not found."))
        branch = parcel.branch
        if actor is not None:
            actor.require("diamonds.receive" if from_supplier else "diamonds.sell",
                          branch=branch)
        carats = _amount(data.carats, CARAT, "carats")
        count = int(data.count or 0)
        if count < 0:
            raise _error("count", _("Must not be negative."))
        if returning and data.price_per_ct in (None, ""):  # back at what it cost
            price = None
        else:
            price = _amount(data.price_per_ct, MONEY, "price_per_ct")
        role = PartyRoleType.SUPPLIER if from_supplier else PartyRoleType.CUSTOMER
        party = None
        if data.party_id:
            party = Party.objects.filter(pk=data.party_id, is_active=True, roles__role=role).first()
            if party is None:
                raise _error("party", _("Choose an active supplier.") if from_supplier
                             else _("Choose an active customer."))
        elif from_supplier:
            raise _error("party", _("Choose the supplier."))
        elif not data.party_name.strip():
            raise _error("party_name", _("Write down who is buying."))
        if not buying and (carats > parcel.carats or count > parcel.count):
            raise _error("carats", _("Parcel %(code)s has only %(carats)s ct (%(count)s stones).")
                         % {"code": parcel.code, "carats": parcel.carats, "count": parcel.count})

        cost = None if buying else _cost_of(parcel, carats)
        if price is None:
            amount = cost
            price = round_money(cost / carats)
        else:
            amount = round_money(carats * price)
        if buying:
            cost = amount

        home = functional_commodity()
        if data.payment not in PAYMENTS:
            raise _error("payment", _("Choose how it is paid."))
        holder_fields, money_account, money_party = {}, None, None
        if data.payment == "account":
            if party is None:
                raise _error("payment", _("Only a registered customer can buy on account."))
            money_account = account_for("suppliers" if from_supplier else "customers")
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
        made = ParcelDeal.objects.create(
            branch=branch, business_date=today, parcel=parcel, kind=data.kind, party=party,
            party_name="" if party else data.party_name.strip()[:200], payment=data.payment,
            carats=carats, count=count, price_per_ct=price, amount=amount, cost_amount=cost,
            note=data.note.strip(), created_by=getattr(actor, "user", None), **holder_fields)
        sign = 1 if buying else -1
        _move(parcel, {"buy": ParcelMovementKind.BUY, "sell": ParcelMovementKind.SELL,
                       "return": ParcelMovementKind.RETURN}[data.kind],
              sign * carats, sign * count, sign * cost, doc_type=DEAL_DOC, doc_id=made.pk,
              day=today)
        diamonds = account_for("inventory_diamonds")
        if buying:
            lines = [LineInput(account=diamonds, commodity=home, quantity=amount),
                     LineInput(account=money_account, commodity=home, quantity=-amount,
                               party=money_party)]
        elif returning:
            lines = [LineInput(account=money_account, commodity=home, quantity=amount,
                               party=money_party),
                     LineInput(account=diamonds, commodity=home, quantity=-cost)]
            if amount != cost:
                lines.append(LineInput(account=account_for("cogs_diamonds"), commodity=home,
                                       quantity=cost - amount))
        else:
            lines = [LineInput(account=money_account, commodity=home, quantity=amount,
                               party=money_party),
                     LineInput(account=account_for("sales_diamonds"), commodity=home,
                               quantity=-amount)]
            if cost:
                lines += [LineInput(account=account_for("cogs_diamonds"), commodity=home,
                                    quantity=cost),
                          LineInput(account=diamonds, commodity=home, quantity=-cost)]
        made.number = allocate_number({"buy": "PB", "sell": "PS", "return": "PX"}[data.kind],
                                      branch=branch, fiscal_year=today.year)
        made.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=DEAL_DOC,
            source_id=made.pk, lines=lines,
            memo={"buy": _("Stones bought into %(code)s"),
                  "sell": _("Stones sold from %(code)s"),
                  "return": _("Stones of %(code)s returned to the supplier")}[data.kind]
            % {"code": parcel.code})
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
            actor.require("diamonds.sell" if made.kind == "sell" else "diamonds.receive",
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


# --- moved to another branch ------------------------------------------------------------------

def _parcel_for(branch: Branch, like: StoneParcel) -> StoneParcel:
    """A new parcel at `branch` with the same description as `like`."""
    base = f"{like.code}-{branch.code}"[:17]
    code, n = base, 1
    while StoneParcel.objects.filter(code=code).exists():
        n += 1
        code = f"{base}-{n}"
    return StoneParcel.objects.create(
        code=code, branch=branch, kind=like.kind, shape=like.shape, size=like.size,
        color=like.color, clarity=like.clarity, note=like.note,
        sale_price_per_ct=like.sale_price_per_ct)


def transfer_parcel(parcel_id: int, *, to_branch_id: int, carats, count: int = 0,
                    to_parcel_id: int | None = None, note: str = "",
                    actor=None) -> ParcelTransfer:
    require_enabled()
    with transaction.atomic():
        source = (StoneParcel.objects.select_for_update().select_related("branch")
                  .filter(pk=parcel_id, is_active=True).first())
        if source is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.manage", branch=source.branch)
        to_branch = Branch.objects.filter(pk=to_branch_id, is_active=True).exclude(
            pk=source.branch_id).first()
        if to_branch is None:
            raise _error("to_branch", _("Choose another branch."))
        carats = _amount(carats, CARAT, "carats")
        count = int(count or 0)
        if count < 0:
            raise _error("count", _("Must not be negative."))
        if carats > source.carats or count > source.count:
            raise _error("carats", _("Parcel %(code)s has only %(carats)s ct (%(count)s stones).")
                         % {"code": source.code, "carats": source.carats,
                            "count": source.count})
        if to_parcel_id:
            target = (StoneParcel.objects.select_for_update()
                      .filter(pk=to_parcel_id, branch=to_branch, is_active=True).first())
            if target is None:
                raise _error("to_parcel", _("Choose a parcel of that branch."))
            if target.kind != source.kind:
                raise _error("to_parcel", _("That parcel holds another kind of stone."))
        else:
            target = _parcel_for(to_branch, source)
        today = timezone.localdate()
        cost = _cost_of(source, carats)
        made = ParcelTransfer.objects.create(
            branch=source.branch, business_date=today, from_parcel=source, to_parcel=target,
            carats=carats, count=count, cost_amount=cost, note=note.strip(),
            created_by=getattr(actor, "user", None))
        _move(source, ParcelMovementKind.MOVE_OUT, -carats, -count, -cost,
              doc_type=TRANSFER_DOC, doc_id=made.pk, day=today)
        _move(target, ParcelMovementKind.MOVE_IN, carats, count, cost,
              doc_type=TRANSFER_DOC, doc_id=made.pk, day=today)
        made.number = allocate_number("PT", branch=source.branch, fiscal_year=today.year)
        if cost:
            home, clearing = functional_commodity(), account_for("branch_clearing")
            diamonds = account_for("inventory_diamonds")
            made.journal_entry = post_entry(
                branch=source.branch, business_date=today, kind=EntryKind.AUTO,
                source_type=TRANSFER_DOC, source_id=made.pk,
                memo=_("Stones of %(code)s moved to %(branch)s") % {
                    "code": source.code, "branch": to_branch.name},
                lines=[LineInput(account=clearing, commodity=home, quantity=cost,
                                 branch=source.branch),
                       LineInput(account=diamonds, commodity=home, quantity=-cost,
                                 branch=source.branch),
                       LineInput(account=diamonds, commodity=home, quantity=cost,
                                 branch=to_branch),
                       LineInput(account=clearing, commodity=home, quantity=-cost,
                                 branch=to_branch)])
        made.status, made.posted_at = DocStatus.POSTED, timezone.now()
        made.posted_by = getattr(actor, "user", None)
        made.save()
    return made


def void_transfer(transfer_id: int, *, reason: str = "", actor=None) -> ParcelTransfer:
    require_enabled()
    with transaction.atomic():
        made = (ParcelTransfer.objects.select_for_update(of=("self",)).select_related("branch")
                .filter(pk=transfer_id).first())
        if made is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.manage", branch=made.branch)
        if made.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        target = StoneParcel.objects.select_for_update().get(pk=made.to_parcel_id)
        source = StoneParcel.objects.select_for_update().get(pk=made.from_parcel_id)
        _move(target, ParcelMovementKind.VOID, -made.carats, -made.count, -made.cost_amount,
              doc_type=TRANSFER_DOC, doc_id=made.pk, day=today)
        _move(source, ParcelMovementKind.VOID, made.carats, made.count, made.cost_amount,
              doc_type=TRANSFER_DOC, doc_id=made.pk, day=today)
        if made.journal_entry_id:
            reverse_entry(made.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": made.number})
        made.status, made.voided_at = DocStatus.VOIDED, timezone.now()
        made.voided_by, made.void_reason = getattr(actor, "user", None), reason.strip()[:300]
        made.save()
    return made


# --- counted ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class CountedParcel:
    parcel_id: int
    carats: Decimal | str
    count: int


def count_parcels(branch_id: int, rows: list[CountedParcel], *, note: str = "",
                  actor=None) -> ParcelCount:
    """Book what was found when weighing a branch's parcels: missing stones leave at the
    average cost, stones found come in at it (or at nothing, for an empty parcel)."""
    require_enabled()
    with transaction.atomic():
        branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
        if branch is None:
            raise _error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("diamonds.manage", branch=branch)
        if not rows:
            raise _error("rows", _("Count at least one parcel."))
        parcels = {p.pk: p for p in StoneParcel.objects.select_for_update().filter(
            branch=branch, pk__in=[row.parcel_id for row in rows])}
        checked = []
        for index, row in enumerate(rows):
            parcel = parcels.get(row.parcel_id)
            if parcel is None:
                raise _error(f"rows.{index}", _("Not a parcel of this branch."))
            carats = _amount(row.carats, CARAT, f"rows.{index}.carats", positive=False)
            count = int(row.count or 0)
            if count < 0:
                raise _error(f"rows.{index}.count", _("Must not be negative."))
            checked.append((parcel, carats, count))
        today = timezone.localdate()
        doc = ParcelCount.objects.create(branch=branch, business_date=today, note=note.strip(),
                                         created_by=getattr(actor, "user", None))
        net = ZERO
        for parcel, carats, count in checked:
            diff = carats - parcel.carats
            if diff < 0:
                cost = -_cost_of(parcel, -diff)
            elif diff > 0 and parcel.carats:
                cost = round_money(parcel.cost_amount * diff / parcel.carats)
            else:
                cost = ZERO
            ParcelCountLine.objects.create(
                count_doc=doc, parcel=parcel, book_carats=parcel.carats,
                book_count=parcel.count, counted_carats=carats, counted_count=count,
                cost_amount=cost)
            if diff or count != parcel.count:
                _move(parcel, ParcelMovementKind.COUNT, diff, count - parcel.count, cost,
                      doc_type=COUNT_DOC, doc_id=doc.pk, day=today)
                net += cost
        doc.number = allocate_number("PC", branch=branch, fiscal_year=today.year)
        if net:
            home = functional_commodity()
            doc.journal_entry = post_entry(
                branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=COUNT_DOC,
                source_id=doc.pk, memo=_("Parcels counted %(number)s") % {"number": doc.number},
                lines=[LineInput(account=account_for("inventory_diamonds"), commodity=home,
                                 quantity=net),
                       LineInput(account=account_for("stone_loss"), commodity=home,
                                 quantity=-net)])
        doc.status, doc.posted_at = DocStatus.POSTED, timezone.now()
        doc.posted_by = getattr(actor, "user", None)
        doc.save()
    return doc


def void_count(count_id: int, *, reason: str = "", actor=None) -> ParcelCount:
    require_enabled()
    with transaction.atomic():
        doc = (ParcelCount.objects.select_for_update(of=("self",)).select_related("branch")
               .filter(pk=count_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("diamonds.manage", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        for line in doc.lines.all():
            if line.carats_diff or line.count_diff:
                parcel = StoneParcel.objects.select_for_update().get(pk=line.parcel_id)
                _move(parcel, ParcelMovementKind.VOID, -line.carats_diff, -line.count_diff,
                      -line.cost_amount, doc_type=COUNT_DOC, doc_id=doc.pk, day=today)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": doc.number})
        doc.status, doc.voided_at = DocStatus.VOIDED, timezone.now()
        doc.voided_by, doc.void_reason = getattr(actor, "user", None), reason.strip()[:300]
        doc.save()
    return doc
