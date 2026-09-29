"""Give tenants provisioned before the ledger existed their commodities and default chart of
accounts (new tenants get them from provisioning). Runs per tenant with app.tenant_id set."""

from django.db import migrations


def setup(apps, schema_editor):
    from apps.ledger.chart import CHART, METAL_COMMODITIES

    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    TenantProfile = apps.get_model("org", "TenantProfile")
    Currency = apps.get_model("catalog", "Currency")
    Metal = apps.get_model("catalog", "Metal")
    Commodity = apps.get_model("ledger", "Commodity")
    Account = apps.get_model("ledger", "Account")

    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        functional = (TenantProfile.objects.using(db).filter(tenant_id=tenant_id)
                      .values_list("functional_currency", flat=True).first())
        for currency in Currency.objects.using(db).filter(tenant_id=tenant_id, is_active=True):
            Commodity.objects.using(db).get_or_create(
                tenant_id=tenant_id, code=currency.code,
                defaults={"kind": "money", "currency": currency,
                          "decimal_places": currency.minor_units,
                          "is_functional": currency.code == functional},
            )
        for code, kind, metal_code, places in METAL_COMMODITIES:
            metal = Metal.objects.using(db).filter(tenant_id=tenant_id, code=metal_code).first()
            if metal is not None:
                Commodity.objects.using(db).get_or_create(
                    tenant_id=tenant_id, code=code,
                    defaults={"kind": kind, "metal": metal, "decimal_places": places})
        by_code = {a.code: a for a in Account.objects.using(db).filter(tenant_id=tenant_id)}
        for code, parent, _name, type_, nature, postable, subledger, scope, role in CHART:
            if code not in by_code:
                by_code[code] = Account.objects.using(db).create(
                    tenant_id=tenant_id, code=code, template_key=code, parent=by_code.get(parent),
                    type=type_, nature=nature, is_postable=postable, subledger=subledger,
                    commodity_scope=scope, role=role,
                )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0002_rls_and_integrity_triggers"),
        ("catalog", "0005_clear_seeded_names"),
        ("org", "0003_tenantprofile_country_alter_tenantprofile_locale"),
        ("tenants", "0001_initial"),
    ]

    operations = [migrations.RunPython(setup, migrations.RunPython.noop)]
