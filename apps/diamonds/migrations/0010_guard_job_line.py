from django.db import migrations

from apps.audit.triggers import GuardTenantFKs


class Migration(migrations.Migration):
    """Job lines now name a category: the guard covers it."""

    dependencies = [("diamonds", "0009_job_line_category")]

    operations = [GuardTenantFKs("SettingJobLine")]
