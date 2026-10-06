"""Add "Production in progress" (1305) and "Production labour absorbed" (5205) to existing
tenants, for in-house production orders."""

from django.db import migrations

ACCOUNTS = [
    # code, parent, type, nature, scope, role
    ("1305", "13", "asset", "debit", "any", "inventory_in_production"),
    ("5205", "5", "expense", "credit", "money", "labour_absorbed"),
]


def add(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    Account = apps.get_model("ledger", "Account")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        accounts = Account.objects.using(db).filter(tenant_id=tenant_id)
        for code, parent_code, kind, nature, scope, role in ACCOUNTS:
            parent = accounts.filter(code=parent_code).first()
            if parent is None or accounts.filter(code=code).exists():
                continue
            Account.objects.using(db).create(
                tenant_id=tenant_id, code=code, template_key=code, parent=parent, type=kind,
                nature=nature, is_postable=True, subledger="none", commodity_scope=scope,
                role=role,
            )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0010_period_closing_rls")]

    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
