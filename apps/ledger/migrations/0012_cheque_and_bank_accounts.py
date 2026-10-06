"""Add the cheque accounts (1104, 2104), bank interest (4302) and bank charges (5206) to
existing tenants."""

from django.db import migrations

ACCOUNTS = [
    # code, parent, type, nature, role
    ("1104", "11", "asset", "debit", "cheques_received"),
    ("2104", "21", "liability", "credit", "cheques_payable"),
    ("4302", "4", "income", "credit", "bank_interest"),
    ("5206", "5", "expense", "debit", "bank_charges"),
]


def add(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    Account = apps.get_model("ledger", "Account")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        accounts = Account.objects.using(db).filter(tenant_id=tenant_id)
        for code, parent_code, kind, nature, role in ACCOUNTS:
            parent = accounts.filter(code=parent_code).first()
            if parent is None or accounts.filter(code=code).exists():
                continue
            Account.objects.using(db).create(
                tenant_id=tenant_id, code=code, template_key=code, parent=parent, type=kind,
                nature=nature, is_postable=True, subledger="none", commodity_scope="money",
                role=role,
            )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0011_production_accounts")]

    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
