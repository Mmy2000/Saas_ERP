from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0017_workshop_stones"), ("audit", "0005_owner_only"),
                    ("iam", "0008_sign_ins_and_shortcuts_rls")]

    operations = [
        EnableTenantRLS("WorkshopStones"),
        EnableTenantRLS("WorkshopStoneMovement"),
        EnableTenantRLS("StoneHandout"),
        GuardTenantFKs("WorkshopStones"),
        GuardTenantFKs("WorkshopStoneMovement"),
        GuardTenantFKs("StoneHandout"),
        migrations.RunPython(grant_to_system_roles("diamonds.handout"),
                             migrations.RunPython.noop),
    ]
