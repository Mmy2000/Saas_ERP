"""Names written by the first version of seeding (in the tenant's language) become blank, so
they now display in each viewer's language. Names a tenant typed itself are kept."""

from django.db import migrations

SEEDED_KARAT_NAMES = {f"عيار {c}" for c in (14, 18, 21, 22, 24)} | {
    f"{c}K" for c in (14, 18, 21, 22, 24)} | {"فضة 925", "Silver 925"}
SEEDED_CURRENCY_NAMES = {"جنيه مصري", "دولار أمريكي", "درهم إماراتي", "ريال سعودي", "يورو",
                         "Egyptian pound", "US dollar", "UAE dirham", "Saudi riyal", "Euro"}


def clear(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    Karat = apps.get_model("catalog", "Karat")
    Currency = apps.get_model("catalog", "Currency")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        Karat.objects.using(db).filter(tenant_id=tenant_id,
                                       display_name__in=SEEDED_KARAT_NAMES).update(display_name="")
        Currency.objects.using(db).filter(tenant_id=tenant_id,
                                          name__in=SEEDED_CURRENCY_NAMES).update(name="")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("catalog", "0004_label_defaults"), ("tenants", "0001_initial")]

    operations = [migrations.RunPython(clear, migrations.RunPython.noop)]
