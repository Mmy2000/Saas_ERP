"""Database triggers for the audit trail and for same-tenant foreign keys (§5.3, §21).

Audit trail: an AFTER trigger on each audited table writes one `audit_auditevent` row per
insert, change or delete: the columns that changed (old and new values), who (the
`app.user_id` setting, set per request) and when. Values of secret-looking columns are masked.
The audit table is append-only.

Same-tenant foreign keys: PostgreSQL checks a foreign key without row-level security, so a row
of one tenant could point at another tenant's row if application code ever let such an id
through. A BEFORE trigger on every tenant table looks each referenced row up (with RLS on) and
refuses the write unless it belongs to the same tenant.

Use the migration operations `AuditModel` and `GuardTenantFKs` for new models; the isolation
suite checks that every tenant table with tenant foreign keys has an up-to-date guard.
"""

from __future__ import annotations

from django.db.migrations.operations.base import Operation

AUDIT_TRIGGER = "audit_trail"
GUARD_TRIGGER = "tenant_fk_guard"

FUNCTIONS_SQL = r"""
CREATE OR REPLACE FUNCTION audit_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  old_row jsonb;
  new_row jsonb;
  diff jsonb;
  skip text[] := ARRAY['id', 'tenant_id', 'created_at', 'updated_at', 'created_by_id',
                       'updated_by_id'] || TG_ARGV;
BEGIN
  IF TG_OP <> 'INSERT' THEN old_row := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN new_row := to_jsonb(NEW); END IF;
  IF TG_OP = 'UPDATE' THEN
    SELECT jsonb_object_agg(n.key, jsonb_build_array(old_row -> n.key, n.value)) INTO diff
      FROM jsonb_each(new_row) AS n
     WHERE NOT (n.key = ANY (skip)) AND (old_row -> n.key) IS DISTINCT FROM n.value;
    IF diff IS NULL THEN
      RETURN NULL;  -- only ignored columns changed
    END IF;
  ELSE
    SELECT jsonb_object_agg(e.key, e.value) INTO diff
      FROM jsonb_each(coalesce(new_row, old_row)) AS e
     WHERE NOT (e.key = ANY (skip)) AND e.value <> 'null'::jsonb;
  END IF;
  -- Never keep secrets, not even encrypted ones.
  SELECT jsonb_object_agg(e.key, CASE
           WHEN e.key ~ '(password|secret|token|_encrypted|_hash)' THEN '"***"'::jsonb
           ELSE e.value END)
    INTO diff FROM jsonb_each(coalesce(diff, '{}'::jsonb)) AS e;
  INSERT INTO audit_auditevent
         (tenant_id, table_name, row_id, action, changes, user_id, at, created_at, updated_at)
  VALUES ((coalesce(new_row, old_row) ->> 'tenant_id')::bigint, TG_TABLE_NAME,
          (coalesce(new_row, old_row) ->> 'id')::bigint, left(TG_OP, 1),
          coalesce(diff, '{}'::jsonb), NULLIF(current_setting('app.user_id', true), '')::bigint,
          now(), now(), now());
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit events are append-only' USING ERRCODE = 'insufficient_privilege';
END $$;

"""


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def audit_sql(table: str, *, ignore: tuple[str, ...] = (), updates_only: bool = False) -> list[str]:
    events = "UPDATE" if updates_only else "INSERT OR UPDATE OR DELETE"
    args = ", ".join(_literal(column) for column in ignore)
    return [f'DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} ON "{table}"',
            f'CREATE TRIGGER {AUDIT_TRIGGER} AFTER {events} ON "{table}" '
            f"FOR EACH ROW EXECUTE FUNCTION audit_record({args})"]


def tenant_fk_pairs(model) -> list[tuple[str, str]]:
    """(column, referenced table) for each foreign key of `model` to another tenant table."""
    pairs = []
    for field in model._meta.get_fields():
        if not getattr(field, "many_to_one", False) and not getattr(field, "one_to_one", False):
            continue
        if not getattr(field, "concrete", False) or field.name == "tenant":
            continue
        target = field.related_model
        if any(f.name == "tenant" for f in target._meta.fields):
            pairs.append((field.column, target._meta.db_table))
    return sorted(pairs)


def _guard_function(table: str) -> str:
    return f"tfg_{table}"[:63]


def guard_sql(table: str, pairs: list[tuple[str, str]]) -> list[str]:
    """A trigger function per table with one static check per foreign key, so PostgreSQL
    plans each lookup once (a generic function with dynamic SQL made every write noticeably
    slower). The pairs are also the trigger's arguments, which the isolation suite reads."""
    function = _guard_function(table)
    sql = [f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{table}"']
    if not pairs:
        sql.append(f'DROP FUNCTION IF EXISTS "{function}"()')
        return sql
    checks = "".join(f"""
  IF NEW."{column}" IS NOT NULL AND (TG_OP = 'INSERT'
      OR NEW."{column}" IS DISTINCT FROM OLD."{column}"
      OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id) THEN
    -- Row-level security hides other tenants' rows: "not found" and "another tenant" are the
    -- same refusal.
    PERFORM 1 FROM "{target}" WHERE id = NEW."{column}" AND tenant_id = NEW.tenant_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'cross-tenant reference: {table}.{column} = % does not belong to tenant %',
        NEW."{column}", NEW.tenant_id USING ERRCODE = 'foreign_key_violation';
    END IF;
  END IF;""" for column, target in pairs)
    sql.append(f'CREATE OR REPLACE FUNCTION "{function}"() RETURNS trigger LANGUAGE plpgsql AS $$\n'
               f"BEGIN{checks}\n  RETURN NEW;\nEND $$")
    args = ", ".join(f"{_literal(column)}, {_literal(target)}" for column, target in pairs)
    sql.append(f'CREATE TRIGGER {GUARD_TRIGGER} BEFORE INSERT OR UPDATE ON "{table}" '
               f'FOR EACH ROW EXECUTE FUNCTION "{function}"({args})')
    return sql


class GuardTenantFKs(Operation):
    """Install (or refresh) the same-tenant foreign key guard of a model, from its current
    fields. Add it to a migration whenever a tenant model gains a foreign key."""

    reversible = True
    reduces_to_sql = True

    def __init__(self, model_name: str):
        self.model_name = model_name

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        for sql in guard_sql(model._meta.db_table, tenant_fk_pairs(model)):
            schema_editor.execute(sql, params=None)  # no params: "%" stays literal

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} '
                              f'ON "{model._meta.db_table}"')

    def describe(self):
        return f"Guard the tenant foreign keys of {self.model_name}"


class AuditModel(Operation):
    """Record every change to a model in the audit trail."""

    reversible = True
    reduces_to_sql = True

    def __init__(self, model_name: str, ignore: tuple[str, ...] = (), updates_only=False):
        self.model_name, self.ignore, self.updates_only = model_name, tuple(ignore), updates_only

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        for sql in audit_sql(model._meta.db_table, ignore=self.ignore,
                             updates_only=self.updates_only):
            schema_editor.execute(sql)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} '
                              f'ON "{model._meta.db_table}"')

    def describe(self):
        return f"Audit changes to {self.model_name}"
