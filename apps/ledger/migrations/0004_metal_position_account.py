"""Add the "Metal trading position" account (role metal_position) to existing tenants."""

from django.db import migrations


def add(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    Account = apps.get_model("ledger", "Account")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        parent = Account.objects.using(db).filter(tenant_id=tenant_id, code="3").first()
        if parent is None or Account.objects.using(db).filter(tenant_id=tenant_id,
                                                              code="3401").exists():
            continue
        Account.objects.using(db).create(
            tenant_id=tenant_id, code="3401", template_key="3401", parent=parent, type="equity",
            nature="credit", is_postable=True, subledger="none", commodity_scope="any",
            role="metal_position",
        )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0003_setup_existing_tenants")]

    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
