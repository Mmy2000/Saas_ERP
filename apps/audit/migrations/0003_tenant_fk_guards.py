"""The same-tenant foreign key guard (apps/audit/triggers.py) on every tenant table that points
at another tenant table. Later migrations use the GuardTenantFKs operation."""

from django.db import migrations

from apps.audit.triggers import GUARD_TRIGGER, _guard_function, guard_sql, tenant_fk_pairs


def _tenant_models(apps):
    return [model for model in apps.get_models()
            if any(field.name == "tenant" for field in model._meta.fields)]


def install(apps, schema_editor):
    for model in _tenant_models(apps):
        for sql in guard_sql(model._meta.db_table, tenant_fk_pairs(model)):
            schema_editor.execute(sql, params=None)  # no params: "%" stays literal


def remove(apps, schema_editor):
    for model in _tenant_models(apps):
        table = model._meta.db_table
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{table}"')
        schema_editor.execute(f'DROP FUNCTION IF EXISTS "{_guard_function(table)}"()')


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_triggers"),
        ("core", "0006_sequence_counter_length"),
        ("purchasing", "0008_seller_role"),
        ("sales", "0011_rls_trade"),
        ("settlements", "0005_alter_settlement_side"),
        ("manufacturing", "0003_in_house_production"),
        ("repairs", "0002_rls"),
        ("reports", "0002_grant_statements"),
    ]

    operations = [migrations.RunPython(install, remove)]
