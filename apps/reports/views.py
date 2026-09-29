import codecs
import csv
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render

from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile

from .registry import REPORTS


def _visible_reports(actor):
    return [report for report in REPORTS.values() if actor.can(report.permission())]


@permission_required("authenticated")
def index(request):
    reports = _visible_reports(request.actor)
    if not reports:
        raise PermissionDenied
    return render(request, "reports/index.html", {"reports": reports})


def _cell(value, places):
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if places is not None:
        return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    return str(value)


def _shape(table) -> dict:
    """Rows as lists of cells in column order, for the template."""
    def cells(row):
        return [{"value": row.get(c.key), "places": c.places, "kind": c.kind,
                 "negative": c.numeric and (row.get(c.key) or 0) < 0} for c in table.columns]
    return {"title": table.title, "note": table.note, "columns": table.columns,
            "rows": [cells(row) for row in table.rows],
            "totals": cells(table.totals) if table.totals else None}


def _csv(report, tables, params) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = "_".join(str(v) for v in params.values.values() if isinstance(v, date))
    response["Content-Disposition"] = f'attachment; filename="{report.code}_{stamp}.csv"'
    response.write(codecs.BOM_UTF8)  # BOM: Excel then reads the file as UTF-8 (Arabic names)
    writer = csv.writer(response)
    for table in tables:
        writer.writerow([table.title])
        writer.writerow([column.label for column in table.columns])
        for row in [*table.rows, *([table.totals] if table.totals else [])]:
            writer.writerow([_cell(row.get(c.key), c.places) for c in table.columns])
        writer.writerow([])
    return response


def run_report(request, code):
    report_class = REPORTS.get(code)
    if report_class is None:
        raise Http404
    permission = report_class.permission()

    @permission_required(permission)
    def view(request):
        report = report_class()
        scope = request.actor.branch_ids(permission)
        branches = Branch.objects.order_by("code")
        if scope is not None:
            branches = branches.filter(pk__in=scope)
        params = report.parse(request.GET, branches)
        chosen = params.get("branch")
        tables = report.run(params, [chosen.pk] if chosen else scope)
        if request.GET.get("format") == "csv":
            return _csv(report, tables, params)
        return render(request, "reports/report.html", {
            "report": report, "tables": [_shape(table) for table in tables],
            "params": params.values,
            "branches": branches, "profile": TenantProfile.objects.first(),
            "query": request.GET.urlencode(),
        })

    return view(request)
