from django.db import migrations

from apps.audit.triggers import GuardTenantFKs


class Migration(migrations.Migration):
    """Scrap purchase lines may now name a bullion category: the guard covers it."""

    dependencies = [("purchasing", "0010_scrap_line_bullion")]

    operations = [GuardTenantFKs("ScrapPurchaseLine")]
