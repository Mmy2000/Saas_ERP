"""Sale lines now carry their category (bulk lines have no piece to take it from); fill it in for
the lines of pieces sold before. Runs per tenant with app.tenant_id set (RLS)."""

from django.db import migrations

BACKFILL = """
UPDATE sales_salesinvoiceline AS line SET category_id = item.category_id
  FROM inventory_item AS item
 WHERE line.item_id = item.id AND line.category_id IS NULL
"""


def backfill(apps, schema_editor):
    connection = schema_editor.connection
    Tenant = apps.get_model("tenants", "Tenant")
    with connection.cursor() as cursor:
        for tenant_id in Tenant.objects.using(connection.alias).values_list("id", flat=True):
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cursor.execute(BACKFILL)
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0008_bulk_lines"),
        ("inventory", "0004_rls_transfers_and_stocktakes"),
        ("tenants", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
