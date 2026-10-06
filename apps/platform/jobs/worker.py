"""Running jobs: claim one due row (FOR UPDATE SKIP LOCKED), run it, record the outcome.

A client's job runs inside its tenant_context, so RLS applies exactly as in a request, with
the client's language and time zone switched on. A failed attempt is retried later (30 s, 2 min,
8 min, 32 min, ~2 h) until the task's max_attempts; a job left "running" by a worker that died
is put back in the queue after STALE_AFTER.
"""

from __future__ import annotations

import logging
import os
import socket
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone, translation

from apps.core.tenancy.context import tenant_context

from . import registry
from .models import Job, JobStatus, Worker
from .registry import TASKS, JobFailed, Task, enqueue

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=15)
FIRST_RETRY = timedelta(seconds=30)
MAX_RETRY = timedelta(hours=6)


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim(name: str) -> Job | None:
    """The next due job, marked running by `name` (committed before it runs)."""
    with transaction.atomic():
        job = (Job.objects.select_for_update(skip_locked=True)
               .filter(status=JobStatus.QUEUED, run_at__lte=timezone.now())
               .order_by("run_at", "id").first())
        if job is None:
            return None
        job.status, job.locked_by, job.locked_at = JobStatus.RUNNING, name, timezone.now()
        job.attempts += 1
        job.save(update_fields=["status", "locked_by", "locked_at", "attempts"])
    return job


@contextmanager
def _client(tenant_id: int | None):
    """The client's tenant_context with its language and time zone (or, for a platform job,
    a plain transaction)."""
    with ExitStack() as stack:
        if tenant_id is None:
            stack.enter_context(transaction.atomic())
        else:
            from apps.org.models import TenantProfile

            stack.enter_context(tenant_context(tenant_id))
            locale, zone = (TenantProfile.objects.values_list("locale", "timezone").first()
                            or (settings.LANGUAGE_CODE, settings.TIME_ZONE))
            stack.enter_context(translation.override(locale or settings.LANGUAGE_CODE))
            stack.enter_context(timezone.override(_zone(zone)))
        yield


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)


def _active(tenant_id: int | None) -> bool:
    if tenant_id is None:
        return True
    from apps.platform.tenants.models import Tenant, TenantStatus

    return Tenant.objects.filter(pk=tenant_id, status=TenantStatus.ACTIVE).exists()


def execute(job: Job) -> bool:
    """Run a claimed job. True when it succeeded."""
    spec = TASKS.get(job.name)
    if not _active(job.tenant_id):
        _finish(job, JobStatus.CANCELLED, "The client is not active.")
        return False
    try:
        if spec is None:
            raise JobFailed(f"Unknown task {job.name!r}.")
        with _client(job.tenant_id):
            spec.func(**job.payload)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        _failed(job, spec, exc)
        return False
    _finish(job, JobStatus.DONE, "")
    return True


def _finish(job: Job, status: str, error: str) -> None:
    job.status, job.last_error, job.finished_at = status, error, timezone.now()
    job.locked_by, job.locked_at = "", None
    job.save(update_fields=["status", "last_error", "finished_at", "locked_by", "locked_at"])


def retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY * 4 ** max(0, attempts - 1), MAX_RETRY)


def _failed(job: Job, spec: Task | None, exc: Exception) -> None:
    error = f"{type(exc).__name__}: {exc}"[:2000]
    final = isinstance(exc, JobFailed) or job.attempts >= job.max_attempts
    if not isinstance(exc, JobFailed):
        logger.warning("job %s #%s attempt %s failed: %s", job.name, job.pk, job.attempts, error,
                       exc_info=exc)
    if spec is not None and spec.on_error is not None:
        try:
            with _client(job.tenant_id):
                spec.on_error(job.payload, error, final)
        except Exception:  # noqa: BLE001 - the job's own outcome is still recorded
            logger.exception("on_error of %s failed", job.name)
    if final:
        _finish(job, JobStatus.FAILED, error)
        return
    job.status, job.last_error = JobStatus.QUEUED, error
    job.run_at = timezone.now() + retry_delay(job.attempts)
    job.locked_by, job.locked_at = "", None
    job.save(update_fields=["status", "last_error", "run_at", "locked_by", "locked_at"])


