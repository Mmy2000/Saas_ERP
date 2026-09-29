from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import ItemStatus, LotBalance
from apps.ledger.models import Account
from apps.ledger.selectors import party_balance, trial_balance
from apps.ledger.statements import party_statement
from apps.parties.models import Party
from apps.parties.services import PartyData, create_customer
from apps.sales.returns import create_return, void_return
from apps.sales.services import TradeInInput, post_sale, void_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.settlements.services import SettlementInput, post_settlement, void_settlement
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.supplier = Party.objects.get(name="Factory")
        yield shop


def settle(shop, kind, side, party, **kw):
    return post_settlement(SettlementInput(kind=kind, side=side, party_id=party.pk,
                                           branch_id=shop.branch.pk, **kw))


def rows(tb):
    return [(r.account.code, r.debit, r.credit, r.metals) for r in tb.rows]


class TestReturns:
    def test_full_cash_return_undoes_the_sale(self, shop):
        before = rows(trial_balance())
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
        line = invoice.lines.get()
        sales_return = create_return(invoice.pk, [line.pk], refund_method="cash")
        assert sales_return.number == "01-SR-2026-000001"
        assert sales_return.refund_amount == RING_A_TOTAL
        shop.ring_a.refresh_from_db()
        assert shop.ring_a.status == ItemStatus.IN_STOCK
        assert rows(trial_balance()) == before

    def test_deduction_is_kept_as_income(self, shop):
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
        sales_return = create_return(invoice.pk, [invoice.lines.get().pk], refund_method="cash",
                                     deduction_amount="100")
        assert sales_return.refund_amount == RING_A_TOTAL - 100
        making = next(r for r in trial_balance().rows if r.account.role == "sales_making")
        assert making.credit == Decimal("100")

    def test_refund_to_account_and_rules(self, shop):
        customer = create_customer(PartyData(name="Mona"))
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                      customer_id=customer.pk))
        line = invoice.lines.get()
        sales_return = create_return(invoice.pk, [line.pk], refund_method="customer_credit")
        assert party_balance(customer) == {}
        with pytest.raises(DomainError) as exc:
            create_return(invoice.pk, [line.pk], refund_method="cash")
        assert exc.value.code == "SALES_ALREADY_RETURNED"
        void_return(sales_return.pk)
        assert party_balance(customer) == {"EGP": RING_A_TOTAL}
        create_return(invoice.pk, [line.pk], refund_method="customer_credit")  # returnable again

    def test_walk_in_refunds_only_in_cash_and_sales_with_returns_stay(self, shop):
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
        line = invoice.lines.get()
        with pytest.raises(DomainError):
            create_return(invoice.pk, [line.pk], refund_method="customer_credit")
        create_return(invoice.pk, [line.pk], refund_method="cash")
        with pytest.raises(DomainError) as exc:
            void_sale(invoice.pk)
        assert exc.value.code == "SALES_HAS_RETURNS"


