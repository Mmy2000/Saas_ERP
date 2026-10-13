"""Nightly backup through the worker (apps.platform.jobs)."""

from datetime import time

from django.utils.translation import gettext_lazy as _

from apps.platform.jobs.registry import daily, task


@task("monitor.backup", label=_("Back up the database and files"), per_tenant=False,
      max_attempts=2)
def backup():
    from .backups import run_backup

    run_backup()


daily("monitor.backup", at=time(2, 30))
