from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_transfers_and_stocktakes"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("StockTransfer"),
        EnableTenantRLS("StockTransferLine"),
        EnableTenantRLS("Stocktake"),
        EnableTenantRLS("StocktakeLine"),
        EnableTenantRLS("StocktakeLotLine"),
        migrations.RunPython(grant_to_system_roles("inventory.transfer.", "inventory.stocktake."),
                             migrations.RunPython.noop),
    ]
