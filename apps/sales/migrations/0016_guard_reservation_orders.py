from django.db import migrations

from apps.audit.triggers import GuardTenantFKs


class Migration(migrations.Migration):
    """Reservation lines may now order a category and karat: the guard covers them."""

    dependencies = [("sales", "0015_reservation_orders")]

    operations = [GuardTenantFKs("ReservationLine")]
