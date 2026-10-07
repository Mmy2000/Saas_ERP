from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0013_buy_backs"), ("iam", "0006_seed_roles_for_existing_tenants")]

    operations = [
        EnableTenantRLS("BuyBack"),
        EnableTenantRLS("BuyBackLine"),
        GuardTenantFKs("BuyBack"),
        GuardTenantFKs("BuyBackLine"),
        migrations.RunPython(grant_to_system_roles("diamonds.buy_back"),
                             migrations.RunPython.noop),
    ]
