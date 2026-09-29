from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0003_scrap"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("ScrapPurchase"),
        EnableTenantRLS("ScrapPurchaseLine"),
        EnableTenantRLS("ScrapSale"),
        EnableTenantRLS("ScrapSaleLine"),
        migrations.RunPython(grant_to_system_roles("purchasing.scrap."),
                             migrations.RunPython.noop),
    ]
