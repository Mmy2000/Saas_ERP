"""Opening balances: parties in money and gold, cash and banks, stock by weight, pieces with
their old barcodes, cancelling, and the Excel import."""

import io
from decimal import Decimal

import pytest

from apps.catalog.models import Currency, ItemCategory
from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item, ItemStatus, LotBalance
from apps.inventory.services import scrap_category
from apps.ledger.models import EntryKind
from apps.ledger.selectors import party_balance
from apps.opening.services import (
    HolderBalance,
    OpeningInput,
    PartyBalance,
    PieceBalance,
    StockBalance,
    post_opening,
    void_opening,
)
from apps.parties.services import PartyData, create_customer, create_supplier, create_workshop
from apps.sales.tests.test_sales import Shop
from apps.treasury.holders import balance, default_cash_box
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def _rings():
    return ItemCategory.objects.get(code="1010")


def test_opening_balances_post_and_cancel(tenant_a, shop):
    with tenant_context(tenant_a.id):
        customer = create_customer(PartyData(name="Ali"))
        supplier = create_supplier(PartyData(name="Gold Co"))
        workshop = create_workshop(PartyData(name="Hany workshop"))
        box = default_cash_box(shop.branch, Currency.objects.get(code="EGP"))
        scrap = scrap_category()
        doc = post_opening(OpeningInput(
            branch_id=shop.branch.pk, note="From Gweb",
            parties=[
                PartyBalance("customer", customer.pk, "5000", True, currency_code="EGP"),
                PartyBalance("supplier", supplier.pk, "100", False, karat_id=shop.k21.pk),
                PartyBalance("workshop", workshop.pk, "20", True, karat_id=shop.k18.pk),
            ],
            holders=[HolderBalance("box", box.pk, "20000"),
                     HolderBalance("bank", shop.bank.pk, "-1500")],
            stock=[StockBalance(scrap.pk, shop.k21.pk, "50")],
            pieces=[PieceBalance(_rings().pk, shop.k18.pk, "7.5", barcode="1010999001",
                                 cost_amount="300")],
        ))
        assert doc.status == DocStatus.POSTED and doc.number.startswith("01-OB-")
        assert doc.journal_entry.kind == EntryKind.OPENING
        assert party_balance(customer) == {"EGP": Decimal("5000.00")}
        assert party_balance(supplier) == {"XAU": Decimal("-87.5000")}  # 100 g × 875‰
        assert party_balance(workshop) == {"XAU": Decimal("15.0000")}  # 20 g × 750‰
        assert balance(box.account_id)[0] == Decimal("20000")
        assert balance(shop.bank.account_id)[0] == Decimal("-1500")
        piece = Item.objects.get(barcode="1010999001")
        assert (piece.status, piece.cost_amount, piece.fine_weight_g) == (
            ItemStatus.IN_STOCK, Decimal("300.00"), Decimal("5.6250"))
        lot = LotBalance.objects.get(lot__category=scrap, lot__karat=shop.k21)
        assert lot.gross_weight_g == Decimal("50.000")
        assert doc.lines.count() == 7

        void_opening(doc.pk, reason="Typed twice")
        doc.refresh_from_db()
        piece.refresh_from_db()
        lot.refresh_from_db()
        assert doc.status == DocStatus.VOIDED and piece.status == ItemStatus.VOIDED
        assert lot.gross_weight_g == 0 and party_balance(customer) == {}
        assert balance(box.account_id)[0] == 0


def test_a_used_piece_blocks_cancelling(tenant_a, shop):
    from apps.sales.services import post_sale

    with tenant_context(tenant_a.id):
        doc = post_opening(OpeningInput(branch_id=shop.branch.pk, pieces=[
            PieceBalance(_rings().pk, shop.k18.pk, "4", barcode="OLD-1")]))
        piece = Item.objects.get(barcode="OLD-1")
        post_sale(shop.sale(shop.line(piece), payments=[shop.cash("20000")]))
        with pytest.raises(DomainError) as error:
            void_opening(doc.pk)
        assert error.value.code == "OPENING_PIECE_USED"


