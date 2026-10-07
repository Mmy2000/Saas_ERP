from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0003_pricefeed"),
        ("audit", "0005_owner_only"),
    ]

    operations = [
        EnableTenantRLS("PriceFeed"),
        GuardTenantFKs("PriceFeed"),
        # who changed the margins and when (not the bookkeeping fields)
        AuditModel("PriceFeed", ("last_checked_at", "last_outcome", "held", "held_at")),
    ]
