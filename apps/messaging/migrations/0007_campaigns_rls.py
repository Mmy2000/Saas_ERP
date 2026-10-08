from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("messaging", "0006_campaigns"), ("audit", "0005_owner_only"),
                    ("iam", "0008_sign_ins_and_shortcuts_rls")]

    operations = [
        EnableTenantRLS("Campaign"),
        EnableTenantRLS("CampaignRecipient"),
        EnableTenantRLS("OptOut"),
        GuardTenantFKs("Campaign"),
        GuardTenantFKs("CampaignRecipient"),
        GuardTenantFKs("OptOut"),
        migrations.RunPython(grant_to_system_roles("messaging.campaign."),
                             migrations.RunPython.noop),
    ]
