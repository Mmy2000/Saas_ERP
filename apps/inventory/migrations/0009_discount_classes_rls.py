from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    """Discount classes are tenant data, audited like other price settings; pieces may now
    name one (the guard covers it)."""

    dependencies = [("inventory", "0008_discount_classes"), ("audit", "0005_owner_only")]

    operations = [
        EnableTenantRLS("DiscountClass"),
        GuardTenantFKs("Item"),
        AuditModel("DiscountClass"),
    ]
