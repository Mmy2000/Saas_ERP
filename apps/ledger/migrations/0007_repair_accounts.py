"""Add the "Repair charges" (4104) and "Repair costs" (5103) accounts to existing tenants."""

from django.db import migrations

ACCOUNTS = [
    ("4104", "4", "income", "credit", "repair_income"),
    ("5103", "5", "expense", "debit", "repair_costs"),
]


def add(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    Account = apps.get_model("ledger", "Account")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        accounts = Account.objects.using(db).filter(tenant_id=tenant_id)
        for code, parent_code, type_, nature, role in ACCOUNTS:
            parent = accounts.filter(code=parent_code).first()
            if parent is None or accounts.filter(code=code).exists():
                continue
            Account.objects.using(db).create(
                tenant_id=tenant_id, code=code, template_key=code, parent=parent, type=type_,
                nature=nature, is_postable=True, subledger="none", commodity_scope="money",
                role=role,
            )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0006_period_status_label")]

    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
