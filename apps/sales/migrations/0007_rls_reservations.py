from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0006_reservations"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("Reservation"),
        EnableTenantRLS("ReservationLine"),
        EnableTenantRLS("ReservationDeposit"),
        migrations.RunPython(grant_to_system_roles("sales.reservation."),
                             migrations.RunPython.noop),
    ]
