"""Agreed statements (legacy Suu2_Match, GHSS40 / GHSS41): the balance a trader, supplier or
customer confirmed as of a date, kept as agreed. Back-dated documents or voids that later move
the balance as of that date are flagged, so a disagreement is found before the next meeting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.errors import ValidationError
from apps.ledger.models import Account, Commodity, JournalLine

from .models import StatementAgreement


def balances_as_of(party, account_role: str, as_of: date) -> dict[str, Decimal]:
    """The party's balance per commodity code on its main account at the end of `as_of`."""
    account = Account.objects.filter(role=account_role).first()
    if account is None:
        return {}
    rows = (JournalLine.objects.filter(party=party, account=account, business_date__lte=as_of)
            .values("commodity__code").annotate(q=Sum("quantity")))
    return {row["commodity__code"]: row["q"] for row in rows if row["q"]}


def agree_statement(party, role: str, account_role: str, *, as_of: date | None = None,
                    agreed_with: str = "", note: str = "", actor=None) -> StatementAgreement:
    as_of = as_of or timezone.localdate()
    if as_of > timezone.localdate():
        raise ValidationError(str(_("The date cannot be in the future.")))
    places = dict(Commodity.objects.values_list("code", "decimal_places"))
    balances = {code: str(q.quantize(Decimal(1).scaleb(-places.get(code, 2))))
                for code, q in balances_as_of(party, account_role, as_of).items()}
    with transaction.atomic():
        return StatementAgreement.objects.create(
            party=party, role=role, as_of=as_of, agreed_with=agreed_with.strip()[:120],
            note=note.strip()[:300], balances=balances,
            recorded_by=getattr(actor, "user", None))


def delete_agreement(agreement_id: int) -> None:
    StatementAgreement.objects.filter(pk=agreement_id).delete()


@dataclass
class AgreementView:
    agreement: StatementAgreement
    lines: list[tuple[str, Decimal, Decimal, int]]  # (code, agreed, now then, decimals)

    @property
    def holds(self) -> bool:
        return all(agreed == now for _code, agreed, now, _places in self.lines)


def agreements_for(party, role: str, account_role: str, limit: int = 10) -> list[AgreementView]:
    """The latest agreements, each checked against the balance as of its date today."""
    places = dict(Commodity.objects.values_list("code", "decimal_places"))
    views = []
    for agreement in (StatementAgreement.objects.filter(party=party, role=role)
                      .select_related("recorded_by").order_by("-as_of", "-id")[:limit]):
        now = balances_as_of(party, account_role, agreement.as_of)
        agreed = {code: Decimal(q) for code, q in agreement.balances.items()}
        codes = sorted(set(agreed) | set(now))
        views.append(AgreementView(agreement, [
            (code, agreed.get(code, Decimal(0)), now.get(code, Decimal(0)), places.get(code, 2))
            for code in codes]))
    return views