def test_every_problem_is_reported_at_once(tenant_a, shop):
    from datetime import timedelta

    from django.utils import timezone

    with tenant_context(tenant_a.id):
        supplier = create_supplier(PartyData(name="Gold Co"))
        with pytest.raises(ValidationError) as error:
            post_opening(OpeningInput(
                branch_id=shop.branch.pk,
                parties=[PartyBalance("customer", supplier.pk, "10", True, currency_code="EGP"),
                         PartyBalance("supplier", supplier.pk, "-5", True,
                                      currency_code="EGP")],
                pieces=[PieceBalance(_rings().pk, None, "5"),
                        PieceBalance(_rings().pk, shop.k18.pk, "5",
                                     barcode=shop.ring_a.barcode),
                        PieceBalance(scrap_category().pk, shop.k21.pk, "5")]))
        fields = error.value.fields
        assert set(fields) == {"parties.0.party", "parties.1.amount", "pieces.0.karat",
                               "pieces.1.barcode", "pieces.2.category"}
        assert not Item.objects.filter(barcode__startswith="9").exists()  # nothing written

        with pytest.raises(ValidationError) as error:
            post_opening(OpeningInput(branch_id=shop.branch.pk,
                                      business_date=timezone.localdate() + timedelta(days=1),
                                      parties=[]))
        with pytest.raises(ValidationError):
            post_opening(OpeningInput(branch_id=shop.branch.pk))


def _xlsx(rows) -> io.BytesIO:
    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    data = io.BytesIO()
    book.save(data)
    data.seek(0)
    data.name = "pieces.xlsx"
    return data


def _import(client, branch, upload, dry_run=True):
    import uuid

    return client.post("/api/v1/opening/import/", {
        "file": upload, "branch": branch.pk, "dry_run": "true" if dry_run else "false"},
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)


def test_excel_import_checks_then_imports(tenant_a, shop):
    client = login(tenant_a, language="en")
    header = ["الباركود", "Category", "العيار", "Weight", "Making cost", "Supplier"]
    bad = _xlsx([header,
                 ["2020000001", "1010", "18", "6.250", "200", "Factory"],
                 ["2020000002", "NOPE", "18", "3", "", ""],
                 [shop.ring_a.barcode, "Rings", "21K", "4", "", ""],
                 ["2020000003", "1010", "18", "x", "", "Nobody"]])
    result = _import(client, shop.branch, bad).json()
    assert result["ok"] is False
    problems = {(p["line"], p["column"]) for p in result["problems"]}
    assert {(3, "Category"), (4, "Barcode"), (5, "Supplier")} <= problems
    with tenant_context(tenant_a.id):
        assert not Item.objects.filter(barcode="2020000001").exists()

    good = [header, ["2020000001", "1010", "18", "6.250", "200", "Factory"],
            ["", "Rings", "21K", "4", "", ""]]
    result = _import(client, shop.branch, _xlsx(good)).json()
    assert result["ok"] is True and result["rows"] == 2
    assert {k["karat"]: k["pieces"] for k in result["by_karat"]} == {"18": 1, "21K": 1}
    with tenant_context(tenant_a.id):
        assert not Item.objects.filter(barcode="2020000001").exists()  # a check saves nothing

    done = _import(client, shop.branch, _xlsx(good), dry_run=False)
    assert done.status_code == 201, done.content
    with tenant_context(tenant_a.id):
        piece = Item.objects.get(barcode="2020000001")
        assert (piece.cost_amount, piece.supplier.name) == (Decimal("200.00"), "Factory")
        assert Item.objects.filter(category__code="1010", karat=shop.k21).count() == 1
    page = client.get(done.json()["url"])
    assert page.status_code == 200 and b"2020000001" in page.content

    csv = io.BytesIO(b"Barcode,Category,Karat,Weight\nC-1,1010,18,2.5\n")
    csv.name = "pieces.csv"
    assert _import(client, shop.branch, csv).json()["ok"] is True
    junk = io.BytesIO(b"not a spreadsheet")
    junk.name = "x.xlsx"
    assert _import(client, shop.branch, junk).status_code == 400


def test_pages_api_and_permissions(tenant_a, shop, make_member):
    client = login(tenant_a, language="en")
    for url in ("/accounting/opening/", "/accounting/opening/new/",
                "/accounting/opening/import/"):
        assert client.get(url).status_code == 200, url
    template = client.get("/accounting/opening/import/template.xlsx")
    assert template.status_code == 200 and template.content.startswith(b"PK")

    with tenant_context(tenant_a.id):
        customer = create_customer(PartyData(name="Mona"))
    lookup = client.get("/api/v1/opening/parties/?role=customer&q=Mon").json()
    assert [p["id"] for p in lookup["results"]] == [customer.pk]
    response = client.post("/api/v1/opening/", {
        "branch": shop.branch.pk, "parties": [{"role": "customer", "party": customer.pk,
                                               "amount": "750", "owes_us": True,
                                               "currency": "EGP"}],
    }, content_type="application/json", HTTP_IDEMPOTENCY_KEY="o1")
    assert response.status_code == 201, response.content
    detail = client.get(response.json()["url"])
    assert detail.status_code == 200 and b"Mona" in detail.content

    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer")
    assert viewer.get("/accounting/opening/").status_code == 200
    assert viewer.get("/accounting/opening/new/").status_code == 403
    assert viewer.post("/api/v1/opening/", {}, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="o2").status_code == 403
