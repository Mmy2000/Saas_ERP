from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0019_parcel_moves")]

    operations = [
        EnableTenantRLS("ParcelTransfer"),
        EnableTenantRLS("ParcelCount"),
        EnableTenantRLS("ParcelCountLine"),
        GuardTenantFKs("ParcelTransfer"),
        GuardTenantFKs("ParcelCount"),
        GuardTenantFKs("ParcelCountLine"),
    ]
