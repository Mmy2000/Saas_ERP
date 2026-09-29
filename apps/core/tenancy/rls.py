"""Row-Level Security for tenant tables (§5.3, "Database" layer).

Every tenant-owned table gets ENABLE + FORCE ROW LEVEL SECURITY and one policy comparing
`tenant_id` with the `app.tenant_id` setting. FORCE matters: without it the table owner
(app_owner, which runs migrations and tests) would bypass the policy.

Add `EnableTenantRLS("<model>")` to a migration after the model's CreateModel. The isolation
suite (tests/isolation/test_model_coverage.py) fails for any tenant model that lacks it.
"""

from __future__ import annotations

from django.db.migrations.operations.base import Operation

POLICY_NAME = "tenant_isolation"

_TENANT_MATCH = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint"


def enable_sql(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {POLICY_NAME} ON {table} USING ({_TENANT_MATCH}) "
        f"WITH CHECK ({_TENANT_MATCH})",
    ]


def disable_sql(table: str) -> list[str]:
    return [
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]


class EnableTenantRLS(Operation):
    reversible = True
    reduces_to_sql = True

    def __init__(self, model_name: str):
        self.model_name = model_name

    def state_forwards(self, app_label, state):
        pass

    def _table(self, app_label, schema_editor, state) -> str:
        model = state.apps.get_model(app_label, self.model_name)
        return schema_editor.quote_name(model._meta.db_table)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        for sql in enable_sql(self._table(app_label, schema_editor, to_state)):
            schema_editor.execute(sql)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        for sql in disable_sql(self._table(app_label, schema_editor, from_state)):
            schema_editor.execute(sql)

    def describe(self):
        return f"Enable tenant row-level security on {self.model_name}"

    @property
    def migration_name_fragment(self):
        return f"rls_{self.model_name.lower()}"

    def deconstruct(self):
        return (f"{__name__}.{self.__class__.__qualname__}", [self.model_name], {})
