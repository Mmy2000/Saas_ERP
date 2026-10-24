"""HR follows branches: a role limited to some branches sees and changes only those branches'
employees. Employees with no branch, and monthly payrolls (they pay everyone), need the
permission for every branch."""

import pytest
from django.utils import timezone

from apps.core.errors import PermissionDenied
from apps.core.tenancy import tenant_context
from apps.hr.models import AdjustmentKind, Employee
from apps.hr.services import (
    EmployeeInput,
    add_adjustment,
    create_employee,
    post_payroll,
    update_employee,
    visible_employees,
)
from apps.iam.authz import build_actor
from apps.iam.models import Role
from apps.iam.services import set_role_permissions
from apps.org.models import Branch
from conftest import login

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()
HR = ["hr.employee.view", "hr.employee.manage", "hr.advance.create", "hr.payroll.view",
      "hr.payroll.post"]


@pytest.fixture
def staff(tenant_a, make_member):
    """Hana does HR for the head office only; Ali works there, Badr at the second branch, and
    Kamal (no branch) for the whole company."""
    with tenant_context(tenant_a.id):
        head = Branch.objects.get(code=1)
        second = Branch.objects.create(code=2, name="Second")
        set_role_permissions(Role.objects.create(code="branch-hr", name="Branch HR"), HR)
    hana = make_member(tenant_a, "hana", "branch-hr", branches=[head])
    with tenant_context(tenant_a.id):
        people = {name: create_employee(EmployeeInput(name=name, monthly_salary="1000",
                                                      branch_id=branch))
                  for name, branch in (("Ali", head.pk), ("Badr", second.pk), ("Kamal", None))}
    return {"hana": hana, "head": head, "second": second, **people}


def test_a_branch_hr_role_sees_and_changes_only_its_branch(tenant_a, staff):
    with tenant_context(tenant_a.id):
        hana = build_actor(staff["hana"])
        assert set(visible_employees(hana).values_list("name", flat=True)) == {"Ali"}
        update_employee(staff["Ali"].pk, EmployeeInput(name="Ali H", branch_id=staff["head"].pk),
                        actor=hana)
        for refused in (
            lambda: update_employee(staff["Badr"].pk, EmployeeInput(
                name="Badr", branch_id=staff["second"].pk), actor=hana),
            lambda: update_employee(staff["Ali"].pk, EmployeeInput(  # moved out of her branch
                name="Ali", branch_id=staff["second"].pk), actor=hana),
            lambda: update_employee(staff["Kamal"].pk, EmployeeInput(name="Kamal"), actor=hana),
            lambda: create_employee(EmployeeInput(name="New", branch_id=staff["second"].pk),
                                    actor=hana),
            lambda: add_adjustment(staff["Badr"].pk, AdjustmentKind.BONUS, "100", TODAY.year,
                                   TODAY.month, actor=hana),
            lambda: post_payroll(TODAY.year, TODAY.month, branch_id=staff["head"].pk,
                                 actor=hana),  # it would pay Badr and Kamal too
        ):
            with pytest.raises(PermissionDenied):
                refused()
        assert Employee.objects.get(pk=staff["Ali"].pk).branch_id == staff["head"].pk


def test_pages_and_api_follow_the_branch(tenant_a, staff):
    client = login(tenant_a, "hana")
    listing = client.get("/hr/").content.decode()
    assert "Ali" in listing and "Badr" not in listing and "Kamal" not in listing
    assert client.get(f"/hr/{staff['Ali'].pk}/").status_code == 200
    assert client.get(f"/hr/{staff['Badr'].pk}/").status_code == 404
    assert client.get("/hr/payroll/").status_code == 403
    names = [row["name"] for row in client.get("/api/v1/hr/employees/").json()["results"]]
    assert names == ["Ali"]
    assert client.patch(f"/api/v1/hr/employees/{staff['Badr'].pk}/", {"name": "X"},
                        content_type="application/json").status_code == 404
    assert client.get("/api/v1/hr/payroll/").status_code == 403

    owner = login(tenant_a)  # every branch: everyone and the payrolls
    assert all(name in owner.get("/hr/").content.decode() for name in ("Ali", "Badr", "Kamal"))
    assert owner.get("/hr/payroll/").status_code == 200