class TestSettlements:
    def test_customer_pays_what_they_owe(self, shop):
        customer = create_customer(PartyData(name="Mona"))
        post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                            customer_id=customer.pk))
        receipt = settle(shop, "receipt", "customer", customer, method="cash",
                         currency_code="EGP", amount=str(RING_A_TOTAL))
        assert receipt.number == "01-RC-2026-000001"
        assert party_balance(customer) == {}

    def test_paying_the_supplier_money_and_gold(self, shop):
        # The purchase in Shop left the supplier owed 8.25 fine g (11 g of 18K) and 1650 EGP.
        assert party_balance(shop.supplier) == {"XAU": Decimal("-8.25"), "EGP": Decimal("-1650")}
        settle(shop, "payment", "supplier", shop.supplier, method="bank_transfer",
               currency_code="EGP", amount="1650")
        with pytest.raises(DomainError) as exc:  # no scrap in stock yet
            settle(shop, "metal_out", "supplier", shop.supplier, karat_id=shop.k21.pk,
                   gross_weight_g="4")
        assert exc.value.code == "INVENTORY_INSUFFICIENT_STOCK"
        post_sale(shop.sale(shop.line(shop.ring_b), trade_ins=[
            TradeInInput(karat_id=shop.k21.pk, gross_weight_g="10")]))  # 8.75 fine g of scrap
        settle(shop, "metal_out", "supplier", shop.supplier, karat_id=shop.k21.pk,
               gross_weight_g="9.428")  # 8.2495 fine g
        balance = party_balance(shop.supplier)
        assert balance == {"XAU": Decimal("-0.0005")}
        scrap = LotBalance.objects.get(lot__category__code="SCRAP")
        assert scrap.gross_weight_g == Decimal("0.572")

    def test_gold_balance_settled_in_money(self, shop):
        conversion = settle(shop, "conversion", "supplier", shop.supplier,
                            direction="we_owe_gold", fine_weight_g="8.25",
                            price_per_fine_g="4500")
        assert conversion.amount == Decimal("37125.00")
        assert party_balance(shop.supplier) == {"EGP": Decimal("-1650") - Decimal("37125")}
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit

    def test_rules_and_void(self, shop):
        customer = create_customer(PartyData(name="Mona"))
        with pytest.raises(ValidationError):
            settle(shop, "payment", "customer", customer, method="card", currency_code="EGP",
                   amount="10")
        with pytest.raises(ValidationError):  # not a supplier
            settle(shop, "payment", "supplier", customer, method="cash", currency_code="EGP",
                   amount="10")
        gold_in = settle(shop, "metal_in", "customer", customer, karat_id=shop.k21.pk,
                         gross_weight_g="5")
        assert party_balance(customer) == {"XAU": Decimal("-4.375")}  # we hold her gold
        void_settlement(gold_in.pk)
        gold_in.refresh_from_db()
        assert gold_in.status == DocStatus.VOIDED and party_balance(customer) == {}


class TestStatements:
    def test_running_balance_and_opening(self, shop):
        customer = create_customer(PartyData(name="Mona"))
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                      customer_id=customer.pk))
        receipt = settle(shop, "receipt", "customer", customer, method="cash",
                         currency_code="EGP", amount="5000")
        account = Account.objects.get(role="customers")
        with translation.override("en"):
            [egp] = party_statement(customer, account)
        assert [r.balance for r in egp.rows] == [RING_A_TOTAL, RING_A_TOTAL - 5000]
        assert egp.closing == RING_A_TOTAL - 5000
        assert egp.rows[0].url and egp.rows[0].url.startswith("/sales/")
        assert [(r.memo, r.document_number) for r in egp.rows] == [
            ("Sales invoice", invoice.number), ("Money received", receipt.number)]
        tomorrow = timezone.localdate() + timedelta(days=1)
        [later] = party_statement(customer, account, date_from=tomorrow)
        assert later.opening == RING_A_TOTAL - 5000 and later.rows == []


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a)
    customer = create_customer(PartyData(name="Mona"))
    with tenant_context(tenant_a.id):
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                      customer_id=customer.pk))
    body = {"kind": "receipt", "side": "customer", "party": customer.pk,
            "branch": shop.branch.pk, "method": "cash", "currency": "EGP", "amount": "1000"}
    first = client.post("/api/v1/settlements/", body, content_type="application/json",
                        HTTP_IDEMPOTENCY_KEY="r1")
    again = client.post("/api/v1/settlements/", body, content_type="application/json",
                        HTTP_IDEMPOTENCY_KEY="r1")
    assert first.status_code == 201 and first.json()["id"] == again.json()["id"]
    returned = client.post("/api/v1/sales/returns/", {
        "invoice": invoice.pk, "lines": [invoice.lines.get().pk],
        "refund_method": "customer_credit"}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="ret1")
    assert returned.status_code == 201, returned.content
    for path in (f"/settlements/{first.json()['id']}/", f"/customers/{customer.pk}/statement/",
                 f"/customers/{customer.pk}/statement/?from=2026-01-01",
                 f"/sales/returns/{returned.json()['id']}/", f"/sales/{invoice.pk}/",
                 f"/customers/{customer.pk}/", f"/suppliers/{shop.supplier.pk}/statement/"):
        assert client.get(path).status_code == 200, path
