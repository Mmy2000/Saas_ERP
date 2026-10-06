"""The platform's own jobs."""

from datetime import time

from django.utils.translation import gettext_lazy as _

from .registry import daily, task


@task("jobs.prune", label=_("Clean up old jobs"), per_tenant=False, max_attempts=2)
def prune_jobs():
    from .worker import prune

    prune()


daily("jobs.prune", at=time(3, 0))
