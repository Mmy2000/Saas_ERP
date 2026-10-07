from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("merchandising", "0001_initial"),
        ("audit", "0005_owner_only"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("PriceChange"),
        EnableTenantRLS("PriceChangeLine"),
        EnableTenantRLS("Offer"),
        GuardTenantFKs("PriceChange"),
        GuardTenantFKs("PriceChangeLine"),
        GuardTenantFKs("Offer"),
        AuditModel("Offer"),
        migrations.RunPython(grant_to_system_roles("inventory.price.", "sales.offer."),
                             migrations.RunPython.noop),
    ]
