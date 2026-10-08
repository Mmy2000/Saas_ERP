"""What the bell can tell a member about. Each kind goes to the members holding its permission in
the notification's branch, and is written in the reader's language when shown (the stored row
keeps only the facts, in `params`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.utils.translation import ngettext

from apps.core.templatetags.ui import num


def _ltr(value) -> str:
    """A document number kept in one piece inside Arabic text (a first-strong isolate)."""
    return f"{chr(0x2068)}{value}{chr(0x2069)}"


@dataclass(frozen=True)
class Kind:
    key: str
    label: object  # on the preferences list
    description: object
    permission: str
    icon: str
    text: Callable[[dict], tuple[str, str]]  # params → (title, body)


def _stock_transfer(p):
    return (_("Goods on their way from %(branch)s") % p,
            ngettext("Transfer %(number)s: %(count)s item, %(weight)s g. Receive it when it "
                     "arrives.",
                     "Transfer %(number)s: %(count)s items, %(weight)s g. Receive them when "
                     "they arrive.", p["count"]) % {**p, "number": _ltr(p["number"]),
                                                  "weight": num(p["weight"], 3)})


def _cash_transfer(p):
    return (_("Cash on its way from %(branch)s") % p,
            _("%(number)s: %(amount)s %(currency)s. Confirm it when it arrives.")
            % {**p, "number": _ltr(p["number"]), "amount": num(p["amount"], 2)})


def _gold_price(p):
    karat = p.get("karat_name") or _("%(code)sK") % {"code": p["karat"]}
    return (_("New gold prices"),
            _("%(karat)s: sell %(sell)s, buy %(buy)s.")
            % {"karat": karat, "sell": num(p["sell"], 2),
               "buy": num(p["buy"], 2) if p["buy"] else "—"})


def _price_held(p):
    return (_("A big gold price change is waiting for you"),
            _("The market moved %(jump)s%%. Approve the new prices or keep today's.")
            % {"jump": num(p["jump"], 2)})


def _repairs_due(p):
    return (_("Repairs due today"),
            ngettext("%(count)s repair at %(branch)s is promised for today.",
                     "%(count)s repairs at %(branch)s are promised for today.", p["count"]) % p)


def _reservations_due(p):
    return (_("Reservations ending today"),
            ngettext("%(count)s reservation at %(branch)s ends today.",
                     "%(count)s reservations at %(branch)s end today.", p["count"]) % p)


def _cheques_due(p):
    return (_("Cheques due today"),
            ngettext("%(count)s cheque at %(branch)s falls due today.",
                     "%(count)s cheques at %(branch)s fall due today.", p["count"]) % p)


KINDS: dict[str, Kind] = {kind.key: kind for kind in (
    Kind("stock_transfer", _l("Goods sent to my branch"),
         _l("When another branch sends pieces or gold to a branch you receive for."),
         "inventory.transfer.receive", "truck", _stock_transfer),
    Kind("cash_transfer", _l("Cash sent to my branch"),
         _l("When another branch sends cash to a branch you receive for."),
         "treasury.transfer.receive", "wallet", _cash_transfer),
    Kind("gold_price", _l("Gold price changes"),
         _l("Each new price board, by hand or from the market. Only the latest one is kept."),
         "pricing.board.view", "coins", _gold_price),
    Kind("price_held", _l("Price changes to approve"),
         _l("When the market jumps more than your limit and the new price waits for you."),
         "pricing.feed.manage", "trending-up", _price_held),
    Kind("repairs_due", _l("Repairs due today"),
         _l("Every morning, the repairs promised for that day."),
         "repairs.order.view", "wrench", _repairs_due),
    Kind("reservations_due", _l("Reservations ending today"),
         _l("Every morning, the reservations whose date is that day."),
         "sales.reservation.view", "bookmark", _reservations_due),
    Kind("cheques_due", _l("Cheques due today"),
         _l("Every morning, the cheques to collect or pay that day."),
         "treasury.cheque.view", "receipt-text", _cheques_due),
)}


def kinds_for(actor) -> list[Kind]:
    """The kinds this member may receive (and read): those whose permission they hold."""
    return [kind for kind in KINDS.values() if actor.can(kind.permission)]
