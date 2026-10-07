from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0007_setting_jobs")]

    operations = [
        EnableTenantRLS("SettingJob"),
        EnableTenantRLS("SettingJobLine"),
        EnableTenantRLS("StoneRemoval"),
        GuardTenantFKs("SettingJob"),
        GuardTenantFKs("SettingJobLine"),
        GuardTenantFKs("StoneRemoval"),
        GuardTenantFKs("StoneSetting"),
        GuardTenantFKs("StoneSettingStone"),
        GuardTenantFKs("ItemStone"),
    ]
