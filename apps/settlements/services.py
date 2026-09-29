"""Receipts, payments, gold settlements and gold-to-money conversions (§7.8).

Every settlement posts one balanced entry on the party's account for its side (customers,
suppliers or trade accounts):

* receipt     Dr box/bank/terminal     Cr party            (in the currency received)
* payment     Dr party                 Cr box/bank         (in the currency paid)
* gold in     Dr scrap stock (fine g)  Cr party (fine g)   + scrap lot in
* gold out    Dr party (fine g)        Cr scrap stock      + scrap lot out
* conversion  the party's gold balance becomes a money balance at an agreed price per fine
              gram; the metal position account carries the metal side (like an FX position).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import Currency, Karat
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import FINE_WEIGHT, UNIT_PRICE, WEIGHT, quantize, round_money
from apps.core.sequences import allocate_number
from apps.inventory.models import MovementType
from apps.inventory.services import DocRef, lot_for, move_lot, scrap_category
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
from apps.parties.models import Party
from apps.pricing.selectors import fine_gram_value, functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force
from apps.treasury.holders import ensure_cash_available, resolve_tender

from .models import ConversionDirection, MoneyMethod, PartySide, Settlement, SettlementKind

DOC_TYPE = "settlements.Settlement"
PERMISSIONS = {
    SettlementKind.RECEIPT: "settlements.money.post",
    SettlementKind.PAYMENT: "settlements.money.post",
    SettlementKind.METAL_IN: "settlements.metal.post",
    SettlementKind.METAL_OUT: "settlements.metal.post",
    SettlementKind.CONVERSION: "settlements.conversion.post",
}
PREFIXES = {SettlementKind.RECEIPT: "RC", SettlementKind.PAYMENT: "PY",
            SettlementKind.METAL_IN: "GI", SettlementKind.METAL_OUT: "GO",
            SettlementKind.CONVERSION: "GC"}
PARTY_ACCOUNT = {PartySide.CUSTOMER: "customers", PartySide.SUPPLIER: "suppliers",
                 PartySide.TRADE_ACCOUNT: "trade_accounts"}


@dataclass(frozen=True)
class SettlementInput:
    kind: str
    side: str
    party_id: int
    branch_id: int
    method: str = ""
    currency_code: str = ""
    amount: Decimal | str | None = None
    karat_id: int | None = None
    gross_weight_g: Decimal | str | None = None
    fine_weight_g: Decimal | str | None = None
    direction: str = ""
    price_per_fine_g: Decimal | str | None = None
    reference: str = ""
    note: str = ""
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    terminal_id: int | None = None


def _field_error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def _positive(value, step, field: str) -> Decimal:
    try:
        number = quantize(value, step)
    except (TypeError, ValueError):
        raise _field_error(field, _("Enter a number.")) from None
    if number <= 0:
        raise _field_error(field, _("Must be greater than zero."))
    return number


def _money(settlement: Settlement, data: SettlementInput) -> None:
    if data.method not in MoneyMethod.values:
        raise _field_error("method", _("Choose how the money moves."))
    if data.method == MoneyMethod.CARD and data.kind != SettlementKind.RECEIPT:
        raise _field_error("method", _("Cards can only be used to receive money."))
    currency = Currency.objects.filter(code=data.currency_code, is_active=True).first()
    if currency is None:
        raise _field_error("currency", _("Unknown currency."))
    settlement.method = data.method
    settlement.currency = currency
    settlement.amount = _positive(data.amount, Decimal("0.01"), "amount")
    settlement.fx_rate = (Decimal(1) if currency.code == functional_currency()
                          else rate_in_force(currency.code))
    settlement.functional_amount = round_money(settlement.amount * settlement.fx_rate)
    holder = resolve_tender(data.method, settlement.branch, currency,
                            cash_box_id=data.cash_box_id, bank_account_id=data.bank_account_id,
                            terminal_id=data.terminal_id)
    if data.kind == SettlementKind.PAYMENT and holder.cash_box is not None:
        ensure_cash_available(holder.cash_box, settlement.amount, "amount")
    for name, value in holder.fields.items():
        setattr(settlement, name, value)


def _metal(settlement: Settlement, data: SettlementInput) -> None:
    karat = Karat.objects.select_related("metal").filter(pk=data.karat_id, is_active=True).first()
    if karat is None:
        raise _field_error("karat", _("Choose the karat."))
    settlement.karat = karat
    settlement.gross_weight_g = _positive(data.gross_weight_g, WEIGHT, "gross_weight_g")
    settlement.fine_weight_g = fine_weight(settlement.gross_weight_g, karat.fineness)
    settlement.functional_amount = round_money(
        settlement.fine_weight_g * fine_gram_value(karat.metal.code))


def _conversion(settlement: Settlement, data: SettlementInput) -> None:
    if data.direction not in ConversionDirection.values:
        raise _field_error("direction", _("Choose which way the balance is settled."))
    settlement.direction = data.direction
    settlement.fine_weight_g = _positive(data.fine_weight_g, FINE_WEIGHT, "fine_weight_g")
    settlement.price_per_fine_g = _positive(data.price_per_fine_g, UNIT_PRICE,
                                            "price_per_fine_g")
    currency = Currency.objects.filter(code=data.currency_code or functional_currency(),
                                       is_active=True).first()
    if currency is None:
        raise _field_error("currency", _("Unknown currency."))
    settlement.currency = currency
    settlement.amount = round_money(settlement.fine_weight_g * settlement.price_per_fine_g)
    settlement.fx_rate = (Decimal(1) if currency.code == functional_currency()
                          else rate_in_force(currency.code))
    settlement.functional_amount = round_money(settlement.amount * settlement.fx_rate)


def _ledger_lines(settlement: Settlement) -> list[LedgerLine]:
    party_account = account_for(PARTY_ACCOUNT[settlement.side])
    party = settlement.party
    kind = settlement.kind
    value = settlement.functional_amount
    lines: list[LedgerLine] = []

    def add(account, commodity, quantity, functional, with_party=False):
        lines.append(LedgerLine(account=account, commodity=commodity, quantity=quantity,
                                functional_amount=functional,
                                party=party if with_party else None))

    if kind in (SettlementKind.RECEIPT, SettlementKind.PAYMENT):
        sign = 1 if kind == SettlementKind.RECEIPT else -1
        money = money_commodity(settlement.currency)
        money_account = (settlement.cash_box or settlement.bank_account
                         or settlement.terminal).account
        add(money_account, money, sign * settlement.amount, sign * value)
        add(party_account, money, -sign * settlement.amount, -sign * value, with_party=True)
    elif kind in (SettlementKind.METAL_IN, SettlementKind.METAL_OUT):
        sign = 1 if kind == SettlementKind.METAL_IN else -1
        metal = metal_commodity(settlement.karat.metal.code)
        add(account_for("inventory_scrap"), metal, sign * settlement.fine_weight_g, sign * value)
        add(party_account, metal, -sign * settlement.fine_weight_g, -sign * value,
            with_party=True)
    else:
        # We owe gold → the party's gold balance goes up by the grams (debit), their money
        # balance goes down by the amount (credit). They owe gold → the mirror image.
        sign = 1 if settlement.direction == ConversionDirection.WE_OWE_GOLD else -1
        metal = metal_commodity("gold")
        money = money_commodity(settlement.currency)
        position = account_for("metal_position")
        add(party_account, metal, sign * settlement.fine_weight_g, sign * value, with_party=True)
        add(position, metal, -sign * settlement.fine_weight_g, -sign * value)
        add(position, money, sign * settlement.amount, sign * value)
        add(party_account, money, -sign * settlement.amount, -sign * value, with_party=True)
    return lines


def post_settlement(data: SettlementInput, *, actor=None) -> Settlement:
    if data.kind not in SettlementKind.values:
        raise _field_error("kind", _("Choose what is being settled."))
    if data.side not in PartySide.values:
        raise _field_error("side", _("Choose who the settlement is with."))
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field_error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require(PERMISSIONS[data.kind], branch=branch)
        party = Party.objects.filter(pk=data.party_id, is_active=True,
                                     roles__role=data.side).first()
        if party is None:
            raise _field_error("party", _("Choose an active account."))

        today = timezone.localdate()
        settlement = Settlement(kind=data.kind, side=data.side, party=party, branch=branch,
                                business_date=today, reference=data.reference.strip()[:60],
                                note=data.note.strip(), created_by=getattr(actor, "user", None))
        if data.kind in (SettlementKind.RECEIPT, SettlementKind.PAYMENT):
            _money(settlement, data)
        elif data.kind == SettlementKind.CONVERSION:
            _conversion(settlement, data)
        else:
            _metal(settlement, data)
        settlement.save()

        if settlement.kind in (SettlementKind.METAL_IN, SettlementKind.METAL_OUT):
            sign = 1 if settlement.kind == SettlementKind.METAL_IN else -1
            move_lot(lot_for(scrap_category(), settlement.karat, branch), qty=0,
                     gross_weight_g=sign * settlement.gross_weight_g,
                     cost_amount=sign * settlement.functional_amount,
                     movement_type=(MovementType.SCRAP_IN if sign > 0 else MovementType.SCRAP_OUT),
                     business_date=today, doc=DocRef(DOC_TYPE, settlement.pk))

        settlement.number = allocate_number(PREFIXES[settlement.kind], branch=branch,
                                            fiscal_year=today.year)
        settlement.journal_entry = post_entry(
            branch=branch, business_date=today, lines=_ledger_lines(settlement),
            kind=EntryKind.AUTO, source_type=DOC_TYPE, source_id=settlement.pk,
            memo=f"{settlement.get_kind_display()} {settlement.number}",
        )
        settlement.status = DocStatus.POSTED
        settlement.posted_at = timezone.now()
        settlement.posted_by = getattr(actor, "user", None)
        settlement.save()
    return settlement


def void_settlement(settlement_id: int, *, reason: str = "", actor=None) -> Settlement:
    with transaction.atomic():
        settlement = (Settlement.objects.select_for_update(of=("self",))
                      .select_related("karat__metal", "branch")
                      .filter(pk=settlement_id).first())
        if settlement is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("settlements.void", branch=settlement.branch)
        if settlement.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        if settlement.kind in (SettlementKind.METAL_IN, SettlementKind.METAL_OUT):
            sign = -1 if settlement.kind == SettlementKind.METAL_IN else 1
            move_lot(lot_for(scrap_category(), settlement.karat, settlement.branch), qty=0,
                     gross_weight_g=sign * settlement.gross_weight_g,
                     cost_amount=sign * settlement.functional_amount,
                     movement_type=(MovementType.SCRAP_IN if sign > 0 else MovementType.SCRAP_OUT),
                     business_date=today, doc=DocRef(DOC_TYPE, settlement.pk))
        if settlement.journal_entry_id:
            reverse_entry(settlement.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": settlement.number})
        settlement.status = DocStatus.VOIDED
        settlement.voided_at = timezone.now()
        settlement.voided_by = getattr(actor, "user", None)
        settlement.void_reason = reason.strip()[:300]
        settlement.save()
    return settlement

