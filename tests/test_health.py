"""/readyz/ on the platform host: database, a live worker and a recent good backup."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.platform.jobs.models import Job, JobStatus, Worker
from apps.platform.monitor import health
from apps.platform.monitor.models import BackupRun

pytestmark = pytest.mark.django_db


def _ready():
    response = Client(HTTP_HOST="admin.localhost").get("/readyz/")
    return response.status_code, response.json()["checks"]


def test_no_worker_and_no_backup_is_failing():
    assert _ready() == (503, {"database": "ok", "worker": "failing", "backup": "failing"})


def test_a_live_worker_and_a_fresh_backup_are_ready():
    now = timezone.now()
    Worker.objects.create(name="host:1", started_at=now, seen_at=now)
    BackupRun.objects.create(started_at=now - timedelta(hours=3), ok=True)
    assert _ready() == (200, {"database": "ok", "worker": "ok", "backup": "ok"})
    BackupRun.objects.update(started_at=now - timedelta(hours=27))
    assert _ready()[1]["backup"] == "failing"


def test_a_worker_busy_with_a_long_job_is_alive():
    """No heartbeat while it runs a job: the nightly backup must not look like a dead worker,
    but a short job "running" for half an hour does (its worker died)."""
    now = timezone.now()
    Worker.objects.create(name="host:1", started_at=now, seen_at=now - timedelta(minutes=30))
    job = Job.objects.create(name="monitor.backup", status=JobStatus.RUNNING,
                             locked_at=now - timedelta(minutes=30), locked_by="host:1")
    assert health.worker_alive()
    Job.objects.filter(pk=job.pk).update(name="messaging.send_email")
    assert not health.worker_alive()


def test_the_database_down_is_failing(monkeypatch):
    monkeypatch.setattr(health, "_database", lambda: False)
    assert _ready() == (503, {"database": "failing"})
