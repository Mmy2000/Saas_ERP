from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0015_stone_parcels"), ("audit", "0005_owner_only")]

    operations = [
        EnableTenantRLS("StoneParcel"),
        EnableTenantRLS("ParcelMovement"),
        EnableTenantRLS("ParcelDeal"),
        GuardTenantFKs("StoneParcel"),
        GuardTenantFKs("ParcelMovement"),
        GuardTenantFKs("ParcelDeal"),
        AuditModel("StoneParcel"),
    ]
