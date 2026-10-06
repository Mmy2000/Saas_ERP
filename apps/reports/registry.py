"""The report registry (§7.12): every report is a class with filters, a permission and a `run`
that returns tables. One screen, CSV export and print serve them all.

    @register
    class DailySummary(Report):
        code = "daily_summary"
        filters = (Filter("date", "date"), Filter("branch", "branch"))
        def run(self, params, scope): ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

REPORTS: dict[str, type[Report]] = {}


def register(report_class):
    REPORTS[report_class.code] = report_class
    return report_class


# kind → decimal places (None: not a number)
KINDS = {"text": None, "int": 0, "money": 2, "weight": 3, "fine": 4, "pct": 1, "date": None}


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str = "text"

    @property
    def places(self):
        return KINDS[self.kind]

    @property
    def numeric(self) -> bool:
        return self.places is not None


# A row may carry "_style" (heading, subtotal or total: statements with sections) and "_indent".
ROW_STYLES = ("heading", "subtotal", "total")


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[dict] = field(default_factory=list)
    totals: dict | None = None  # same keys as the rows; shown as the last line
    note: str = ""

    def add_totals(self, label_key: str, label: str) -> Table:
        totals = {label_key: label}
        for column in self.columns:
            if column.numeric and column.kind != "pct":
                totals[column.key] = sum((row.get(column.key) or 0 for row in self.rows),
                                         Decimal(0))
        self.totals = totals
        return self


@dataclass(frozen=True)
class Filter:
    key: str
    kind: str  # date | branch | choice
    label: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    default: str = ""


@dataclass
class Params:
    """Parsed filter values. Dates default to today (and date_from to the month start)."""

    values: dict

    def date(self, key: str) -> date:
        raw = self.values.get(key)
        return raw if isinstance(raw, date) else timezone.localdate()

    def get(self, key: str, default=None):
        return self.values.get(key, default)


class Report:
    code = ""
    title = ""
    description = ""
    filters: tuple[Filter, ...] = ()

    @classmethod
    def permission(cls) -> str:
        return f"reports.{cls.code}.view"

    def parse(self, query, branches) -> Params:
        """Filter values from the query string, limited to what the viewer may see."""
        today = timezone.localdate()
        values = {}
        for spec in self.filters:
            raw = (query.get(spec.key) or "").strip()
            if spec.kind == "date":
                fallback = (today.replace(day=1) if spec.key == "date_from" else today)
                try:
                    values[spec.key] = date.fromisoformat(raw) if raw else fallback
                except ValueError:
                    values[spec.key] = fallback
            elif spec.kind == "branch":
                allowed = {str(b.pk): b for b in branches}
                values[spec.key] = allowed.get(raw)  # None: every branch the viewer may see
            else:
                keys = {key for key, _label in spec.choices}
                values[spec.key] = raw if raw in keys else spec.default
        start, end = values.get("date_from"), values.get("date_to")
        if start and end and start > end:
            values["date_from"], values["date_to"] = end, start
        if values.get("date_to") and values.get("date_from") \
                and values["date_to"] - values["date_from"] > timedelta(days=366):
            values["date_from"] = values["date_to"] - timedelta(days=366)
        return Params(values)

    def run(self, params: Params, scope) -> list[Table]:
        """`scope`: branch ids to include (None = all). Returns the tables to show."""
        raise NotImplementedError


DATE_FILTERS = (Filter("date_from", "date", _("From")), Filter("date_to", "date", _("To")))
BRANCH_FILTER = Filter("branch", "branch", _("Branch"))
