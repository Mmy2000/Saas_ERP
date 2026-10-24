"""Nightly backup through the worker (apps.platform.jobs)."""

from datetime import time, timedelta

from django.utils.translation import gettext_lazy as _

from apps.platform.jobs.registry import daily, task


# Not atomic: a failed run's record must outlive the failure, and pg_dump can take long.
@task("monitor.backup", label=_("Back up the database and files"), per_tenant=False,
      max_attempts=2, atomic=False, stale_after=timedelta(hours=2))  # pg_dump: up to 1 h
def backup():
    from .backups import run_backup

    run_backup()


daily("monitor.backup", at=time(2, 30))
