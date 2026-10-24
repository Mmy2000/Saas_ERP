"""/readyz/ on the platform host: what an uptime monitor should watch (TD-03).

/healthz/ only says the web process answers. This also checks what fails silently otherwise:
the database, a live worker (no worker: no e-mail, WhatsApp, prices or backups) and a recent
good backup. 200 when all is well, 503 with the failing checks. It names checks, nothing else.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

WORKER_SEEN_WITHIN = timedelta(seconds=60)  # workers heartbeat every 15 s between jobs
BACKUP_STALE_AFTER = timedelta(hours=26)  # a nightly backup, with some slack


def _database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - any failure to reach the database is "down"
        return False


def worker_alive(now=None) -> bool:
    """A worker seen lately, or one busy with a job still within its time: a worker doesn't
    heartbeat while it runs a job (the nightly backup takes minutes)."""
    from apps.platform.jobs.models import Job, JobStatus, Worker
    from apps.platform.jobs.worker import longest_run, stale_after

    now = now or timezone.now()
    if Worker.objects.filter(seen_at__gte=now - WORKER_SEEN_WITHIN).exists():
        return True
    running = Job.objects.filter(status=JobStatus.RUNNING, locked_at__gte=now - longest_run())
    return any(job.locked_at >= now - stale_after(job.name)
               for job in running.only("name", "locked_at"))


def backup_fresh(now=None) -> bool:
    from .backups import last_good

    good = last_good()
    return good is not None and good.started_at >= (now or timezone.now()) - BACKUP_STALE_AFTER


def checks() -> dict[str, bool]:
    if not _database():
        return {"database": False}
    return {"database": True, "worker": worker_alive(), "backup": backup_fresh()}


def readyz(request):
    results = checks()
    ok = all(results.values())
    return JsonResponse({"status": "ok" if ok else "failing",
                         "checks": {name: "ok" if good else "failing"
                                    for name, good in results.items()}},
                        status=200 if ok else 503)
