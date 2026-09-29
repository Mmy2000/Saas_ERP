from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS

# ledger_append_only() is created by ledger.0002; stock history is protected the same way.
APPEND_ONLY = """
CREATE TRIGGER inventory_stockmovement_append_only
    BEFORE UPDATE OR DELETE ON inventory_stockmovement
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
"""
DROP = "DROP TRIGGER IF EXISTS inventory_stockmovement_append_only ON inventory_stockmovement;"


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("ledger", "0002_rls_and_integrity_triggers"),
    ]

    operations = [
        EnableTenantRLS("Item"),
        EnableTenantRLS("StockLot"),
        EnableTenantRLS("LotBalance"),
        EnableTenantRLS("StockMovement"),
        migrations.RunSQL(APPEND_ONLY, DROP),
    ]
