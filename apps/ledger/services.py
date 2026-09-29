"""PostingService and ledger setup (§7.10, §16.1).

Every module posts through `post_entry`. It validates the invariants, allocates the entry
number, writes the lines and updates the balance projections in the caller's transaction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.numeric import MONEY_HIGH_PRECISION, to_decimal
from apps.core.sequences import allocate_number

from .chart import CHART, METAL_COMMODITIES
from .models import (
    Account,
    BalanceProjection,
    Commodity,
    CommodityKind,
    EntryKind,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    PeriodStatus,
    Subledger,
)

ROUNDING_TOLERANCE = Decimal("0.05")  # functional units absorbed by the rounding account
ZERO = Decimal(0)


# --- setup ------------------------------------------------------------------------------------

def seed_ledger(functional_currency: str) -> None:
    """Commodities and the default chart for a new tenant (§5.7). Idempotent."""
    from apps.catalog.models import Currency, Metal

    for currency in Currency.objects.filter(is_active=True):
        Commodity.objects.get_or_create(
            code=currency.code,
            defaults={"kind": CommodityKind.MONEY, "currency": currency,
                      "decimal_places": currency.minor_units,
                      "is_functional": currency.code == functional_currency},
        )
    for code, kind, metal_code, places in METAL_COMMODITIES:
        metal = Metal.objects.filter(code=metal_code).first()
        if metal is not None:
            Commodity.objects.get_or_create(
                code=code, defaults={"kind": kind, "metal": metal, "decimal_places": places})

    by_code: dict[str, Account] = {a.code: a for a in Account.objects.all()}
    for code, parent, _name, type_, nature, postable, subledger, scope, role in CHART:
        if code in by_code:
            continue
        by_code[code] = Account.objects.create(
            code=code, template_key=code, parent=by_code.get(parent), type=type_, nature=nature,
            is_postable=postable, subledger=subledger, commodity_scope=scope, role=role,
        )


def account_for(role: str) -> Account:
    account = Account.objects.filter(role=role, is_active=True).first()
    if account is None:
        raise DomainError(_("No account is set up for “%(role)s”.") % {"role": role},
                          code="LEDGER_ROLE_MISSING")
    return account


def functional_commodity() -> Commodity:
    return Commodity.objects.get(is_functional=True)


def money_commodity(currency) -> Commodity:
    """The commodity for a catalog Currency (created on first use for currencies added later)."""
    commodity, _created = Commodity.objects.get_or_create(
        code=currency.code,
        defaults={"kind": CommodityKind.MONEY, "currency": currency,
                  "decimal_places": currency.minor_units},
    )
    return commodity


def metal_commodity(metal_code: str) -> Commodity:
    commodity = Commodity.objects.filter(kind=CommodityKind.METAL, metal__code=metal_code).first()
    if commodity is None:
        raise DomainError(_("No ledger commodity for %(metal)s.") % {"metal": metal_code},
                          code="LEDGER_NO_METAL_COMMODITY")
    return commodity


# --- posting ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class LineInput:
    account: Account
    commodity: Commodity
    quantity: Decimal | str | int  # signed: debit > 0, credit < 0
    functional_amount: Decimal | str | int | None = None  # required unless functional currency
    party: object | None = None
    branch: object | None = None  # defaults to the entry's branch
    memo: str = ""


@dataclass
class _Totals:
    functional: Decimal = ZERO
    metal: dict[int, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))


def _ensure_period_open(business_date: date) -> None:
    closed = FiscalPeriod.objects.filter(status=PeriodStatus.CLOSED, start_date__lte=business_date,
                                         end_date__gte=business_date).first()
    if closed is not None:
        raise DomainError(_("The period %(period)s is closed.") % {"period": closed.name},
                          code="LEDGER_PERIOD_CLOSED")


def _prepare(lines: list[LineInput], entry_branch) -> list[JournalLine]:
    prepared = []
    for index, line in enumerate(lines):
        account, commodity = line.account, line.commodity
        where = {"lines": {str(index): [_("Invalid line.")]}}
        if not account.is_postable or not account.is_active:
            raise ValidationError(_("Account %(account)s cannot be posted to.")
                                  % {"account": account}, code="LEDGER_ACCOUNT_NOT_POSTABLE",
                                  fields=where)
        if not account.allows(commodity):
            raise ValidationError(_("Account %(account)s does not take %(commodity)s.")
                                  % {"account": account, "commodity": commodity.label},
                                  code="LEDGER_COMMODITY_NOT_ALLOWED", fields=where)
        if (account.subledger == Subledger.PARTY) != (line.party is not None):
            raise ValidationError(
                _("Account %(account)s needs a customer/supplier on every line.")
                % {"account": account} if account.subledger == Subledger.PARTY
                else _("Account %(account)s does not take a customer/supplier.")
                % {"account": account},
                code="LEDGER_PARTY_MISMATCH", fields=where)

        quantity = to_decimal(line.quantity)
        if quantity == 0:
            raise ValidationError(_("A line cannot be zero."), code="LEDGER_ZERO_LINE",
                                  fields=where)
        if commodity.is_functional:
            functional = quantity if line.functional_amount is None else to_decimal(
                line.functional_amount)
            if functional != quantity:
                raise ValidationError(_("A line in the company currency has no separate value."),
                                      code="LEDGER_FUNCTIONAL_MISMATCH", fields=where)
        elif line.functional_amount is None:
            raise ValidationError(
                _("Give the value in the company currency for %(commodity)s lines.")
                % {"commodity": commodity.label}, code="LEDGER_VALUE_REQUIRED", fields=where)
        else:
            functional = to_decimal(line.functional_amount)

        for value in (quantity, functional):
            if value != value.quantize(MONEY_HIGH_PRECISION):
                raise ValidationError(_("At most 6 decimal places."), code="LEDGER_PRECISION",
                                      fields=where)
        prepared.append(JournalLine(
            account=account, commodity=commodity, quantity=quantity,
            functional_amount=functional, party=line.party,
            branch=line.branch or entry_branch, memo=line.memo[:300],
        ))
    return prepared


def _totals(lines: list[JournalLine]) -> _Totals:
    totals = _Totals()
    for line in lines:
        totals.functional += line.functional_amount
        if line.commodity.kind == CommodityKind.METAL:
            totals.metal[line.commodity_id] += line.quantity
    return totals


def _apply_projections(lines: list[JournalLine]) -> None:
    deltas: dict[tuple, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in lines:
        key = (line.account_id, line.commodity_id, line.branch_id, line.party_id)
        deltas[key][0] += line.quantity
        deltas[key][1] += line.functional_amount
    keys = sorted(deltas, key=lambda k: (k[0], k[1], k[2], k[3] or 0))  # stable lock order
    BalanceProjection.objects.bulk_create(
        [BalanceProjection(account_id=a, commodity_id=c, branch_id=b, party_id=p)
         for a, c, b, p in keys], ignore_conflicts=True)
    for key in keys:
        account_id, commodity_id, branch_id, party_id = key
        quantity, functional = deltas[key]
        BalanceProjection.objects.filter(
            account_id=account_id, commodity_id=commodity_id, branch_id=branch_id,
            party_id=party_id,
        ).update(quantity=F("quantity") + quantity,
                 functional_amount=F("functional_amount") + functional)


def post_entry(*, branch, business_date: date, lines: list[LineInput],
               kind: str = EntryKind.AUTO, memo: str = "", source_type: str = "",
               source_id: int | None = None, reverses: JournalEntry | None = None,
               absorb_rounding: bool = False, actor=None) -> JournalEntry:
    """Post one balanced entry. Raises ValidationError/DomainError and writes nothing if any
    invariant fails. `absorb_rounding` books a functional difference up to 0.05 to the
    "rounding" account (FX conversions, §8.6)."""
    if kind == EntryKind.MANUAL and actor is not None:
        actor.require("ledger.journal.post", branch=branch)
    if len(lines) < 2:
        raise ValidationError(_("An entry needs at least two lines."), code="LEDGER_TOO_FEW_LINES")

    with transaction.atomic():
        _ensure_period_open(business_date)
        prepared = _prepare(lines, branch)
        totals = _totals(prepared)

        small = abs(totals.functional) <= ROUNDING_TOLERANCE
        if totals.functional != 0 and absorb_rounding and small:
            prepared.append(JournalLine(
                account=account_for("rounding"), commodity=functional_commodity(),
                quantity=-totals.functional, functional_amount=-totals.functional,
                branch=branch, memo=_("Rounding"),
            ))
            totals.functional = ZERO
        if totals.functional != 0:
            raise ValidationError(
                _("Debits and credits differ by %(diff)s.") % {"diff": totals.functional},
                code="LEDGER_UNBALANCED")
        unbalanced = [c for c, q in totals.metal.items() if q != 0]
        if unbalanced:
            metals = Commodity.objects.filter(pk__in=unbalanced)
            raise ValidationError(
                _("Metal does not balance: %(metals)s.") % {
                    "metals": ", ".join(m.label for m in metals)},
                code="LEDGER_METAL_UNBALANCED")

        entry = JournalEntry.objects.create(
            number=allocate_number("JV", branch=branch, fiscal_year=business_date.year),
            branch=branch, business_date=business_date, kind=kind, memo=memo[:300],
            source_type=source_type, source_id=source_id, reverses=reverses,
            posted_at=timezone.now(), posted_by=getattr(actor, "user", None),
            created_by=getattr(actor, "user", None),
        )
        for line in prepared:
            line.entry = entry
            line.business_date = business_date
        JournalLine.objects.bulk_create(prepared)
        _apply_projections(prepared)
    return entry


def reverse_entry(entry_id: int, *, business_date: date | None = None, memo: str = "",
                  actor=None) -> JournalEntry:
    """Post the mirror image of an entry (the only way to correct a posted one)."""
    entry = JournalEntry.objects.filter(pk=entry_id).first()
    if entry is None:
        raise NotFound(_("Not found."))
    if actor is not None:
        actor.require("ledger.journal.post", branch=entry.branch)
    if hasattr(entry, "reversed_by"):
        raise DomainError(_("This entry was already reversed."), code="LEDGER_ALREADY_REVERSED")
    if entry.kind == EntryKind.REVERSAL:
        raise DomainError(_("A reversal cannot be reversed; post a new entry instead."),
                          code="LEDGER_REVERSE_REVERSAL")
    lines = [
        LineInput(account=line.account, commodity=line.commodity, quantity=-line.quantity,
                  functional_amount=-line.functional_amount, party=line.party,
                  branch=line.branch, memo=line.memo)
        for line in entry.lines.select_related("account", "commodity", "party", "branch")
    ]
    return post_entry(
        branch=entry.branch, business_date=business_date or timezone.localdate(),
        lines=lines, kind=EntryKind.REVERSAL,
        memo=memo or _("Reversal of %(number)s") % {"number": entry.number},
        source_type=entry.source_type, source_id=entry.source_id, reverses=entry, actor=actor,
    )


def rebuild_balances() -> int:
    """Recompute every projection of the current tenant from its journal lines."""
    with transaction.atomic():
        BalanceProjection.objects.all().delete()
        rows = (JournalLine.objects.values("account", "commodity", "branch", "party")
                .annotate(q=Sum("quantity"), f=Sum("functional_amount")))
        BalanceProjection.objects.bulk_create([
            BalanceProjection(account_id=r["account"], commodity_id=r["commodity"],
                              branch_id=r["branch"], party_id=r["party"],
                              quantity=r["q"], functional_amount=r["f"])
            for r in rows
        ])
        return len(rows)
