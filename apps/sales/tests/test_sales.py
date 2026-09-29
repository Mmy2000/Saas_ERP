from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.catalog.models import Karat, ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import DomainError, PermissionDenied, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.iam.authz import build_actor
from apps.iam.models import MembershipLimit
from apps.inventory.models import Item, ItemStatus, LotBalance
from apps.ledger.models import JournalLine
from apps.ledger.selectors import party_balance, trial_balance
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer, create_supplier
from apps.pricing.services import (
    KaratPriceInput,
    PublishPriceBoardCommand,
    publish_price_board,
    record_fx_rate,
)
from apps.purchasing.services import InvoiceInput, LineInput, post_invoice, save_draft
from apps.sales.models import SalesInvoice
from apps.sales.services import (
    PaymentInput,
    SaleInput,
    SaleLineInput,
    TradeInInput,
    post_sale,
    quote_sale,
    void_sale,
)
from apps.treasury.holders import (
    BankAccountInput,
    TerminalInput,
    create_bank_account,
    create_terminal,
)

pytestmark = pytest.mark.django_db

# 18K sell = 4000 × 750/875 = 3428.57/g; ring A weighs 5 g with a 250/g making charge:
# (3428.57 + 250) × 5 = 18,392.85 of which metal 17,142.85 and making 1,250.00.
RING_A_TOTAL = Decimal("18392.85")


class Shop:
    def __init__(self):
        self.branch = Branch.objects.get(code=1)
        self.k18, self.k21 = Karat.objects.get(code=18), Karat.objects.get(code=21)
        rings = create_item_category(CreateItemCategoryCommand(
            code="1010", name="Rings", product_family=ProductFamily.GOLD,
            tracking=Tracking.SERIALIZED, barcode_prefix=1010))
        supplier = create_supplier(PartyData(name="Factory"))
        post_invoice(save_draft(InvoiceInput(
            supplier_id=supplier.pk, branch_id=self.branch.pk, business_date=date(2026, 9, 1),
            currency_code="EGP", lines=(LineInput(
                category_id=rings.pk, karat_id=self.k18.pk, piece_weights=("5", "6"),
                making_cost_rate="150", list_making_rate="250"),))).pk)
        publish_price_board(PublishPriceBoardCommand(
            reference=KaratPriceInput(self.k21.pk, "4000", "3950", "3900")))
        self.ring_a, self.ring_b = Item.objects.order_by("barcode")
        self.bank = create_bank_account(BankAccountInput(name="CIB", currency_code="EGP"))
        self.terminal = create_terminal(TerminalInput(name="POS 1", bank_account_id=self.bank.pk,
                                                      fee_rate="0.02"))

    def sale(self, *lines, payments=(), trade_ins=(), **kw):
        return SaleInput(branch_id=self.branch.pk, lines=tuple(lines), payments=tuple(payments),
                         trade_ins=tuple(trade_ins), **kw)

    @staticmethod
    def line(item, discount="0"):
        return SaleLineInput(barcode=item.barcode, discount_rate=discount)

    @staticmethod
    def cash(amount, currency="EGP"):
        return PaymentInput(kind="cash", currency_code=currency, amount=str(amount))


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def test_cash_sale_posts_everything(shop):
    before = trial_balance()
    invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
    assert invoice.number == "01-SI-2026-000001" and invoice.status == DocStatus.POSTED
    assert invoice.total_amount == RING_A_TOTAL and invoice.change_amount == 0
    line = invoice.lines.get()
    assert (line.metal_amount, line.making_amount) == (Decimal("17142.85"), Decimal("1250.00"))
    shop.ring_a.refresh_from_db()
    assert shop.ring_a.status == ItemStatus.SOLD

    entry_lines = JournalLine.objects.filter(entry=invoice.journal_entry)
    by_role = {(ln.account.role or ln.account.parent.role, ln.commodity.code): ln.quantity
               for ln in entry_lines}
    assert by_role[("cash", "EGP")] == RING_A_TOTAL
    assert by_role[("sales_gold", "EGP")] == Decimal("-17142.85")
    assert by_role[("sales_making", "EGP")] == Decimal("-1250")
    assert by_role[("inventory_gold", "XAU")] == Decimal("-3.75")  # 5 g of 18K
    assert by_role[("inventory_gold", "EGP")] == Decimal("-750")  # making cost 150 × 5
    after = trial_balance()
    assert after.total_debit == after.total_credit
    assert after.total_debit > before.total_debit


def test_discount_on_making_only_with_cost_floor(shop):
    quote = quote_sale(shop.sale(shop.line(shop.ring_a, "0.10")))
    assert quote.total == Decimal("18267.85") and quote.discount == Decimal("125.00")
    floored = quote_sale(shop.sale(shop.line(shop.ring_a, "0.50"))).lines[0]
    assert floored.making_rate_net == Decimal("150") and floored.at_cost_floor


