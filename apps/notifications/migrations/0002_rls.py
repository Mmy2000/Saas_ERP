from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial"), ("audit", "0005_owner_only")]

    operations = [
        EnableTenantRLS("Notification"),
        EnableTenantRLS("NotificationMute"),
        GuardTenantFKs("Notification"),
        GuardTenantFKs("NotificationMute"),
    ]
