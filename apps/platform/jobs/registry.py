"""Tasks and schedules. Each app declares them in its own `jobs.py`:

    from apps.platform.jobs.registry import daily, task

    @task("messaging.send_email", max_attempts=4, on_error=note_failure)
    def send_email(message_id): ...

    daily("messaging.daily_digest", at=time(7, 30))   # every client, at 07:30 their time
    monthly("messaging.monthly_pack", day=1, at=time(8, 0))   # on the 1st, 08:00 their time

and asks for one with `enqueue("messaging.send_email", message_id=5)`, inside the transaction
of the work that needs it. A client task runs in that client's tenant_context, language and
time zone; a platform task (`per_tenant=False`) runs with no tenant, in one transaction unless
it says `atomic=False` (a backup: what it records must survive its own failure, and no
transaction should stay open while pg_dump runs).
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
    atomic: bool = True  # platform tasks only: False runs it without a wrapping transaction
    # How long a run may take before it counts as abandoned by a dead worker and is queued
    # again (None: worker.STALE_AFTER). Set it above the task's longest run, or a second
    # worker starts it again while the first is still at it.
    stale_after: timedelta | None = None


@dataclass(frozen=True)
class Schedule:
    task: str
    at: time  # local time: the client's time zone, or the server's for platform tasks
    until: time  # missed by then (no worker running): skipped for that day
    monthday: int | None = None  # only on this day of the month (None: every day)
    every_minutes: int | None = None  # a repeating task instead of a daily one


TASKS: dict[str, Task] = {}
SCHEDULES: list[Schedule] = []


def task(name: str, *, label="", max_attempts: int = 5, per_tenant: bool = True,
         on_error: Callable | None = None, atomic: bool = True,
         stale_after: timedelta | None = None):
    if per_tenant and not atomic:
        raise ValueError(f"Task {name!r}: a client task runs in tenant_context, a transaction.")

    def register(func):
        if name in TASKS and TASKS[name].func is not func:
            raise RuntimeError(f"Task {name!r} is registered twice.")
        TASKS[name] = Task(name, func, max_attempts, per_tenant, on_error, label or name, atomic,
                           stale_after)
        return func
    return register


def daily(task_name: str, *, at: time, until: time | None = None) -> None:
    if until is None:  # six hours to catch up (a worker started late), not past midnight
        until = time(min(at.hour + 6, 23), at.minute if at.hour + 6 <= 23 else 59)
    if not any(s.task == task_name for s in SCHEDULES):
        SCHEDULES.append(Schedule(task_name, at, until))


def every(task_name: str, *, minutes: int) -> None:
    """Every `minutes` around the clock, once per interval even with several workers (e.g. the
    market prices poll). Meant for platform tasks: a client task would run for every client."""
    if minutes < 1:
        raise ValueError("At least one minute between runs.")
    if not any(s.task == task_name for s in SCHEDULES):
        SCHEDULES.append(Schedule(task_name, time(0, 0), time(23, 59), None, minutes))


def monthly(task_name: str, *, day: int, at: time, until: time | None = None) -> None:
    """Once a month, on `day` (1-28) at `at` in the client's time."""
    if not 1 <= day <= 28:
        raise ValueError("A monthly schedule runs on day 1 to 28 (every month has them).")
    daily(task_name, at=at, until=until)
    index = next(i for i, s in enumerate(SCHEDULES) if s.task == task_name)
    SCHEDULES[index] = Schedule(task_name, SCHEDULES[index].at, SCHEDULES[index].until, day)


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
