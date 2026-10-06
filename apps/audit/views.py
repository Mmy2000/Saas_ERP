"""The activity page: who changed what, and when, across the audited master data, or the
history of one record (?table=…&row=…)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.apps import apps
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import render
from django.utils.translation import gettext as _

from apps.core.templatetags.ui import num
from apps.iam.authz import permission_required
from apps.iam.models import Membership

from .labels import COLUMNS, TABLES
from .models import AuditAction, AuditEvent
from .registry import AUDITED

HIDDEN = "***"


def _models_by_table() -> dict[str, type[models.Model]]:
    return {apps.get_model(label)._meta.db_table: apps.get_model(label) for label in AUDITED}


def _name(obj) -> str:
    for attribute in ("label", "display_name", "name", "username", "barcode", "code",
                      "permission"):
        value = getattr(obj, attribute, None)
        if value:
            return str(value)
    return f"#{obj.pk}"


def _date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


class _Labels:
    """Resolves record ids to names in bulk, one query per model."""

    def __init__(self):
        self.wanted: dict[type, set[int]] = defaultdict(set)
        self.names: dict[tuple[type, int], str] = {}

    def want(self, model, pk):
        if pk is not None:
            self.wanted[model].add(int(pk))

    def load(self):
        for model, ids in self.wanted.items():
            for obj in model._default_manager.filter(pk__in=ids):
                self.names[(model, obj.pk)] = _name(obj)

    def get(self, model, pk):
        return self.names.get((model, int(pk)))


def _describe(events) -> list[dict]:
    by_table = _models_by_table()
    labels = _Labels()
    for event in events:
        model = by_table.get(event.table_name)
        if model is None:
            continue
        labels.want(model, event.row_id)
        fields = {f.column: f for f in model._meta.concrete_fields}
        for column, value in event.changes.items():
            field = fields.get(column)
            if field is not None and field.is_relation:
                for item in (value if event.action == AuditAction.UPDATE else [value]):
                    if item not in (None, HIDDEN) and str(item).isdigit():
                        labels.want(field.related_model, item)
    labels.load()

    def shown(field, value):
        if value is None or value == "":
            return "—"
        if value == HIDDEN:
            return _("(hidden)")
        if field is not None and field.is_relation and str(value).isdigit():
            return labels.get(field.related_model, value) or f"#{value}"
        if field is not None and field.choices:
            return str(dict(field.flatchoices).get(value, value))
        if isinstance(value, bool):
            return _("Yes") if value else _("No")
        if isinstance(field, models.DecimalField) and isinstance(value, (int, float, str)):
            return num(value, min(field.decimal_places, 4))
        if isinstance(value, (dict, list)):
            return "…" if value else "—"
        return str(value)

    rows = []
    for event in events:
        model = by_table.get(event.table_name)
        fields = {f.column: f for f in model._meta.concrete_fields} if model else {}
        changes = []
        for column, value in sorted(event.changes.items()):
            field = fields.get(column)
            label = COLUMNS.get(column) or (
                str(field.verbose_name).capitalize() if field is not None else column)
            if event.action == AuditAction.UPDATE and value == HIDDEN:
                changes.append({"label": label, "old": _("(hidden)"), "new": _("(hidden)")})
            elif event.action == AuditAction.UPDATE:
                changes.append({"label": label, "old": shown(field, value[0]),
                                "new": shown(field, value[1])})
            else:
                changes.append({"label": label, "new": shown(field, value)})
        name = labels.get(model, event.row_id) if model else None
        if name is None:  # deleted since: name it from what was recorded
            recorded = event.changes
            name = next((str(recorded[key]) for key in ("name", "display_name", "username",
                                                        "code", "barcode", "permission")
                         if isinstance(recorded.get(key), str)), f"#{event.row_id}")
        rows.append({"event": event, "what": TABLES.get(event.table_name, event.table_name),
                     "name": name, "changes": changes})
    return rows


@permission_required("admin.audit.history")
def activity(request):
    events = AuditEvent.objects.select_related("user")
    table = request.GET.get("table", "")
    row = request.GET.get("row", "")
    who = request.GET.get("user", "")
    date_from, date_to = _date(request.GET.get("from", "")), _date(request.GET.get("to", ""))
    if table in TABLES:
        events = events.filter(table_name=table)
    if row.isdigit():
        events = events.filter(row_id=int(row))
    if who.isdigit():
        events = events.filter(user_id=int(who))
    if date_from:
        events = events.filter(at__date__gte=date_from)
    if date_to:
        events = events.filter(at__date__lte=date_to)
    page = Paginator(events.order_by("-at", "-id"), 40).get_page(request.GET.get("page"))
    rows = _describe(list(page.object_list))
    record = None
    if table in TABLES and row.isdigit():
        model = _models_by_table()[table]
        obj = model._default_manager.filter(pk=int(row)).first()
        record = {"what": TABLES[table], "name": _name(obj) if obj else rows[0]["name"]
                  if rows else f"#{row}"}
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "audit/activity.html", {
        "page": page, "rows": rows, "record": record, "tables": sorted(
            TABLES.items(), key=lambda pair: str(pair[1])),
        "members": Membership.objects.select_related("user").order_by("username"),
        "filters": {"table": table, "row": row, "user": who, "from": date_from, "to": date_to},
        "query": query.urlencode(),
    })
