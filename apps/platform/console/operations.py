"""Console → Errors (server errors, grouped and counted) and Console → Backups (when the last
one ran, every run, back up now)."""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.platform.jobs.registry import enqueue
from apps.platform.monitor.backups import last_good
from apps.platform.monitor.models import BackupRun, ErrorEvent

from .views import record, staff_required

STALE_AFTER = timedelta(hours=26)  # a nightly backup, with some slack


@staff_required
def errors(request):
    view = request.GET.get("view", "open")
    view = view if view in ("open", "resolved", "all") else "open"
    found = ErrorEvent.objects.select_related("last_tenant")
    if view == "open":
        found = found.filter(resolved_at__isnull=True)
    elif view == "resolved":
        found = found.filter(resolved_at__isnull=False)
    day = timezone.now() - timedelta(hours=24)
    return render(request, "console/errors.html", {
        "section": "errors", "view": view,
        "page": Paginator(found.order_by("-last_seen"), 30).get_page(request.GET.get("page")),
        "open_count": ErrorEvent.objects.filter(resolved_at__isnull=True).count(),
        "today": ErrorEvent.objects.filter(last_seen__gte=day).count(),
        "alerts": bool(getattr(settings, "ERROR_ALERT_EMAILS", [])),
    })


@staff_required
@require_POST
def error_state(request, pk):
    event = ErrorEvent.objects.filter(pk=pk).first()
    if event is None:
        raise Http404
    resolve = request.POST.get("action") == "resolve"
    event.resolved_at = timezone.now() if resolve else None
    event.save(update_fields=["resolved_at"])
    record(request, "error.resolved" if resolve else "error.reopened", error=event.exc_type)
    view = request.POST.get("view", "open")
    return redirect(f"{reverse('console-errors')}?view={view if view.isalpha() else 'open'}")


@staff_required
def backups(request):
    good = last_good()
    now = timezone.now()
    runs = list(BackupRun.objects.order_by("-started_at")[:30])
    for run in runs:  # the newest BACKUP_KEEP files are kept; older ones were removed
        run.present = bool(run.database_file) and Path(run.database_file).exists()
    return render(request, "console/backups.html", {
        "section": "backups", "runs": runs, "good": good,
        "stale": good is None or now - good.started_at > STALE_AFTER,
        "configured": bool(settings.BACKUP_DATABASE_URL), "directory": settings.BACKUP_DIR,
        "keep": settings.BACKUP_KEEP,
    })


@staff_required
@require_POST
def backup_now(request):
    job = enqueue("monitor.backup", dedupe_key=f"backup-now-{timezone.now():%Y%m%d%H%M}")
    record(request, "backup.requested")
    if job is None:
        messages.info(request, _("A backup was already asked for this minute."))
    else:
        messages.success(request, _("Backup queued: the worker runs it in a moment."))
    return redirect("console-backups")


@staff_required
def whatsapp(request):
    """Console → WhatsApp (legacy WhatsappDashboard): the hosted service's account (messages
    sent, quota) and each client's messages this month and last, by route."""
    from apps.core.tenancy import tenant_context
    from apps.messaging import gateways, usage
    from apps.platform.tenants.features import is_enabled
    from apps.platform.tenants.models import Tenant

    account = gateways.hosted_usage()
    this_month, last_month = usage.month_bounds(), usage.month_bounds(back=1)
    rows, totals = [], {"this": 0, "last": 0, "api": 0, "own": 0, "failed": 0}
    for tenant in Tenant.objects.order_by("name"):
        with tenant_context(tenant.id):
            now, before = usage.usage(*this_month), usage.usage(*last_month)
            last = usage.last_sent()
        features = {key: is_enabled(tenant.id, key) for key in ("whatsapp_api", "whatsapp_own")}
        if not (now.total or before.total or now.failed or any(features.values())):
            continue
        rows.append({"tenant": tenant, "now": now, "before": before, "last": last, **features})
        totals["this"] += now.total
        totals["last"] += before.total
        totals["api"] += now.sent["api"]
        totals["own"] += now.sent["own"] + now.campaign
        totals["failed"] += now.failed
    quota, sent = account.data.get("quota"), account.data.get("sent")
    left = (quota - sent) if isinstance(quota, int) and isinstance(sent, int) else None
    return render(request, "console/whatsapp.html", {
        "section": "whatsapp", "account": account, "quota": quota, "sent": sent, "left": left,
        "used_pct": round(sent * 100 / quota) if left is not None and quota else None,
        "rows": rows, "totals": totals, "month": this_month[0], "previous": last_month[0],
    })
