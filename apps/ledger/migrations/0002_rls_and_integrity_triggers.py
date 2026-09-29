"""Row-level security for every ledger table, plus two database-level guarantees:

* a deferred constraint trigger re-checks at COMMIT that each entry balances in functional
  currency and per metal (PostingService checks first; this catches any other writer);
* journal entries and lines are append-only: UPDATE and DELETE are rejected.
"""

from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS

BALANCE_TRIGGER = """
CREATE OR REPLACE FUNCTION ledger_entry_must_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    diff numeric;
BEGIN
    SELECT COALESCE(SUM(functional_amount), 0) INTO diff
      FROM ledger_journalline WHERE entry_id = NEW.entry_id;
    IF diff <> 0 THEN
        RAISE EXCEPTION 'ledger: journal entry % is out of balance by %', NEW.entry_id, diff
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM ledger_journalline l JOIN ledger_commodity c ON c.id = l.commodity_id
         WHERE l.entry_id = NEW.entry_id AND c.kind = 'metal'
         GROUP BY l.commodity_id HAVING SUM(l.quantity) <> 0
    ) THEN
        RAISE EXCEPTION 'ledger: metal does not balance in journal entry %', NEW.entry_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER ledger_journalline_balanced
    AFTER INSERT ON ledger_journalline
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_must_balance();

CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger: % is append-only; post a reversal instead', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER ledger_journalentry_append_only
    BEFORE UPDATE OR DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER ledger_journalline_append_only
    BEFORE UPDATE OR DELETE ON ledger_journalline
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
"""

DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS ledger_journalline_append_only ON ledger_journalline;
DROP TRIGGER IF EXISTS ledger_journalentry_append_only ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_append_only();
DROP TRIGGER IF EXISTS ledger_journalline_balanced ON ledger_journalline;
DROP FUNCTION IF EXISTS ledger_entry_must_balance();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [
        EnableTenantRLS("Commodity"),
        EnableTenantRLS("Account"),
        EnableTenantRLS("JournalEntry"),
        EnableTenantRLS("JournalLine"),
        EnableTenantRLS("BalanceProjection"),
        EnableTenantRLS("FiscalPeriod"),
        migrations.RunSQL(BALANCE_TRIGGER, DROP_TRIGGERS),
    ]
