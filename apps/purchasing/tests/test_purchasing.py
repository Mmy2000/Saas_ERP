from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.catalog.models import Karat, ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item, ItemStatus, LotBalance, MovementType, StockMovement
from apps.inventory.selectors import stock_by_karat
from apps.inventory.services import DocRef, change_item_status
from apps.ledger.models import JournalEntry
from apps.ledger.selectors import party_balance, trial_balance
from apps.org.models import Branch
from apps.parties.services import PartyData, create_supplier
from apps.pricing.services import (
    KaratPriceInput,
    PublishPriceBoardCommand,
    publish_price_board,
    record_fx_rate,
)
from apps.purchasing.services import (
    InvoiceInput,
    LineInput,
    delete_draft,
    post_invoice,
    save_draft,
    void_invoice,
)

pytestmark = pytest.mark.django_db
DAY = timezone.localdate()  # rates and boards are looked up as of the invoice date


class Shop:
    def __init__(self):
        self.branch = Branch.objects.get(code=1)
        self.k18 = Karat.objects.get(code=18)
        self.k21 = Karat.objects.get(code=21)
        self.silver = Karat.objects.get(code=925)
        self.rings = create_item_category(CreateItemCategoryCommand(
            code="1010", name="Rings", product_family=ProductFamily.GOLD,
            tracking=Tracking.SERIALIZED, barcode_prefix=1010))
        self.chain = create_item_category(CreateItemCategoryCommand(
            code="2020", name="Chain by weight", product_family=ProductFamily.GOLD,
            tracking=Tracking.BULK))
        self.supplier = create_supplier(PartyData(name="Cairo Gold Factory"))

    def invoice(self, *lines, currency="EGP", fx_rate=None):
        return save_draft(InvoiceInput(
            supplier_id=self.supplier.pk, branch_id=self.branch.pk, business_date=DAY,
            currency_code=currency, fx_rate=fx_rate, lines=tuple(lines)))

    def rings_line(self, *weights, rate="150", list_rate="250"):
        return LineInput(category_id=self.rings.pk, karat_id=self.k18.pk,
                         piece_weights=tuple(weights), making_cost_rate=rate,
                         list_making_rate=list_rate)

    def chain_line(self, weight, qty=0, rate="40"):
        return LineInput(category_id=self.chain.pk, karat_id=self.k21.pk, gross_weight_g=weight,
                         qty=qty, making_cost_rate=rate)


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def test_posting_serialized_pieces(shop):
    draft = shop.invoice(shop.rings_line("5.25", "6.10"))
    assert (draft.status, draft.number) == (DocStatus.DRAFT, None)
    invoice = post_invoice(draft.pk)
    assert invoice.number == "01-PI-2026-000001"

    items = list(Item.objects.order_by("barcode"))
    assert [i.barcode for i in items] == ["1010000001", "1010000002"]
    assert [i.fine_weight_g for i in items] == [Decimal("3.9375"), Decimal("4.5750")]
    assert all(i.status == ItemStatus.IN_STOCK and i.supplier_id == shop.supplier.pk for i in items)
    assert items[0].cost_amount == Decimal("787.50")  # 150 × 5.25
    assert items[0].list_making_rate == Decimal("250")
    assert StockMovement.objects.filter(movement_type=MovementType.PURCHASE_RECEIPT).count() == 2

    # The supplier is owed the gold in fine grams and the making charges in money.
    assert party_balance(shop.supplier) == {"XAU": Decimal("-8.5125"), "EGP": Decimal("-1702.50")}
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit


