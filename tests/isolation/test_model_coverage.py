"""Meta-tests over every installed model (§23.3 "model coverage")."""

import pytest
from django.apps import apps
from django.db import connection, models

from apps.core.models import TenantScopedModel
from apps.core.tenancy.rls import POLICY_NAME

# Models that are deliberately not tenant-scoped. Adding to this list needs a reason.
NOT_TENANT_SCOPED = {
    "tenants.Tenant",  # platform registry
    "tenants.TenantDomain",  # platform registry, read by host resolution
    "iam.User",  # global identity (ADR-004)
    "iam.User_groups",  # Django admin permissions for platform staff
    "iam.User_user_permissions",
}
DJANGO_APPS = {"admin", "auth", "contenttypes", "sessions"}


def _project_models():
    for model in apps.get_models(include_auto_created=True):
        if model._meta.app_label in DJANGO_APPS or model._meta.label in NOT_TENANT_SCOPED:
            continue
        yield model


def test_every_project_model_is_tenant_scoped():
    offenders = [m._meta.label for m in _project_models() if not issubclass(m, TenantScopedModel)]
    assert offenders == [], (
        "These models are neither TenantScopedModel nor allow-listed as platform/global. "
        "Auto-created M2M tables count too: use an explicit tenant-scoped through model."
    )


def test_every_tenant_index_leads_with_tenant():
    offenders = []
    for model in _project_models():
        leading = [c.fields[0] for c in model._meta.constraints
                   if isinstance(c, models.UniqueConstraint) and c.fields]
        leading += [i.fields[0] for i in model._meta.indexes if i.fields]
        if "tenant" not in leading:
            offenders.append(model._meta.label)
    assert offenders == [], "Declare an index or unique constraint starting with `tenant`."


@pytest.mark.django_db
def test_every_tenant_table_has_forced_rls_policy():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = current_schema()
            WHERE c.relrowsecurity AND c.relforcerowsecurity
              AND EXISTS (SELECT 1 FROM pg_policies p
                          WHERE p.tablename = c.relname AND p.policyname = %s)
            """,
            [POLICY_NAME],
        )
        protected = {row[0] for row in cursor.fetchall()}

    missing = [m._meta.db_table for m in _project_models() if m._meta.db_table not in protected]
    assert missing == [], "Add EnableTenantRLS(<model>) to a migration for these tables."


def test_no_float_fields():
    offenders = [
        f"{m._meta.label}.{f.name}"
        for m in apps.get_models()
        if m._meta.app_label not in DJANGO_APPS
        for f in m._meta.get_fields()
        if isinstance(f, models.FloatField)
    ]
    assert offenders == [], "Money, weights and rates are DecimalField (§6.4)."


@pytest.mark.django_db
def test_test_connection_is_not_a_superuser():
    # Superusers bypass RLS even with FORCE; the isolation suite would prove nothing.
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone() == (False, False)
