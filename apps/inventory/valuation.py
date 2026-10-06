"""Ledger lines for stock moving between branches or found/lost in a count (§7.5, §15).

Gold is carried on the inventory accounts in fine grams (valued at the board's fine-gram value)
plus the making cost paid, in the company currency. Scrap lives on its own account and has no
separate making cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from apps.core.numeric import round_money
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import account_for, functional_commodity, metal_commodity
from apps.pricing.selectors import fine_gram_value

from .services import SCRAP_CATEGORY_CODE

ZERO = Decimal(0)


@dataclass(frozen=True)
class StockPart:
    category: object
    karat: object | None
    fine_weight_g: Decimal
    cost_amount: Decimal  # making cost (gold) in the company currency; ignored for scrap
    stone_cost: Decimal = Decimal(0)  # stones in the piece (diamonds), on their own account

    @property
    def is_scrap(self) -> bool:
        return self.category.code == SCRAP_CATEGORY_CODE


def metal_value(karat, fine_weight_g: Decimal) -> Decimal:
    if karat is None or not fine_weight_g:
        return ZERO
    return round_money(fine_weight_g * fine_gram_value(karat.metal.code))


def stock_lines(parts: list[StockPart], *, sign: int, counter_role: str, branch,
                values: list[Decimal] | None = None, party=None) -> list[LedgerLine]:
    """sign = +1: stock comes onto the inventory accounts against `counter_role`;
    -1: it leaves them. `values` fixes the metal value per part (a receipt mirrors its send).
    `party` goes on the counter lines (e.g. the supplier goods are returned to)."""
    metal: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    making: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for index, part in enumerate(parts):
        role = "inventory_scrap" if part.is_scrap else "inventory_gold"
        if part.karat is not None and part.fine_weight_g:
            value = values[index] if values is not None else metal_value(part.karat,
                                                                         part.fine_weight_g)
            key = (role, part.karat.metal.code)
            metal[key][0] += part.fine_weight_g
            metal[key][1] += value
        if not part.is_scrap and part.cost_amount:
            making[role] += part.cost_amount

    counter = account_for(counter_role)
    lines: list[LedgerLine] = []
    for (role, metal_code), (fine, value) in sorted(metal.items()):
        commodity = metal_commodity(metal_code)
        lines.append(LedgerLine(account=account_for(role), commodity=commodity,
                                quantity=sign * fine, functional_amount=sign * value,
                                branch=branch))
        lines.append(LedgerLine(account=counter, commodity=commodity, quantity=-sign * fine,
                                functional_amount=-sign * value, branch=branch, party=party))
    stones = sum((part.stone_cost for part in parts), ZERO)
    if stones:
        making["inventory_diamonds"] = making.get("inventory_diamonds", ZERO) + stones
    home = functional_commodity()
    for role, amount in sorted(making.items()):
        lines.append(LedgerLine(account=account_for(role), commodity=home, quantity=sign * amount,
                                branch=branch))
        lines.append(LedgerLine(account=counter, commodity=home, quantity=-sign * amount,
                                branch=branch, party=party))
    return lines
