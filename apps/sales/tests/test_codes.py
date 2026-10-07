"""One-time codes to the customer's phone before a sale (legacy GHC16/GHC30 OTP)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.core.errors import DomainError, PermissionDenied, ValidationError
from apps.core.tenancy import tenant_context
from apps.iam.authz import build_actor
from apps.iam.models import Membership, Role, RolePermission
from apps.iam.services import assign_role
from apps.messaging.codes import OUTBOX
from apps.parties.services import PartyData, create_customer
from apps.platform.tenants import features
from apps.platform.tenants.models import TenantFeature
from apps.sales import codes
from apps.sales.models import SaleCode
from apps.sales.reservations import ReservationInput, complete_reservation, create_reservation
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db


def switch_on(tenant):
    TenantFeature.objects.update_or_create(tenant=tenant, key="sale_codes",
                                           defaults={"enabled": True})
    features.forget(tenant.pk)


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id), translation.override("en"):
        shop = Shop()
        shop.mona = create_customer(PartyData(name="Mona", phone="01012345678"))
        yield shop


def credit_sale(shop, **kw):
    return shop.sale(shop.line(shop.ring_a), payment_terms="credit", customer_id=shop.mona.pk,
                     **kw)


def last_code() -> str:
    return OUTBOX[-1].code


def test_off_unless_the_platform_switches_it_on(shop):
    assert not codes.need(total=RING_A_TOTAL, payment_terms="credit", customer=shop.mona).needed
    invoice = post_sale(credit_sale(shop))
    assert invoice.code_check == ""


def test_a_sale_on_account_needs_the_customers_code(shop, tenant_a):
    switch_on(tenant_a)
    with pytest.raises(DomainError) as exc:
        post_sale(credit_sale(shop))
    assert exc.value.code == "SALE_CODE_REQUIRED"

    sale_code = codes.request_code(branch_id=shop.branch.pk, amount=RING_A_TOTAL,
                                   customer_id=shop.mona.pk)
    assert sale_code.phone == "+201012345678" and len(last_code()) == codes.LENGTH
    assert OUTBOX[-1].customer_name == "Mona" and last_code() in OUTBOX[-1].text
    assert last_code() not in sale_code.code_hash  # only a keyed hash is stored
    wrong = "000000" if last_code() != "000000" else "111111"
    with pytest.raises(ValidationError) as exc:
        codes.verify(sale_code.pk, wrong)
    assert "4 tries left" in exc.value.fields["code"][0]
    codes.verify(sale_code.pk, last_code())

    with pytest.raises(DomainError):  # the code is for this amount only
        post_sale(shop.sale(shop.line(shop.ring_a, discount="0.05"), payment_terms="credit",
                            customer_id=shop.mona.pk, code_id=sale_code.pk))
    invoice = post_sale(credit_sale(shop, code_id=sale_code.pk))
    assert invoice.code_check == "confirmed"
    sale_code.refresh_from_db()
    assert sale_code.invoice == invoice
    with pytest.raises(DomainError):  # used up
        post_sale(shop.sale(shop.line(shop.ring_b), payment_terms="credit",
                            customer_id=shop.mona.pk, code_id=sale_code.pk))

    # A cash sale needs none, unless the shop says so for large amounts.
    assert post_sale(shop.sale(shop.line(shop.ring_b), payments=[
        shop.cash(Decimal("22071.42"))])).code_check == ""
    codes.save_rules(on_account=True, on_reservations=True, above_amount="10000")
    assert codes.need(total=Decimal("22071.42"), payment_terms="cash", customer=None).needed
    assert not codes.need(total=Decimal("9000"), payment_terms="cash", customer=None).needed
    with pytest.raises(ValidationError) as exc:  # a walk-in without a phone
        codes.request_code(branch_id=shop.branch.pk, amount="22071.42")
    assert exc.value.code == "SALE_CODE_NO_PHONE"


def test_tries_expiry_resending_and_skipping(shop, tenant_a, make_member):
    switch_on(tenant_a)
    sale_code = codes.request_code(branch_id=shop.branch.pk, amount=RING_A_TOTAL,
                                   customer_id=shop.mona.pk)
    with pytest.raises(DomainError) as exc:  # one a minute
        codes.request_code(branch_id=shop.branch.pk, amount=RING_A_TOTAL,
                           customer_id=shop.mona.pk)
    assert exc.value.code == "SALE_CODE_TOO_SOON"
    with pytest.raises(DomainError):
        codes.resend(sale_code.pk)
    SaleCode.objects.filter(pk=sale_code.pk).update(
        last_sent_at=timezone.now() - timedelta(minutes=2))
    first = last_code()
    codes.resend(sale_code.pk)
    assert len(OUTBOX) >= 2 and SaleCode.objects.get(pk=sale_code.pk).sends == 2
    if first != last_code():
        with pytest.raises(ValidationError):  # the old code no longer works
            codes.verify(sale_code.pk, first)

    for _try in range(codes.MAX_ATTEMPTS):
        try:
            codes.verify(sale_code.pk, "999999" if last_code() != "999999" else "888888")
        except (ValidationError, DomainError):  # wrong, then too many
            pass
    with pytest.raises(DomainError) as exc:
        codes.verify(sale_code.pk, last_code())
    assert exc.value.code == "SALE_CODE_TOO_MANY"

    SaleCode.objects.filter(pk=sale_code.pk).update(attempts=0,
                                                    expires_at=timezone.now() - timedelta(1))
    with pytest.raises(DomainError) as exc:
        codes.verify(sale_code.pk, last_code())
    assert exc.value.code == "SALE_CODE_EXPIRED"

    # Skipping: only with the permission.
    seller = make_member(tenant_a, "seller", "viewer")
    with tenant_context(tenant_a.id):
        role = Role.objects.create(code="till", name="Till")
        RolePermission.objects.bulk_create([RolePermission(role=role, permission=code)
                                            for code in ("sales.invoice.create",)])
        assign_role(Membership.objects.get(pk=seller.pk), role)
        actor = build_actor(Membership.objects.get(pk=seller.pk))
        with pytest.raises(PermissionDenied):
            post_sale(credit_sale(shop, skip_code=True), actor=actor)
        invoice = post_sale(credit_sale(shop, skip_code=True))  # the system / an owner
        assert invoice.code_check == "skipped"


def test_reservation_pick_up_needs_the_code(shop, tenant_a):
    switch_on(tenant_a)
    reservation = create_reservation(ReservationInput(
        branch_id=shop.branch.pk, customer_id=shop.mona.pk, lines=(shop.line(shop.ring_a),),
        deposit=shop.cash("5000")))
    with pytest.raises(DomainError) as exc:
        complete_reservation(reservation.pk, payments=(shop.cash(RING_A_TOTAL - 5000),))
    assert exc.value.code == "SALE_CODE_REQUIRED"
    sale_code = codes.request_code(branch_id=shop.branch.pk, amount=None,
                                   reservation_id=reservation.pk)
    assert sale_code.amount == RING_A_TOTAL and sale_code.customer == shop.mona
    codes.verify(sale_code.pk, last_code())
    invoice = complete_reservation(reservation.pk, payments=(shop.cash(RING_A_TOTAL - 5000),),
                                   code_id=sale_code.pk)
    assert invoice.code_check == "confirmed"


def test_api_and_pages(tenant_a, shop):
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    sale = {"branch": shop.branch.pk, "lines": [{"barcode": shop.ring_a.barcode}],
            "payment_terms": "credit", "customer": shop.mona.pk}
    quote = client.post("/api/v1/sales/quote/", sale, content_type="application/json").json()
    assert quote["code"]["needed"] and quote["code"]["phone"] == "+20 ••• ••• 5678"
    assert quote["code"]["can_skip"]
    refused = client.post("/api/v1/sales/invoices/", sale, content_type="application/json",
                          HTTP_IDEMPOTENCY_KEY="s1")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "SALE_CODE_REQUIRED"
    sent = client.post("/api/v1/sales/codes/", {"branch": shop.branch.pk,
                                                "amount": quote["totals"]["total"],
                                                "customer": shop.mona.pk},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="c1")
    assert sent.status_code == 201, sent.content
    code_id = sent.json()["id"]
    wrong = client.post(f"/api/v1/sales/codes/{code_id}/verify/", {"code": "12"},
                        content_type="application/json", HTTP_IDEMPOTENCY_KEY="v0")
    assert wrong.status_code in (400, 422) and "code" in wrong.json()["error"]["fields"]
    right = client.post(f"/api/v1/sales/codes/{code_id}/verify/", {"code": last_code()},
                        content_type="application/json", HTTP_IDEMPOTENCY_KEY="v1")
    assert right.json()["verified"] is True
    made = client.post("/api/v1/sales/invoices/", {**sale, "code": code_id},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="s2")
    assert made.status_code == 201, made.content

    saved = client.put("/api/v1/sales/codes/rules/", {"on_account": True,
                                                      "on_reservations": False,
                                                      "above_amount": "50000"},
                       content_type="application/json")
    assert saved.status_code == 200 and saved.json()["above_amount"] == "50000.00"
    page = client.get("/settings/sale-codes/")
    assert page.status_code == 200 and "+20 ••• ••• 5678" in page.content.decode()
    assert "Code confirmed" in client.get(f"/sales/{made.json()['id']}/").content.decode()
    assert client.get("/sales/new/").status_code == 200
