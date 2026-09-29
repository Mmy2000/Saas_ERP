"""ORM-level isolation: fail-closed managers and the write guard (§5.3 "ORM", "Write guard")."""

import pytest

from apps.core.tenancy import TenantContextError, tenant_context
from apps.iam.models import Membership
from apps.org.models import Branch

pytestmark = pytest.mark.django_db


def test_queries_without_context_raise(tenant_a):
    with pytest.raises(TenantContextError):
        Branch.objects.count()
    with pytest.raises(TenantContextError):
        list(Branch.objects.filter(code=1))
    with pytest.raises(TenantContextError):
        Branch.objects.create(code=9, name="nowhere")


def test_queryset_built_outside_context_binds_when_used_inside(tenant_a, tenant_b):
    unbound = Branch.objects.all()  # like a DRF class attribute, built at import time
    with pytest.raises(TenantContextError):
        list(unbound)

    with tenant_context(tenant_a.id):
        Branch.objects.create(code=2, name="A2")
        assert {b.tenant_id for b in unbound.all()} == {tenant_a.id}
    with tenant_context(tenant_b.id):
        assert {b.tenant_id for b in unbound.all()} == {tenant_b.id}
        assert unbound.all().count() == 1


def test_class_level_queryset_first_built_inside_a_request(tenant_a, tenant_b):
    # Regression: a view module imported during tenant A's request must not pin its
    # class-level queryset to A.
    with tenant_context(tenant_a.id):
        class_attr = Branch.objects.all()
        assert [b.tenant_id for b in class_attr.all()] == [tenant_a.id]
    with tenant_context(tenant_b.id):
        assert [b.tenant_id for b in class_attr.all()] == [tenant_b.id]
        assert class_attr.all().count() == 1
    assert class_attr._tenant_id is None  # never mutated


def test_evaluated_queryset_cannot_cross_into_another_tenant(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        qs_a = Branch.objects.filter(is_active=True)
        list(qs_a)  # result cache now holds tenant A rows
    with tenant_context(tenant_b.id):
        with pytest.raises(TenantContextError):
            list(qs_a)
        with pytest.raises(TenantContextError):
            qs_a[0]
        with pytest.raises(TenantContextError):
            list(qs_a.filter(code=1))


def test_union_is_scoped(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        Branch.objects.create(code=2, name="A2")
        head = Branch.objects.filter(code=1).values_list("tenant_id", flat=True)
        other = Branch.objects.filter(code=2).values_list("tenant_id", flat=True)
        assert sorted(head.union(other, all=True)) == [tenant_a.id, tenant_a.id]


def test_save_stamps_tenant(tenant_a):
    with tenant_context(tenant_a.id):
        branch = Branch.objects.create(code=3, name="Stamped")
        assert branch.tenant_id == tenant_a.id


def test_save_refuses_foreign_tenant_object(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        branch_a = Branch.objects.get(code=1)
    with tenant_context(tenant_b.id):
        branch_a.name = "moved"
        with pytest.raises(TenantContextError):
            branch_a.save()
        with pytest.raises(TenantContextError):
            branch_a.delete()


def test_bulk_create_stamps_tenant(tenant_a):
    with tenant_context(tenant_a.id):
        Branch.objects.bulk_create([Branch(code=10, name="x"), Branch(code=11, name="y")])
        assert set(Branch.objects.filter(code__in=[10, 11]).values_list("tenant_id", flat=True)) \
            == {tenant_a.id}


def test_cannot_switch_tenant_inside_a_tenant(tenant_a, tenant_b):
    with tenant_context(tenant_a.id), pytest.raises(TenantContextError):
        with tenant_context(tenant_b.id):
            pass


def test_same_username_in_two_tenants(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        assert Membership.objects.get(username="owner").tenant_id == tenant_a.id
    with tenant_context(tenant_b.id):
        assert Membership.objects.get(username="owner").tenant_id == tenant_b.id


def test_subquery_is_scoped(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        heads = Branch.objects.filter(is_head_office=True).values("id")
        assert Membership.objects.filter(default_branch__in=heads).count() == 1
