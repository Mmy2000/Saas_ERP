from decimal import Decimal

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.audit.models import AuditAction, AuditEvent
from apps.core.tenancy import tenant_context
from apps.expenses.models import ExpenseCategory
from apps.hr.services import EmployeeInput, create_employee, update_employee
from apps.iam.models import Membership
from apps.inventory.models import Item
from apps.ledger.models import Account
from apps.parties.services import PartyData, create_customer, update_customer
from apps.sales.tests.test_sales import Shop

pytestmark = pytest.mark.django_db


def events(table, row_id=None):
    queryset = AuditEvent.objects.filter(table_name=table).order_by("id")
    return list(queryset if row_id is None else queryset.filter(row_id=row_id))


def as_user(user):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.user_id', %s, true)", [str(user.pk)])


def test_changes_are_recorded_with_who(tenant_a):
    with tenant_context(tenant_a.id):
        owner = Membership.objects.get(username="owner").user
        as_user(owner)
        mona = create_customer(PartyData(name="Mona", phone="01001234567"))
        update_customer(mona.pk, PartyData(name="Mona Ali", phone="01001234567"))
        added, changed = events("parties_party", mona.pk)
        assert added.action == AuditAction.INSERT and added.changes["name"] == "Mona"
        assert added.user == owner
        assert changed.action == AuditAction.UPDATE
        assert changed.changes == {"name": ["Mona", "Mona Ali"]}  # only what changed

        account = Account.objects.get(role="expenses")
        rent = ExpenseCategory.objects.create(name="Rent", account=account)
        rent.delete()
        deleted = events("expenses_expensecategory", rent.pk)[-1]
        assert deleted.action == AuditAction.DELETE and deleted.changes["name"] == "Rent"


def test_secrets_are_masked_and_the_trail_is_append_only(tenant_a):
    with tenant_context(tenant_a.id):
        hany = create_employee(EmployeeInput(name="Hany", national_id="29001011234567"))
        update_employee(hany.pk, EmployeeInput(name="Hany", national_id="29001019999999"))
        added, changed = events("hr_employee", hany.pk)
        assert added.changes["national_id_encrypted"] == "***"
        assert changed.changes["national_id_encrypted"] == "***"
        assert "29001011234567" not in str(added.changes)
        with pytest.raises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(pk=added.pk).update(action="D")
        with pytest.raises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(pk=added.pk).delete()


def test_items_record_edits_only(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        ring = shop.ring_a
        assert events("inventory_item") == []  # bought: not recorded
        Item.objects.filter(pk=ring.pk).update(status="sold")  # sales move them: not recorded
        assert events("inventory_item") == []
        Item.objects.filter(pk=ring.pk).update(list_making_rate=Decimal("300"))
        (edit,) = events("inventory_item", ring.pk)
        assert edit.changes["list_making_rate"][1] == 300


def test_a_row_cannot_point_at_another_tenant(tenant_a, tenant_b):
    with tenant_context(tenant_b.id):
        foreign_root = Account.objects.get(code="1").pk
        foreign_expenses = Account.objects.get(role="expenses").pk
    with tenant_context(tenant_a.id):
        own_root = Account.objects.get(code="1").pk
        with pytest.raises(IntegrityError, match="cross-tenant"), transaction.atomic():
            Account.objects.filter(code="11").update(parent_id=foreign_root)
        with pytest.raises(IntegrityError, match="cross-tenant"), transaction.atomic():
            ExpenseCategory.objects.create(name="Leak", account_id=foreign_expenses)
        Account.objects.filter(code="11").update(parent_id=own_root)  # same tenant: fine


def test_activity_pages_and_who(tenant_a, make_member):
    from conftest import login

    client = login(tenant_a, language="en")
    made = client.post("/api/v1/parties/customers/", {"name": "Hoda", "phone": "01012345678"},
                       content_type="application/json")
    assert made.status_code == 201, made.content
    party = made.json()["id"]
    with tenant_context(tenant_a.id):
        (added,) = events("parties_party", party)
        assert added.user.memberships.get().username == "owner"  # from the request
    page = client.get("/settings/activity/").content.decode()
    assert "Hoda" in page and "Customer or supplier" in page
    history = client.get(f"/settings/activity/?table=parties_party&row={party}")
    assert history.status_code == 200 and "History" in history.content.decode()
    for path in ("/settings/activity/?table=inventory_item", "/settings/activity/?from=2026-01-01",
                 f"/customers/{party}/"):
        assert client.get(path).status_code == 200, path
    make_member(tenant_a, "clerk", "manager")
    make_member(tenant_a, "reader", "viewer")
    for username in ("clerk", "reader"):  # owners only, by default
        assert login(tenant_a, username).get("/settings/activity/").status_code == 403
