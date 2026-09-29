from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0010_trade"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("TradeSale"),
        EnableTenantRLS("TradeSaleLine"),
        EnableTenantRLS("TradeReturn"),
        EnableTenantRLS("TradeReturnLine"),
        migrations.RunPython(grant_to_system_roles("sales.trade."), migrations.RunPython.noop),
    ]
