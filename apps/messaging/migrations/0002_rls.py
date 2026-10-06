from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0001_initial"),
        ("audit", "0005_owner_only"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("OutboundMessage"),
        EnableTenantRLS("DigestRecipient"),
        GuardTenantFKs("OutboundMessage"),
        GuardTenantFKs("DigestRecipient"),
        migrations.RunPython(grant_to_system_roles("messaging."), migrations.RunPython.noop),
    ]