def run_pending(limit: int = 1000, name: str = "inline") -> int:
    """Run the due jobs now, one after another (tests, `run_worker --once`). Returns how many
    ran."""
    count = 0
    while count < limit:
        job = claim(name)
        if job is None:
            break
        execute(job)
        count += 1
    return count


def release_stale(now=None) -> int:
    """Jobs left running by a worker that died go back in the queue (the attempt counts)."""
    now = now or timezone.now()
    return Job.objects.filter(status=JobStatus.RUNNING, locked_at__lt=now - STALE_AFTER).update(
        status=JobStatus.QUEUED, locked_by="", locked_at=None, run_at=timezone.now(),
        last_error="The worker stopped while running it.")


def retry(job: Job) -> None:
    """Run a failed or cancelled job again (console)."""
    job.status, job.run_at, job.finished_at = JobStatus.QUEUED, timezone.now(), None
    job.max_attempts = max(job.max_attempts, job.attempts + 1)
    job.save(update_fields=["status", "run_at", "finished_at", "max_attempts"])


def heartbeat(name: str, started_at, *, done: int = 0, failed: int = 0) -> None:
    now = timezone.now()
    updated = Worker.objects.filter(name=name).update(
        seen_at=now, jobs_done=F("jobs_done") + done, jobs_failed=F("jobs_failed") + failed)
    if not updated:
        Worker.objects.create(name=name, started_at=started_at, seen_at=now, jobs_done=done,
                              jobs_failed=failed)


class Scheduler:
    """Queues the daily tasks: for every active client at the schedule's time in the client's
    own time zone (TenantProfile.timezone), once per local day. Several workers may tick: the
    dedupe key (the local date) keeps one job."""

    ZONES_EVERY = timedelta(hours=1)

    def __init__(self):
        self._done: set = set()
        self._zones: dict[int, str] = {}
        self._zones_at = None

    def _tenant_zones(self, now) -> dict[int, str]:
        if self._zones_at is None or now - self._zones_at > self.ZONES_EVERY:
            from apps.org.models import TenantProfile
            from apps.platform.tenants.models import Tenant, TenantStatus

            zones = {}
            for tenant_id in Tenant.objects.filter(status=TenantStatus.ACTIVE).values_list(
                    "pk", flat=True):
                with tenant_context(tenant_id):
                    zones[tenant_id] = (TenantProfile.objects.values_list("timezone", flat=True)
                                        .first() or settings.TIME_ZONE)
            self._zones, self._zones_at = zones, now
        return self._zones

    def tick(self, now=None) -> int:
        """Queue whatever is due. Returns how many jobs were queued."""
        now = now or timezone.now()
        if not registry.SCHEDULES:
            return 0
        queued = 0
        for schedule in registry.SCHEDULES:
            spec = TASKS.get(schedule.task)
            if spec is None:
                continue
            targets = (self._tenant_zones(now).items() if spec.per_tenant
                       else [(None, settings.TIME_ZONE)])
            for tenant_id, zone in targets:
                local = now.astimezone(_zone(zone))
                key = (schedule.task, tenant_id, local.date())
                if key in self._done or not schedule.at <= local.time() < schedule.until:
                    continue
                if enqueue(schedule.task, tenant_id=tenant_id,
                           dedupe_key=local.date().isoformat()) is not None:
                    queued += 1
                self._done.add(key)
        return queued


def prune(now=None) -> dict:
    """Old finished jobs and long-gone workers."""
    now = now or timezone.now()
    done = Job.objects.filter(status__in=(JobStatus.DONE, JobStatus.CANCELLED),
                              finished_at__lt=now - timedelta(days=14)).delete()[0]
    failed = Job.objects.filter(status=JobStatus.FAILED,
                                finished_at__lt=now - timedelta(days=60)).delete()[0]
    workers = Worker.objects.filter(seen_at__lt=now - timedelta(days=1)).delete()[0]
    return {"done": done, "failed": failed, "workers": workers}
