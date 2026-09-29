"""Give every existing balance on the old single cash / bank / card accounts its own holder.

Before treasury, money was posted to one postable account per role (1101 cash, 1102 bank,
1103 card receivable). Those become groups: each branch gets a default cash box per currency,
each currency found on 1102 a bank account, each currency on 1103 a card terminal, and the
historical lines are re-pointed to the new accounts, so balances and later reversals of old
documents land on the holder. Lines are append-only at runtime; this one-off rewrite is done
before any production data exists and changes no amounts. Runs per tenant with app.tenant_id
set (RLS applies to the migrating role too).
"""

from django.db import migrations

GROUP_TEMPLATE = {"cash": "1101", "bank": "1102", "card_receivable": "1103"}


def forwards(apps, schema_editor):
    connection = schema_editor.connection

    def sql(statement, params=()):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)

    Tenant = apps.get_model("tenants", "Tenant")
    sql("ALTER TABLE ledger_journalline DISABLE TRIGGER ledger_journalline_append_only")
    for tenant_id in Tenant.objects.using(connection.alias).values_list("id", flat=True):
        sql("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        _migrate_tenant(apps, connection.alias, sql, tenant_id)
    # The UPDATEs queued deferred FK checks; run them now, or the ALTER is refused.
    sql("SET CONSTRAINTS ALL IMMEDIATE")
    sql("ALTER TABLE ledger_journalline ENABLE TRIGGER ledger_journalline_append_only")
    sql("SELECT set_config('app.tenant_id', '', true)")


def _migrate_tenant(apps, db, sql, tenant_id):  # noqa: C901
    TenantProfile = apps.get_model("org", "TenantProfile")
    Branch = apps.get_model("org", "Branch")
    Currency = apps.get_model("catalog", "Currency")
    Commodity = apps.get_model("ledger", "Commodity")
    Account = apps.get_model("ledger", "Account")
    JournalLine = apps.get_model("ledger", "JournalLine")
    CashBox = apps.get_model("treasury", "CashBox")
    BankAccount = apps.get_model("treasury", "BankAccount")
    CardTerminal = apps.get_model("treasury", "CardTerminal")

    groups = {role: Account.objects.using(db).filter(tenant_id=tenant_id, role=role).first()
              for role in GROUP_TEMPLATE}
    if not all(groups.values()):
        return
    functional = (TenantProfile.objects.using(db).filter(tenant_id=tenant_id)
                  .values_list("functional_currency", flat=True).first())
    money = {c.currency_id: c for c in Commodity.objects.using(db).filter(
        tenant_id=tenant_id, kind="money")}
    currencies = {c.pk: c for c in Currency.objects.using(db).filter(tenant_id=tenant_id)}

    def new_account(role, commodity, name=""):
        group = groups[role]
        codes = Account.objects.using(db).filter(tenant_id=tenant_id, parent=group).values_list(
            "code", flat=True)
        seq = max([int(c[len(group.code):]) for c in codes if c[len(group.code):].isdigit()],
                  default=0) + 1
        return Account.objects.using(db).create(
            tenant_id=tenant_id, code=f"{group.code}{seq:03d}", name=name[:200],
            template_key="" if name else GROUP_TEMPLATE[role], parent=group, type="asset",
            nature="debit", subledger="none", commodity_scope="money", commodity=commodity,
            is_postable=True)

    def repoint(group, account, commodity_id, branch_id=None):
        where = "tenant_id = %s AND account_id = %s AND commodity_id = %s"
        params = [tenant_id, group.pk, commodity_id]
        if branch_id is not None:
            where += " AND branch_id = %s"
            params.append(branch_id)
        for table in ("ledger_journalline", "ledger_balanceprojection"):
            sql(f"UPDATE {table} SET account_id = %s WHERE {where}",  # noqa: S608
                [account.pk, *params])

    def used(group):
        return (JournalLine.objects.using(db).filter(tenant_id=tenant_id, account=group)
                .values_list("branch_id", "commodity_id").distinct())

    boxes = {}

    def box_for(branch, currency):
        key = (branch.pk, currency.pk)
        if key not in boxes:
            boxes[key] = CashBox.objects.using(db).filter(
                tenant_id=tenant_id, branch=branch, currency=currency, is_default=True).first()
        if boxes[key] is None:
            commodity = money.get(currency.pk)
            if commodity is None:
                return None
            boxes[key] = CashBox.objects.using(db).create(
                tenant_id=tenant_id, branch=branch, currency=currency, is_default=True,
                account=new_account("cash", commodity, f"{branch.name} · {currency.code}"))
        return boxes[key]

    branches = {b.pk: b for b in Branch.objects.using(db).filter(tenant_id=tenant_id)}
    home = next((c for c in currencies.values() if c.code == functional), None)
    for branch in branches.values():
        if home is not None and branch.is_active:
            box_for(branch, home)
    commodities = {c.pk: c for c in money.values()}
    for branch_id, commodity_id in used(groups["cash"]):
        commodity = commodities[commodity_id]
        box = box_for(branches[branch_id], currencies[commodity.currency_id])
        repoint(groups["cash"], box.account, commodity_id, branch_id)

    banks = {}

    def bank_for(commodity):
        if commodity.pk not in banks:
            banks[commodity.pk] = BankAccount.objects.using(db).create(
                tenant_id=tenant_id, currency_id=commodity.currency_id, all_branches=True,
                account=new_account("bank", commodity))
        return banks[commodity.pk]

    for commodity_id in {c for _b, c in used(groups["bank"])}:
        commodity = commodities[commodity_id]
        repoint(groups["bank"], bank_for(commodity).account, commodity_id)
    for commodity_id in {c for _b, c in used(groups["card_receivable"])}:
        commodity = commodities[commodity_id]
        terminal = CardTerminal.objects.using(db).create(
            tenant_id=tenant_id, bank_account=bank_for(commodity), fee_rate=0,
            account=new_account("card_receivable", commodity))
        repoint(groups["card_receivable"], terminal.account, commodity_id)

    for group in groups.values():
        group.is_postable = False
        group.save(update_fields=["is_postable"])


class Migration(migrations.Migration):
    dependencies = [
        ("treasury", "0002_rls"),
        ("ledger", "0005_account_commodity"),
        ("org", "0003_tenantprofile_country_alter_tenantprofile_locale"),
        ("tenants", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
