"""Console → Jobs: is a worker running, what is waiting, what failed (and run it again)."""

from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.platform.jobs.models import Job, JobStatus, Worker
from apps.platform.jobs.registry import TASKS
from apps.platform.jobs.worker import retry

from .views import record, staff_required

ALIVE_WITHIN = timedelta(seconds=60)
VIEWS = ("problems", "waiting", "done", "all")


@staff_required
def jobs(request):
    now = timezone.now()
    day = now - timedelta(hours=24)
    counts = Job.objects.aggregate(
        due=Count("id", filter=Q(status=JobStatus.QUEUED, run_at__lte=now, attempts=0)),
        retrying=Count("id", filter=Q(status=JobStatus.QUEUED, attempts__gt=0)),
        later=Count("id", filter=Q(status=JobStatus.QUEUED, run_at__gt=now, attempts=0)),
        running=Count("id", filter=Q(status=JobStatus.RUNNING)),
        done=Count("id", filter=Q(status=JobStatus.DONE, finished_at__gte=day)),
        failed=Count("id", filter=Q(status=JobStatus.FAILED, finished_at__gte=day)),
    )
    oldest = (Job.objects.filter(status=JobStatus.QUEUED, run_at__lte=now)
              .order_by("run_at").values_list("run_at", flat=True).first())
    workers = list(Worker.objects.order_by("-seen_at"))
    alive = [w for w in workers if now - w.seen_at <= ALIVE_WITHIN]

    view = request.GET.get("view", "problems")
    view = view if view in VIEWS else "problems"
    queryset = Job.objects.select_related("tenant")
    if view == "problems":
        queryset = queryset.filter(Q(status=JobStatus.FAILED)
                                   | Q(status=JobStatus.QUEUED, attempts__gt=0))
    elif view == "waiting":
        queryset = queryset.filter(status__in=(JobStatus.QUEUED, JobStatus.RUNNING))
    elif view == "done":
        queryset = queryset.filter(status__in=(JobStatus.DONE, JobStatus.CANCELLED))
    page = Paginator(queryset.order_by("-created_at", "-id"), 40).get_page(
        request.GET.get("page"))
    rows = [{"job": job, "label": TASKS[job.name].label if job.name in TASKS else job.name}
            for job in page.object_list]
    return render(request, "console/jobs.html", {
        "section": "jobs", "counts": counts, "oldest": oldest, "workers": workers,
        "alive": alive, "now": now, "view": view, "page": page, "rows": rows,
    })


def _job(pk) -> Job:
    job = Job.objects.select_related("tenant").filter(pk=pk).first()
    if job is None:
        raise Http404
    return job


def _back(request):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}):
        return redirect(target)
    return redirect("console-jobs")


@staff_required
@require_POST
def job_retry(request, pk):
    job = _job(pk)
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED):
        messages.error(request, _("Only a waiting, failed or cancelled job can be run again."))
    else:
        retry(job)
        record(request, "job.retried", job.tenant, job=f"{job.name} #{job.pk}")
        messages.success(request, _("The job will run again in a moment."))
    return _back(request)


@staff_required
@require_POST
def job_cancel(request, pk):
    job = _job(pk)
    if job.status != JobStatus.QUEUED:
        messages.error(request, _("Only a waiting job can be cancelled."))
    else:
        job.status, job.finished_at = JobStatus.CANCELLED, timezone.now()
        job.last_error = _("Cancelled from the console.")
        job.save(update_fields=["status", "finished_at", "last_error"])
        record(request, "job.cancelled", job.tenant, job=f"{job.name} #{job.pk}")
        messages.success(request, _("Job cancelled."))
    return _back(request)
