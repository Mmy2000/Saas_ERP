from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context
from apps.iam.authz import Actor, build_actor
from apps.iam.models import Membership
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.inventory.transfers import TransferInput, TransferLineInput, send_transfer
from apps.org.dashboard import figures
from apps.parties.services import PartyData, create_trade_account
from apps.sales.models import SettlementBasis
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL
from apps.sales.trade import TradeLineInput, TradeSaleInput, post_trade_sale
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def busy_day(tenant_a):
    """A retail sale, a wholesale settled in gold and a transfer on its way to branch 2."""
    with tenant_context(tenant_a.id):
        stock = Stock()
        post_sale(stock.sale(stock.line(stock.ring_a), payments=[stock.cash(RING_A_TOTAL)]))
        trader = create_trade_account(PartyData(name="Nour Jewellers"))
        post_trade_sale(TradeSaleInput(
            branch_id=stock.branch.pk, trade_account_id=trader.pk,
            settlement_basis=SettlementBasis.METAL, lines=(TradeLineInput(
                category_id=stock.chain.pk, karat_id=stock.k21.pk, gross_weight_g="5",
                making_rate="30"),)))
        send_transfer(TransferInput(from_branch_id=stock.branch.pk, to_branch_id=stock.other.pk,
                                    lines=(TransferLineInput(barcode=stock.ring_b.barcode),)))
        yield stock


def amounts(items):
    return {a.commodity.code: a.quantity for a in items}


def test_todays_figures(tenant_a, busy_day):
    with tenant_context(tenant_a.id):
        data = figures(build_actor(Membership.objects.get(username="owner")))
    sales = data["sales_today"]
    assert (sales["count"], sales["total"], sales["weight"]) == (1, RING_A_TOTAL, Decimal("5"))
    assert sales["change"] is None  # nothing sold yesterday
    assert data["sales_week"][-1]["today"] and data["sales_week"][-1]["height"] == 100
    assert len(data["sales_week"]) == 7
    wholesale = data["wholesale_today"]
    assert (wholesale["count"], wholesale["gross"], wholesale["fine"], wholesale["money"]) == (
        1, Decimal("5"), Decimal("4.375"), Decimal("150"))
    assert amounts(data["cash_on_hand"]) == {"EGP": RING_A_TOTAL}
    balances = {group.url: amounts(group.amounts) for group in data["balances"]}
    assert balances["/trade-accounts/"] == {"EGP": Decimal("150"), "XAU": Decimal("4.375")}
    assert balances["/suppliers/"] == {"EGP": Decimal("-3650"), "XAU": Decimal("-25.75")}
    assert balances["/customers/"] == {}
    # (The fixtures post on fixed dates, so a "months to close" reminder may follow: its
    # rules are covered in apps/ledger/tests/test_closing.py.)
    attention = [(a.url, a.count) for a in data["attention"]
                 if not a.url.startswith("/accounting/periods/")]
    assert attention == [("/stock/transfers/", 1)]
    assert len(data["recent"]) == 3 and all(doc["url"] for doc in data["recent"])


def test_blocks_follow_permissions_and_branches(tenant_a, busy_day, make_member):
    with tenant_context(tenant_a.id):
        nothing = figures(Actor(user=None, membership=None))
        assert all(nothing[key] is None for key in (
            "sales_today", "sales_week", "wholesale_today", "cash_on_hand", "gold_stock",
            "balances", "recent"))
        assert nothing["attention"] == []

        member = make_member(tenant_a, "maadi", "viewer", branches=[busy_day.other])
        seen = figures(build_actor(member))
        assert seen["sales_today"]["count"] == 0 and seen["cash_on_hand"] == []
        assert seen["attention"][0].count == 1  # the transfer is coming to their branch
        assert seen["recent"] == []


@pytest.mark.parametrize("language", ["ar", "en"])
def test_page(tenant_a, busy_day, language):
    html = login(tenant_a, language=language).get("/").content.decode()
    assert "18,392.85" in html and "4.375" in html
    assert ("تحويلات بانتظار الاستلام" if language == "ar"
            else "Transfers waiting to be received") in html
