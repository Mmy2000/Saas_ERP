"""Console → Errors (server errors grouped and counted) and backups (database dump + files)."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from django.core import mail
from django.test import Client, RequestFactory, override_settings

from apps.platform.monitor import backups, errors
from apps.platform.monitor.models import BackupRun, ErrorEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db):
    from apps.iam.models import User

    user = User.objects.create_user(email="ops@gweb.test", password="pw-123456789",
                                    is_platform_staff=True)
    client = Client(HTTP_HOST="admin.localhost")
    client.cookies["django_language"] = "en"
    client.force_login(user)
    return client


def boom():
    raise ValueError("price board missing")


def caught():
    try:
        boom()
    except ValueError:
        return sys.exc_info()


def test_errors_are_grouped_and_counted():
    request = RequestFactory().get("/sales/new/", HTTP_HOST="alpha.localhost")
    logger = logging.getLogger("django.request")
    for _ in range(3):
        logger.error("Internal Server Error", exc_info=caught(), extra={"request": request})
    event = ErrorEvent.objects.get()
    assert event.count == 3 and event.exc_type == "builtins.ValueError"
    assert event.message == "price board missing" and "in boom" in event.where
    assert event.last_path == "/sales/new/" and "ValueError" in event.traceback
    logger.warning("no exception here")  # not an error with a traceback: ignored
    assert ErrorEvent.objects.count() == 1


@override_settings(ERROR_ALERT_EMAILS=["ops@gweb.test"])
def test_alert_on_new_and_returning_errors(monkeypatch):
    sent = []
    monkeypatch.setattr(errors, "_alert", lambda event: sent.append(event.exc_type))
    errors.record_error(caught())
    errors.record_error(caught())  # the same one again: no new alert
    assert sent == ["builtins.ValueError"]
    ErrorEvent.objects.update(resolved_at="2026-01-01T00:00:00Z")
    errors.record_error(caught())  # came back after being resolved
    assert len(sent) == 2 and ErrorEvent.objects.get().resolved_at is None
    assert not mail.outbox  # sent from a thread in real life; patched here


def test_errors_page(staff):
    errors.record_error(caught())
    event = ErrorEvent.objects.get()
    page = staff.get("/errors/").content.decode()
    assert "price board missing" in page and "1 time" in page
    staff.post(f"/errors/{event.pk}/", {"action": "resolve", "view": "open"})
    event.refresh_from_db()
    assert event.resolved_at is not None
    assert "price board missing" not in staff.get("/errors/").content.decode()
    assert "price board missing" in staff.get("/errors/?view=resolved").content.decode()


def fake_pg_dump(command, **kwargs):
    target = Path(command[command.index("--file") + 1])
    target.write_bytes(b"PGDMP fake dump")
    assert kwargs["env"]["PGPASSWORD"] == "secret"
    assert command[command.index("--username") + 1] == "app_platform"
    return subprocess.CompletedProcess(command, 0, "", "")


def test_backup(tmp_path, monkeypatch):
    media = tmp_path / "media" / "tenants" / "1"
    media.mkdir(parents=True)
    (media / "logo.png").write_bytes(b"png")
    monkeypatch.setattr(backups.subprocess, "run", fake_pg_dump)
    with override_settings(BACKUP_DIR=tmp_path / "backups", MEDIA_ROOT=tmp_path / "media",
                           BACKUP_KEEP=1,
                           BACKUP_DATABASE_URL="postgres://app_platform:secret@db:5432/gweb"):
        run = backups.run_backup()
        assert run.ok and run.database_bytes == len(b"PGDMP fake dump") and run.media_bytes
        import zipfile

        assert zipfile.ZipFile(run.media_file).namelist() == ["tenants/1/logo.png"]
        (tmp_path / "backups" / "gweb-db-20000101-000000.dump").write_bytes(b"old")
        assert backups.prune(tmp_path / "backups", 1) == ["gweb-db-20000101-000000.dump"]


def test_backup_failures_are_recorded(tmp_path):
    with override_settings(BACKUP_DIR=tmp_path, BACKUP_DATABASE_URL=""):
        with pytest.raises(backups.BackupError):
            backups.run_backup()
    run = BackupRun.objects.get()
    assert not run.ok and "BACKUP_DATABASE_URL" in run.error and run.finished_at


def test_backups_page(staff, tmp_path):
    with override_settings(BACKUP_DATABASE_URL=""):
        page = staff.get("/backups/").content.decode()
    assert "Backups are not set up" in page
    asked = staff.post("/backups/now/")
    assert asked.status_code == 302
    from apps.platform.jobs.models import Job

    assert Job.objects.filter(name="monitor.backup").exists()