def test_discount_limit_is_personal(shop, tenant_a, make_member):
    member = make_member(tenant_a, "sam", "manager")
    with tenant_context(tenant_a.id):
        seller = build_actor(member)
        with pytest.raises(ValidationError) as exc:
            quote_sale(shop.sale(shop.line(shop.ring_a, "0.05")), actor=seller)
        assert exc.value.code == "SALES_DISCOUNT_LIMIT" and "0" in exc.value.fields["lines"]
        MembershipLimit.objects.create(membership=member, key="sales.discount.max_rate.gold",
                                       value=Decimal("0.05"))
        assert quote_sale(shop.sale(shop.line(shop.ring_a, "0.05")),
                          actor=build_actor(member)).total < RING_A_TOTAL


def test_trade_in_worth_more_than_the_goods(shop):
    # 21K scrap 10 g, 0.5 g loss: 9.5 × 3900 = 37,050, so the shop pays out the difference.
    invoice = post_sale(shop.sale(shop.line(shop.ring_a), trade_ins=[
        TradeInInput(karat_id=shop.k21.pk, gross_weight_g="10", loss_weight_g="0.5")]))
    assert invoice.trade_in_amount == Decimal("37050.00")
    assert invoice.change_amount == Decimal("37050.00") - RING_A_TOTAL
    scrap = LotBalance.objects.get(lot__category__code="SCRAP")
    assert (scrap.gross_weight_g, scrap.fine_weight_g) == (Decimal("9.500"), Decimal("8.3125"))
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit


def test_mixed_currency_and_card(shop):
    record_fx_rate("USD", "48.5")
    rest = RING_A_TOTAL - Decimal("4850")
    invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[
        shop.cash("100", "USD"), PaymentInput(kind="card", currency_code="EGP", amount=str(rest))]))
    kinds = {(p.kind, p.currency.code): p.functional_amount for p in invoice.payments.all()}
    assert kinds == {("cash", "USD"): Decimal("4850.00"), ("card", "EGP"): rest}
    usd_cash = JournalLine.objects.get(entry=invoice.journal_entry, commodity__code="USD")
    assert (usd_cash.quantity, usd_cash.functional_amount) == (Decimal("100"), Decimal("4850"))


def test_payment_rules(shop):
    with pytest.raises(DomainError) as exc:
        post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash("100")]))
    assert exc.value.code == "SALES_NOT_FULLY_PAID"
    with pytest.raises(DomainError) as exc:
        post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit"))
    assert exc.value.code == "SALES_CUSTOMER_REQUIRED"
    with pytest.raises(DomainError) as exc:
        post_sale(shop.sale(shop.line(shop.ring_a), payments=[PaymentInput(
            kind="card", currency_code="EGP", amount=str(RING_A_TOTAL + 100))]))
    assert exc.value.code == "SALES_OVERPAID"
    # Cash overpayment is fine: the difference is change.
    invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash("20000")]))
    assert invoice.change_amount == Decimal("20000") - RING_A_TOTAL


def test_credit_sale_leaves_a_balance(shop):
    customer = create_customer(PartyData(name="Mona", phone="01012345678"))
    invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash("5000")],
                                  payment_terms="credit", customer_id=customer.pk))
    assert invoice.balance_amount == RING_A_TOTAL - 5000
    assert party_balance(customer) == {"EGP": RING_A_TOTAL - 5000}
    assert invoice.customer_phone == "01012345678"


def test_pieces_can_only_be_sold_once(shop):
    post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
    with pytest.raises(ValidationError) as exc:
        quote_sale(shop.sale(shop.line(shop.ring_a)))
    assert "0" in exc.value.fields["lines"]
    with pytest.raises(ValidationError):
        quote_sale(shop.sale(shop.line(shop.ring_b), shop.line(shop.ring_b)))


def test_void_puts_everything_back(shop):
    before = [(r.account.code, r.debit, r.credit) for r in trial_balance().rows]
    invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash("1000")],
                                  trade_ins=[TradeInInput(karat_id=shop.k21.pk,
                                                          gross_weight_g="5")]))
    void_sale(invoice.pk, reason="Customer changed mind")
    shop.ring_a.refresh_from_db()
    assert shop.ring_a.status == ItemStatus.IN_STOCK
    assert LotBalance.objects.get(lot__category__code="SCRAP").gross_weight_g == 0
    assert [(r.account.code, r.debit, r.credit) for r in trial_balance().rows] == before


def test_older_sales_need_void_any(shop, tenant_a, make_member):
    member = make_member(tenant_a, "cashier", "viewer")
    with tenant_context(tenant_a.id):
        from apps.iam.models import Role
        from apps.iam.services import assign_role, set_role_permissions

        role = Role.objects.create(code="cashier", name="Cashier")
        set_role_permissions(role, ["sales.invoice.create", "sales.invoice.void"])
        assign_role(member, role)
        invoice = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
        SalesInvoice.objects.filter(pk=invoice.pk).update(
            business_date=invoice.business_date - timedelta(days=1))
        with pytest.raises(PermissionDenied):
            void_sale(invoice.pk, actor=build_actor(member))


def test_quote_never_writes(shop):
    quote_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
    shop.ring_a.refresh_from_db()
    assert shop.ring_a.status == ItemStatus.IN_STOCK
    assert SalesInvoice.objects.count() == 0
