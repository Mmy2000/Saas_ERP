"""Database-level isolation, independent of the ORM (§23.3 "raw SQL")."""

import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _scalar(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def _tenant_ids(table):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT DISTINCT tenant_id FROM {table}")
        return {row[0] for row in cursor.fetchall()}


@pytest.mark.parametrize("table", ["org_branch", "org_tenantprofile", "iam_membership"])
def test_no_rows_without_tenant_setting(tenant_a, tenant_b, table):
    assert _scalar(f"SELECT count(*) FROM {table}") == 0


@pytest.mark.parametrize("table", ["org_branch", "org_tenantprofile", "iam_membership"])
def test_only_current_tenant_rows_visible(tenant_a, tenant_b, table):
    with tenant_context(tenant_a.id):
        assert _tenant_ids(table) == {tenant_a.id}
    with tenant_context(tenant_b.id):
        assert _tenant_ids(table) == {tenant_b.id}


def test_setting_is_restored_after_context(tenant_a):
    with tenant_context(tenant_a.id):
        assert _scalar("SELECT current_setting('app.tenant_id', true)") == str(tenant_a.id)
    assert _scalar("SELECT current_setting('app.tenant_id', true)") in ("", None)


def test_with_check_blocks_moving_rows_to_another_tenant(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE org_branch SET tenant_id = %s", [tenant_b.id])


def test_updates_cannot_reach_other_tenant_rows(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        with connection.cursor() as cursor:
            cursor.execute("UPDATE org_branch SET name = 'hijacked' WHERE tenant_id = %s",
                           [tenant_b.id])
            assert cursor.rowcount == 0
    with tenant_context(tenant_b.id):
        assert _scalar("SELECT count(*) FROM org_branch WHERE name = 'hijacked'") == 0


def test_insert_for_another_tenant_is_rejected(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO org_branch (tenant_id, code, name, is_head_office, "
                    "address, phone, is_active, created_at, updated_at) "
                    "VALUES (%s, 50, 'x', false, '', '', true, now(), now())",
                    [tenant_b.id],
                )
