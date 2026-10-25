"""plan_receipt as it behaves today, recorded in snapshots/plan_receipt.json, so its
refactoring (TD-10) can be proven to change nothing: pieces, cast bars, bulk and scrap back,
loss and gain per metal, labour per gram or as a fixed fee, the carried making cost spread over
the goods, and every input error (including the ones that are not an application error today).

To re-record after an intended change: RECORD_SNAPSHOTS=1 (tests/snapshot.py).
"""

from pathlib import Path

import pytest
from django.utils import translation

from apps.catalog.models import Karat, MetalCode
from apps.core.errors import AppError
from apps.inventory.services import scrap_category
from apps.manufacturing.services import ReceiptInput, ReceiptLineInput, plan_receipt
from apps.manufacturing.tests.test_work_orders import send
from tests.snapshot import assert_snapshots

pytestmark = pytest.mark.django_db
SNAPSHOT = Path(__file__).parent / "snapshots" / "plan_receipt.json"


def run(order, *lines, accept_gain=False):
    try:
        return plan_receipt(order, ReceiptInput(lines=lines, accept_gain=accept_gain))
    except AppError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - recorded: a refactor must not change these either
        return {"unexpected": type(exc).__name__}


def scenarios(stock, order):
    rings, chain, scrap = stock.rings.pk, stock.chain.pk, scrap_category().pk
    k18, k21 = stock.k18.pk, stock.k21.pk
    silver = Karat.objects.get(metal__code=MetalCode.SILVER).pk

    def line(category, karat, **kw):
        return ReceiptLineInput(category_id=category, karat_id=karat, **kw)

    bars = ({"weight_g": "2.5", "fineness": "995", "serial": "A1"},
            {"weight_g": "2.5", "fineness": "999.9", "serial": "A2"})
    return {
        "rings back with a loss": run(order, line(
            rings, k18, piece_weights=("5", "6.1"), labour_rate="60", list_making_rate="250")),
        "rings and scrap back": run(order, line(rings, k18, piece_weights=("5",),
                                                labour_rate="60"),
                                    line(scrap, k21, gross_weight_g="2.25")),
        "three worked lines share the carried cost": run(
            order, line(rings, k18, piece_weights=("3.333",), labour_rate="10"),
            line(rings, k18, piece_weights=("3.333",), labour_rate="10"),
            line(chain, k18, gross_weight_g="3.334", qty=1, labour_rate="10")),
        "three equal lines: the last takes what rounding leaves": run(
            order, *(line(rings, k18, piece_weights=("3.333",)) for _ in range(3))),
        "bulk back with a fixed fee": run(order, line(chain, k21, gross_weight_g="9.5", qty=2,
                                                      labour_amount="150")),
        "more gold back, gain not confirmed": run(order, line(chain, k21, gross_weight_g="12")),
        "more gold back, gain confirmed": run(order, line(chain, k21, gross_weight_g="12"),
                                              accept_gain=True),
        "cast bars with their own fineness": run(order, line(
            rings, k21, bars=bars, labour_amount="80", list_making_rate="30")),
        "cast bars and silver": run(order, line(rings, k21, bars=bars),
                                    line(chain, silver, gross_weight_g="50"), accept_gain=True),
        "only scrap back": run(order, line(scrap, k21, gross_weight_g="9.9",
                                           labour_rate="60", list_making_rate="250")),
        "a bar's serial twice": run(order, line(rings, k21, bars=(
            {"weight_g": "1", "serial": "B1"}, {"weight_g": "1", "serial": "b1"}))),
        "a bar without weight": run(order, line(rings, k21, bars=({"weight_g": "0"},))),
        "a bar with fineness over 1000": run(order, line(rings, k21, bars=(
            {"weight_g": "1", "fineness": "1001"},))),
        "a bar's weight not a number": run(order, line(rings, k21, bars=(
            {"weight_g": "abc"},))),
        "bars without a serial": run(order, line(rings, k21, bars=(
            {"weight_g": "1"}, {"weight_g": "2", "fineness": "875"}))),
        "a piece of zero grams": run(order, line(rings, k18, piece_weights=("5", "0"))),
        "no piece weights": run(order, line(rings, k18)),
        "a piece weight not a number": run(order, line(rings, k18, piece_weights=("abc",))),
        "bulk without weight": run(order, line(chain, k21)),
        "bulk weight not a number": run(order, line(chain, k21, gross_weight_g="abc")),
        "bulk with a negative count": run(order, line(chain, k21, gross_weight_g="5", qty=-3)),
        "scrap with a count": run(order, line(scrap, k21, gross_weight_g="5", qty=4)),
        "negative labour": run(order, line(rings, k18, piece_weights=("5",), labour_rate="-1")),
        "negative making charge": run(order, line(rings, k18, piece_weights=("5",),
                                                  list_making_rate="-1")),
        "labour not a number": run(order, line(rings, k18, piece_weights=("5",),
                                               labour_rate="abc")),
        "negative fixed fee": run(order, line(chain, k21, gross_weight_g="5",
                                              labour_amount="-5")),
        "fixed fee not a number": run(order, line(chain, k21, gross_weight_g="5",
                                                  labour_amount="abc")),
        "scrap with labour and a bad fee": run(order, line(scrap, k21, gross_weight_g="5",
                                                           labour_amount="-5")),
        "unknown category": run(order, line(999999, k21, gross_weight_g="5")),
        "unknown karat": run(order, line(chain, 999999, gross_weight_g="5")),
        "nothing back": run(order),
    }


def test_plan_receipt_is_unchanged(stock):
    order = send(stock, "10")  # 10 g of 21K chain, making cost carried: 1,000
    # Messages in one language: an earlier test can leave another one active.
    with translation.override("en"):
        assert_snapshots(SNAPSHOT, scenarios(stock, order))
