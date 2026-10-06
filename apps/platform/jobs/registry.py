"""Tasks and schedules. Each app declares them in its own `jobs.py`:

    from apps.platform.jobs.registry import daily, task

    @task("messaging.send_email", max_attempts=4, on_error=note_failure)
    def send_email(message_id): ...

    daily("messaging.daily_digest", at=time(7, 30))   # every client, at 07:30 their time

and asks for one with `enqueue("messaging.send_email", message_id=5)`, inside the transaction
of the work that needs it. A client task runs in that client's tenant_context, language and
time zone; a platform task (`per_tenant=False`) runs with no tenant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.tenancy.context import get_current_tenant_id


class JobFailed(Exception):
    """Raise from a task to fail now, without retrying (nothing will change on a retry)."""


@dataclass(frozen=True)
class Task:
    name: str
    func: Callable
    max_attempts: int = 5
    per_tenant: bool = True
    # (payload, error text, final?) after a failed attempt, in a fresh transaction (and the
    # client's tenant_context): e.g. mark the message "failed" so the shop can see it.
    on_error: Callable | None = None
    label: object = ""  # shown in the console


@dataclass(frozen=True)
class Schedule:
    task: str
    at: time  # local time: the client's time zone, or the server's for platform tasks
    until: time  # missed by then (no worker running): skipped for that day


TASKS: dict[str, Task] = {}
SCHEDULES: list[Schedule] = []


def task(name: str, *, label="", max_attempts: int = 5, per_tenant: bool = True,
         on_error: Callable | None = None):
    def register(func):
        if name in TASKS and TASKS[name].func is not func:
            raise RuntimeError(f"Task {name!r} is registered twice.")
        TASKS[name] = Task(name, func, max_attempts, per_tenant, on_error, label or name)
        return func
    return register


def daily(task_name: str, *, at: time, until: time | None = None) -> None:
    if until is None:  # six hours to catch up (a worker started late), not past midnight
        until = time(min(at.hour + 6, 23), at.minute if at.hour + 6 <= 23 else 59)
    if not any(s.task == task_name for s in SCHEDULES):
        SCHEDULES.append(Schedule(task_name, at, until))


def enqueue(name: str, *, tenant_id: int | None = None, run_at: datetime | None = None,
            delay: timedelta | None = None, dedupe_key: str = "", **payload):
    """Queue `name` with `payload` (JSON values: ids, not personal data). A client task takes
    the current tenant unless `tenant_id` is given. Returns the Job, or None when one with the
    same dedupe key already exists."""
    from .models import Job

    spec = TASKS.get(name)
    if spec is None:
        raise KeyError(f"Unknown task {name!r}: is it registered in a jobs.py?")
    if spec.per_tenant:
        tenant_id = tenant_id or get_current_tenant_id()
        if tenant_id is None:
            raise ValueError(f"Task {name!r} runs for a client: no tenant given or in context.")
    else:
        tenant_id = None
    when = run_at or (timezone.now() + delay if delay else timezone.now())
    job = Job(tenant_id=tenant_id, name=name, payload=payload, run_at=when,
              max_attempts=spec.max_attempts, dedupe_key=dedupe_key)
    if not dedupe_key:
        job.save()
        return job
    try:
        with transaction.atomic():  # a savepoint: a duplicate must not spoil the caller's work
            job.save()
    except IntegrityError:
        return None
    return job
