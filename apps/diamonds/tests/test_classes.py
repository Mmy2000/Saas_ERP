"""Diamond discount classes (legacy Offd1, GHD10 / GHD42)."""

from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import services
from apps.diamonds.tests.test_diamonds import receive_ring, shop, switch_on  # noqa: F401
from apps.iam.authz import build_actor
from apps.iam.models import Membership, MembershipLimit, Role, RolePermission
from apps.iam.services import assign_role
from apps.inventory.models import Item
from apps.sales.services import SaleLineInput, quote_sale
from conftest import login

pytestmark = pytest.mark.django_db


def standard():
    return services.save_discount_class(services.DiscountClassInput(
        code=1, name="Standard", usual_percent="10", max_percent="25"))


def test_a_class_sets_the_usual_and_the_largest_discount(shop):  # noqa: F811
    with pytest.raises(ValidationError) as exc:
        services.save_discount_class(services.DiscountClassInput(
            code=1, name="Odd", usual_percent="30", max_percent="20"))
    assert "max_percent" in exc.value.fields
    usual = standard()
    assert (usual.usual_rate, usual.max_rate) == (Decimal("0.1000"), Decimal("0.2500"))
    ring = receive_ring(shop)  # label 60,000, cost 17,321.14 + 505.20 + 30,000
    ring.discount_class = usual
    ring.save()

    # Nothing entered: the usual 10 %.
    line = quote_sale(shop.sale(SaleLineInput(barcode=ring.barcode))).lines[0]
    assert line.discount_rate == Decimal("0.1") and line.line_total == Decimal("54000.00")
    # An explicit discount still applies; the system (no actor) may give up to the class's 25 %.
    line = quote_sale(shop.sale(shop.line(ring, "0.2"))).lines[0]
    assert line.line_total == Decimal("48000.00")
    with pytest.raises(ValidationError):
        quote_sale(shop.sale(shop.line(ring, "0.3")))


def test_the_sellers_limit_and_the_class(shop, tenant_a, make_member):  # noqa: F811
    usual = standard()
    ring = receive_ring(shop)
    ring.discount_class = usual
    ring.save()
    member = make_member(tenant_a, "seller", "viewer")
    with tenant_context(tenant_a.id):
        role = Role.objects.create(code="till", name="Till")
        RolePermission.objects.bulk_create([RolePermission(role=role, permission=code) for code in
                                            ("sales.invoice.create", "diamonds.sell")])
        membership = Membership.objects.get(pk=member.pk)
        assign_role(membership, role)
        MembershipLimit.objects.create(membership=membership, key="diamonds.discount.max_rate",
                                       value=Decimal("0.05"))
        actor = build_actor(Membership.objects.get(pk=member.pk))
        # Her own limit is 5 %, but anyone may give the class's usual 10 %…
        line = quote_sale(shop.sale(SaleLineInput(barcode=ring.barcode)), actor=actor).lines[0]
        assert line.discount_rate == Decimal("0.1")
        # …and no more.
        with pytest.raises(ValidationError):
            quote_sale(shop.sale(shop.line(ring, "0.12")), actor=actor)


def test_classes_in_bulk_on_receipt_and_on_the_piece(shop):  # noqa: F811
    usual = standard()
    solitaire = services.save_discount_class(services.DiscountClassInput(
        code=2, name="Solitaires", usual_percent="0", max_percent="5"))
    first, second = receive_ring(shop), receive_ring(shop)
    assert services.assign_discount_class(usual.pk, family="diamond") == 2
    assert services.assign_discount_class(usual.pk, family="diamond") == 0  # already
    assert services.assign_discount_class(solitaire.pk, term=first.barcode) == 1
    assert Item.objects.get(pk=first.pk).discount_class == solitaire
    assert services.assign_discount_class(None, current=str(usual.pk)) == 1  # second, cleared
    assert Item.objects.get(pk=second.pk).discount_class is None

    services.update_item(second.pk, stones=[], discount_class_id=usual.pk)
    assert Item.objects.get(pk=second.pk).discount_class == usual
    services.update_item(second.pk, stones=[])  # unchanged when not given
    assert Item.objects.get(pk=second.pk).discount_class == usual

    invoice = services.receive(services.ReceiveInput(
        supplier_id=shop.dealer.pk, branch_id=shop.branch.pk, currency_code="EGP",
        pieces=(services.PieceInput(category_id=shop.mountings.pk, karat_id=shop.k18.pk,
                                    gross_weight_g="4", label_price="20000",
                                    discount_class_id=solitaire.pk),)))
    piece = Item.objects.get(pk=invoice.lines.get().pieces.get().item_id)
    assert piece.discount_class == solitaire


def test_api_and_pages(tenant_a):
    cache.clear()
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/diamonds/discount-classes/", {
        "code": "3", "name": "Fancy", "usual_percent": "12.5", "max_percent": "20"},
        content_type="application/json")
    assert made.status_code == 201, made.content
    edited = client.put(f"/api/v1/diamonds/discount-classes/{made.json()['id']}/", {
        "code": "3", "name": "Fancy colours", "usual_percent": "12.5", "max_percent": "22",
        "is_active": True}, content_type="application/json")
    assert edited.status_code == 200
    page = client.get("/diamonds/discount-classes/").content.decode()
    assert "Fancy colours" in page and "12.5%" in page and "22%" in page
    assigned = client.post("/api/v1/diamonds/discount-classes/assign/", {
        "discount_class": made.json()["id"], "family": "diamond"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="a1")
    assert assigned.status_code == 200 and assigned.json() == {"count": 0}
    assert client.get("/diamonds/?cls=none").status_code == 200
    assert client.get("/diamonds/receive/").status_code == 200
