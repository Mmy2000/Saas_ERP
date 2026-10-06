"""Add "Cash over and short" (5207) to existing tenants, for cash counts."""

from django.db import migrations


def add(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    Account = apps.get_model("ledger", "Account")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        accounts = Account.objects.using(db).filter(tenant_id=tenant_id)
        parent = accounts.filter(code="5").first()
        if parent is None or accounts.filter(code="5207").exists():
            continue
        Account.objects.using(db).create(
            tenant_id=tenant_id, code="5207", template_key="5207", parent=parent,
            type="expense", nature="debit", is_postable=True, subledger="none",
            commodity_scope="money", role="cash_over_short",
        )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0012_cheque_and_bank_accounts")]

    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