def test_metal_is_valued_at_the_board_price(shop):
    publish_price_board(PublishPriceBoardCommand(
        reference=KaratPriceInput(shop.k21.pk, "4000", "3950")))
    invoice = post_invoice(shop.invoice(shop.rings_line("10", rate="0")).pk)
    entry = JournalEntry.objects.get(pk=invoice.journal_entry_id)
    gold_line = entry.lines.get(commodity__code="XAU", quantity__gt=0)
    # 7.5 fine g at the 24K buy price per pure gram: 3950 × 999.9/875 = 4513.83 per 24K g,
    # / 0.9999 = 4514.2814 per fine g.
    assert gold_line.quantity == Decimal("7.5")
    assert gold_line.functional_amount == Decimal("33857.11")


def test_bulk_lines_go_to_a_lot(shop):
    post_invoice(shop.invoice(shop.chain_line("50.5", qty=10)).pk)
    balance = LotBalance.objects.get(lot__category=shop.chain)
    assert (balance.qty, balance.gross_weight_g, balance.fine_weight_g) == (
        10, Decimal("50.500"), Decimal("44.1875"))
    assert balance.cost_amount == Decimal("2020.00")
    [line] = [s for s in stock_by_karat() if s.karat == shop.k21]
    assert (line.bulk_weight_g, line.equivalent_21k_g) == (Decimal("50.500"), Decimal("50.500"))


def test_making_charges_in_dollars(shop):
    record_fx_rate("USD", "48.5")
    invoice = post_invoice(shop.invoice(shop.rings_line("2", rate="50"), currency="USD").pk)
    assert invoice.fx_rate == Decimal("48.5")
    balance = party_balance(shop.supplier)
    assert balance["USD"] == Decimal("-100")
    assert Item.objects.get().cost_amount == Decimal("4850.00")


def test_draft_validation(shop):
    with pytest.raises(ValidationError) as exc:
        shop.invoice(LineInput(category_id=shop.rings.pk, karat_id=shop.k18.pk))
    assert "piece_weights" in exc.value.fields["lines"]["0"]
    with pytest.raises(ValidationError) as exc:
        shop.invoice(LineInput(category_id=shop.rings.pk, karat_id=shop.silver.pk,
                               piece_weights=("3",)))
    assert "karat" in exc.value.fields["lines"]["0"]
    with pytest.raises(ValidationError):
        shop.invoice()


def test_only_drafts_are_edited_or_deleted(shop):
    draft = shop.invoice(shop.rings_line("3"))
    post_invoice(draft.pk)
    with pytest.raises(DomainError):
        post_invoice(draft.pk)
    with pytest.raises(DomainError):
        delete_draft(draft.pk)
    other = shop.invoice(shop.rings_line("3"))
    delete_draft(other.pk)


def test_void_reverses_stock_and_ledger(shop):
    invoice = post_invoice(shop.invoice(shop.rings_line("5", "6"), shop.chain_line("20")).pk)
    void_invoice(invoice.pk, reason="Wrong supplier")
    invoice.refresh_from_db()
    assert invoice.status == DocStatus.VOIDED
    assert set(Item.objects.values_list("status", flat=True)) == {ItemStatus.VOIDED}
    assert LotBalance.objects.get().gross_weight_g == 0
    assert party_balance(shop.supplier) == {}
    assert trial_balance().rows == []


def test_void_refused_once_goods_have_left(shop):
    invoice = post_invoice(shop.invoice(shop.rings_line("5")).pk)
    item = Item.objects.get()
    change_item_status(item.pk, to=ItemStatus.SOLD, allowed_from=(ItemStatus.IN_STOCK,),
                       movement_type=MovementType.SALE, business_date=DAY,
                       doc=DocRef("test", None))
    with pytest.raises(DomainError) as exc:
        void_invoice(invoice.pk)
    assert exc.value.code == "INVENTORY_ITEM_UNAVAILABLE"
    invoice.refresh_from_db()
    assert invoice.status == DocStatus.POSTED  # nothing half-done


def test_stock_history_is_append_only(shop):
    post_invoice(shop.invoice(shop.rings_line("5")).pk)
    movement = StockMovement.objects.get()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE inventory_stockmovement SET qty = 5 WHERE id = %s", [movement.pk])
